from __future__ import annotations

import copy
import errno
import os
import pickle
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from specpilot.manifests.corpus_store import (
    CollectionFreezeLease,
    CollectionFrozenError,
    CollectionLeaseError,
    CollectionLeaseUnavailableError,
    CollectionWriteLease,
    CorpusManifestStore,
    UnsupportedCorpusManifestVersionError,
)
from tests.helpers.corpus_manifest_factory import corpus_draft, corpus_intent

KERNEL_CONTENDED_EXIT = 74
CONTENTION_MISSED_EXIT = 75
PROBE_FAILURE_EXIT = 76
WRITER_PROBE = f"""
import sys
from pathlib import Path

from specpilot.manifests.corpus_store import (
    CollectionLeaseUnavailableError,
    CorpusManifestStore,
)

store = CorpusManifestStore(Path(sys.argv[1]))
try:
    uncontended = store.acquire_write_lease(sys.argv[2], blocking=False)
except CollectionLeaseUnavailableError:
    raise SystemExit({KERNEL_CONTENDED_EXIT})
except BaseException:
    raise SystemExit({PROBE_FAILURE_EXIT})
else:
    uncontended.close()
    raise SystemExit({CONTENTION_MISSED_EXIT})
"""


def _wait_or_terminate(process: subprocess.Popen[bytes], timeout: float = 2) -> int:
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        raise


def _create_manifest(store: CorpusManifestStore) -> None:
    draft = corpus_draft()
    with store.acquire_freeze_lease(draft.collection_name) as lease:
        store.create(draft, lease=lease)


def _assert_closed(descriptor: int) -> None:
    with pytest.raises(OSError) as raised:
        os.fstat(descriptor)
    assert raised.value.errno == errno.EBADF


def _ignore_subclass_guard(cls: type[object], **kwargs: object) -> None:
    del cls, kwargs


def _ignore_lease_validation(*args: object, **kwargs: object) -> None:
    del args, kwargs


def test_shared_writers_coexist(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with (
        store.acquire_write_lease(collection, blocking=False) as first,
        store.acquire_write_lease(collection, blocking=False) as second,
    ):
        assert first.root_fd >= 0
        assert second.root_fd >= 0


def test_freeze_is_unavailable_while_a_writer_lives(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with (
        store.acquire_write_lease(collection),
        pytest.raises(CollectionLeaseUnavailableError),
    ):
        store.acquire_freeze_lease(collection, blocking=False)


def test_writer_is_unavailable_while_freeze_lives(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with (
        store.acquire_freeze_lease(collection),
        pytest.raises(CollectionLeaseUnavailableError),
    ):
        store.acquire_write_lease(collection, blocking=False)


def test_writer_is_refused_after_collection_is_frozen(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")

    _create_manifest(store)

    with pytest.raises(CollectionFrozenError):
        store.acquire_write_lease(corpus_intent().collection_name)


def test_subprocess_nonblocking_writer_observes_real_freeze_contention(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name
    probe: subprocess.Popen[bytes] | None = None
    try:
        with store.acquire_freeze_lease(collection):
            probe = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    WRITER_PROBE,
                    str(root),
                    collection,
                ]
            )
            assert _wait_or_terminate(probe) == KERNEL_CONTENDED_EXIT
            probe = None
    finally:
        if probe is not None and probe.poll() is None:
            probe.terminate()
            _wait_or_terminate(probe, timeout=1)


def test_waiting_writer_rescans_registry_after_controlled_flock_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import corpus_store as corpus_store_module

    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    draft = corpus_draft()
    collection = draft.collection_name
    freeze = store.acquire_freeze_lease(collection)
    original_flock = corpus_store_module.fcntl.flock
    original_read_all_under = CorpusManifestStore._read_all_under  # noqa: SLF001
    blocking_entered = threading.Event()
    allow_lock_return = threading.Event()
    writer_done = threading.Event()
    writer_thread_id: list[int] = []
    events: list[str] = []
    events_lock = threading.Lock()
    writer_errors: list[BaseException] = []

    def record(event: str) -> None:
        with events_lock:
            events.append(event)

    def controlled_flock(descriptor: int, operation: int) -> None:
        if (
            writer_thread_id
            and threading.get_ident() == writer_thread_id[0]
            and operation == corpus_store_module.fcntl.LOCK_SH
        ):
            record("blocking-entered")
            blocking_entered.set()
            if not allow_lock_return.wait(timeout=2):
                raise TimeoutError("flock-return test barrier timed out")
            original_flock(descriptor, operation)
            record("lock-return")
            return
        original_flock(descriptor, operation)

    def recording_read_all_under(
        self: CorpusManifestStore,
        lease: CollectionWriteLease | CollectionFreezeLease,
    ) -> tuple[object, ...]:
        if writer_thread_id and threading.get_ident() == writer_thread_id[0]:
            record("registry-scan")
        return original_read_all_under(self, lease)

    def acquire_waiting_writer() -> None:
        writer_thread_id.append(threading.get_ident())
        try:
            with store.acquire_write_lease(collection):
                raise AssertionError("frozen writer unexpectedly acquired a lease")
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_done.set()

    monkeypatch.setattr(corpus_store_module.fcntl, "flock", controlled_flock)
    monkeypatch.setattr(
        CorpusManifestStore,
        "_read_all_under",
        recording_read_all_under,
    )
    writer = threading.Thread(target=acquire_waiting_writer)
    writer.start()
    try:
        assert blocking_entered.wait(timeout=1)
        assert not writer_done.wait(timeout=0.1)
        store.create(draft, lease=freeze)
        freeze.close()
        allow_lock_return.set()
        writer.join(timeout=2)
    finally:
        freeze.close()
        allow_lock_return.set()
        writer.join(timeout=2)

    assert not writer.is_alive()
    assert len(writer_errors) == 1
    assert isinstance(writer_errors[0], CollectionFrozenError)
    assert events == ["blocking-entered", "lock-return", "registry-scan"]


def test_close_waits_for_manifest_publication_before_releasing_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests._secure_records import SecureRecordDirectory

    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    draft = corpus_draft()
    freeze = store.acquire_freeze_lease(draft.collection_name)
    publication_entered = threading.Event()
    allow_publication = threading.Event()
    fsync_entered = threading.Event()
    allow_fsync = threading.Event()
    close_finished = threading.Event()
    original_publish = SecureRecordDirectory.publish
    original_fsync = os.fsync
    root_fd = freeze._root_fd  # noqa: SLF001
    creator_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def paused_publish(
        self: SecureRecordDirectory,
        name: str,
        data: bytes,
        *,
        max_bytes: int,
    ) -> bytes:
        publication_entered.set()
        if not allow_publication.wait(timeout=2):
            raise TimeoutError("publication test barrier timed out")
        return original_publish(self, name, data, max_bytes=max_bytes)

    def paused_root_fsync(descriptor: int) -> None:
        if descriptor == root_fd:
            fsync_entered.set()
            if not allow_fsync.wait(timeout=2):
                raise TimeoutError("fsync test barrier timed out")
        original_fsync(descriptor)

    def create_manifest() -> None:
        try:
            store.create(draft, lease=freeze)
        except BaseException as error:
            creator_errors.append(error)

    def close_freeze() -> None:
        try:
            freeze.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            close_finished.set()

    monkeypatch.setattr(SecureRecordDirectory, "publish", paused_publish)
    monkeypatch.setattr(os, "fsync", paused_root_fsync)
    creator = threading.Thread(target=create_manifest)
    closer = threading.Thread(target=close_freeze)
    creator.start()
    assert publication_entered.wait(timeout=1)
    closer.start()
    with freeze._state.condition:  # noqa: SLF001
        close_started = freeze._state.condition.wait_for(  # noqa: SLF001
            lambda: freeze._state.closing,  # noqa: SLF001
            timeout=1,
        )
    close_returned_before_publication = close_finished.is_set()
    writer_entered_before_publication = False
    writer_entered_before_fsync = False
    writers: list[CollectionWriteLease] = []
    try:
        try:
            writers.append(
                store.acquire_write_lease(draft.collection_name, blocking=False)
            )
            writer_entered_before_publication = True
        except CollectionLeaseUnavailableError:
            pass
        allow_publication.set()
        assert fsync_entered.wait(timeout=1)
        close_returned_before_fsync = close_finished.is_set()
        try:
            writers.append(
                store.acquire_write_lease(draft.collection_name, blocking=False)
            )
            writer_entered_before_fsync = True
        except CollectionLeaseUnavailableError:
            pass
    finally:
        for writer in writers:
            writer.close()
        allow_publication.set()
        allow_fsync.set()
        creator.join(timeout=2)
        closer.join(timeout=2)

    assert not creator.is_alive()
    assert not closer.is_alive()
    assert close_started
    assert not close_returned_before_publication
    assert not close_returned_before_fsync
    assert not writer_entered_before_publication
    assert not writer_entered_before_fsync
    assert creator_errors == []
    assert close_errors == []
    with pytest.raises(CollectionFrozenError):
        store.acquire_write_lease(draft.collection_name)


def test_close_inside_an_active_operation_fails_without_deadlocking(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name
    lease = store.acquire_write_lease(collection)

    with lease.active_operation(collection):
        with pytest.raises(CollectionLeaseError, match="active operation"):
            lease.close()
        lease.require_active_for(collection)

    lease.close()


def test_lock_files_are_permanent(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name

    with store.acquire_write_lease(collection):
        lock_files = tuple((root / ".locks").iterdir())

    assert len(lock_files) == 1
    inode = lock_files[0].stat().st_ino
    with store.acquire_freeze_lease(collection):
        assert lock_files[0].stat().st_ino == inode
    assert lock_files[0].stat().st_ino == inode


def test_writer_object_is_invalid_after_context_exit(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with store.acquire_write_lease(collection) as lease:
        root_fd = lease.root_fd

    with pytest.raises(CollectionLeaseError):
        lease.require_active_for(collection)
    with pytest.raises(CollectionLeaseError):
        _ = lease.root_fd
    _assert_closed(root_fd)


def test_live_writer_rejects_a_replaced_store_root(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    moved_root = tmp_path / "original-corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name

    with store.acquire_write_lease(collection) as writer:
        root.rename(moved_root)
        root.mkdir(mode=0o700)
        with (
            CorpusManifestStore(root).acquire_freeze_lease(
                collection,
                blocking=False,
            ),
            pytest.raises(CollectionLeaseError, match="namespace"),
        ):
            writer.require_active_for(collection)


def test_live_writer_rejects_a_replaced_locks_directory(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name

    with store.acquire_write_lease(collection) as writer:
        locks = root / ".locks"
        locks.rename(root / ".original-locks")
        locks.mkdir(mode=0o700)
        with (
            CorpusManifestStore(root).acquire_freeze_lease(
                collection,
                blocking=False,
            ),
            pytest.raises(CollectionLeaseError, match="namespace"),
        ):
            writer.require_active_for(collection)


def test_live_writer_rejects_a_replaced_named_lock_file(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name

    with store.acquire_write_lease(collection) as writer:
        lock_file = next((root / ".locks").iterdir())
        lock_file.unlink()
        descriptor = os.open(
            lock_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.close(descriptor)
        with (
            CorpusManifestStore(root).acquire_freeze_lease(
                collection,
                blocking=False,
            ),
            pytest.raises(CollectionLeaseError, match="namespace"),
        ):
            writer.require_active_for(collection)


def test_namespace_validation_preserves_control_flow_exceptions_and_closes_temp_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import corpus_store as corpus_store_module

    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name
    lease = store.acquire_write_lease(collection)
    original_fstat = corpus_store_module.os.fstat
    original_revalidate = corpus_store_module.revalidate_directory_path
    opened_locks_fds: list[int] = []
    root_revalidated = False

    def recording_revalidate(path: Path, descriptor: int) -> None:
        nonlocal root_revalidated
        original_revalidate(path, descriptor)
        root_revalidated = True

    def interrupting_fstat(descriptor: int) -> os.stat_result:
        if root_revalidated and descriptor not in {
            lease._root_fd,  # noqa: SLF001
            lease._lock_fd,  # noqa: SLF001
        }:
            opened_locks_fds.append(descriptor)
            raise KeyboardInterrupt
        return original_fstat(descriptor)

    monkeypatch.setattr(
        corpus_store_module,
        "revalidate_directory_path",
        recording_revalidate,
    )
    monkeypatch.setattr(corpus_store_module.os, "fstat", interrupting_fstat)

    with pytest.raises(KeyboardInterrupt):
        lease.require_active_for(collection)

    assert len(opened_locks_fds) == 1
    with pytest.raises(OSError) as raised:
        original_fstat(opened_locks_fds[0])
    assert raised.value.errno == errno.EBADF
    lease.close()


def test_concrete_leases_cannot_be_publicly_constructed() -> None:
    with pytest.raises(TypeError):
        CollectionWriteLease()
    with pytest.raises(TypeError):
        CollectionFreezeLease()


def test_uninitialized_forged_lease_fails_with_domain_error(tmp_path: Path) -> None:
    forged = object.__new__(CollectionFreezeLease)
    store = CorpusManifestStore(tmp_path / "corpus")

    with pytest.raises(CollectionLeaseError):
        store.create(corpus_draft(), lease=forged)


def test_freeze_lease_subclass_cannot_publish_while_a_real_writer_lives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    draft = corpus_draft()

    with store.acquire_write_lease(draft.collection_name) as writer:
        with monkeypatch.context() as patch:
            patch.setattr(
                CollectionFreezeLease,
                "__init_subclass__",
                classmethod(_ignore_subclass_guard),
                raising=False,
            )
            forged_type = type(
                "ForgedFreezeLease",
                (CollectionFreezeLease,),
                {
                    "require_active_for": _ignore_lease_validation,
                    "require_owned": _ignore_lease_validation,
                },
            )
        forged = object.__new__(forged_type)
        object.__setattr__(forged, "_namespace", writer._namespace)  # noqa: SLF001
        object.__setattr__(forged, "_root_fd", writer.root_fd)

        with pytest.raises(CollectionLeaseError):
            store.create(draft, lease=forged)  # type: ignore[arg-type]

    assert not tuple(item for item in root.iterdir() if item.suffix == ".json")


def test_write_lease_subclass_cannot_enter_store_lease_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with store.acquire_write_lease(collection) as writer:
        with monkeypatch.context() as patch:
            patch.setattr(
                CollectionWriteLease,
                "__init_subclass__",
                classmethod(_ignore_subclass_guard),
                raising=False,
            )
            forged_type = type(
                "ForgedWriteLease",
                (CollectionWriteLease,),
                {
                    "require_active_for": _ignore_lease_validation,
                    "require_owned": _ignore_lease_validation,
                },
            )
        forged = object.__new__(forged_type)
        object.__setattr__(forged, "_namespace", writer._namespace)  # noqa: SLF001
        object.__setattr__(forged, "_root_fd", writer.root_fd)

        with pytest.raises(CollectionLeaseError):
            store._read_all_under(forged)  # type: ignore[arg-type]  # noqa: SLF001


@pytest.mark.parametrize("lease_type", [CollectionWriteLease, CollectionFreezeLease])
def test_concrete_lease_types_are_runtime_final(lease_type: type[object]) -> None:
    with pytest.raises(TypeError, match="cannot be subclassed"):
        type("ForgedLease", (lease_type,), {})


def test_private_lease_factory_rejects_an_untrusted_issuer() -> None:
    with pytest.raises(CollectionLeaseError, match="issuer"):
        CollectionFreezeLease._issue(  # noqa: SLF001
            issue_token=object(),
            directory=Path("corpus"),
            collection_name=corpus_intent().collection_name,
            owner_token=object(),
            root_fd=-1,
            lock_fd=-1,
            lock_name=f"{'0' * 64}.lock",
            locks_identity=(0, 0),
            lock_identity=(0, 0),
            exclusive=True,
        )


@pytest.mark.parametrize(
    "clone",
    [copy.copy, copy.deepcopy, pickle.dumps],
    ids=["shallow-copy", "deep-copy", "pickle"],
)
def test_live_lease_cannot_be_copied_or_serialized(
    tmp_path: Path,
    clone: object,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")

    with (
        store.acquire_write_lease(corpus_intent().collection_name) as lease,
        pytest.raises(TypeError, match="cannot be copied or serialized"),
    ):
        clone(lease)  # type: ignore[operator]


def test_lease_validates_owner_collection_and_mode(tmp_path: Path) -> None:
    first = CorpusManifestStore(tmp_path / "first")
    second = CorpusManifestStore(tmp_path / "second")
    collection = corpus_intent().collection_name

    with first.acquire_write_lease(collection) as lease:
        with pytest.raises(CollectionLeaseError):
            lease.require_owned(
                owner_token=second._owner_token,  # noqa: SLF001
                collection_name=collection,
            )
        with pytest.raises(CollectionLeaseError):
            lease.require_owned(
                owner_token=None,
                collection_name=collection,
            )
        with pytest.raises(CollectionLeaseError):
            lease.require_active_for("another_collection")
        with pytest.raises(CollectionLeaseError):
            lease.require_owned(
                owner_token=first._owner_token,  # noqa: SLF001
                collection_name=collection,
                exclusive=True,
            )


def test_root_fd_is_read_only(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with (
        store.acquire_write_lease(collection) as lease,
        pytest.raises(AttributeError),
    ):
        lease.root_fd = -1  # type: ignore[misc]


def test_collection_name_is_read_only(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name

    with (
        store.acquire_write_lease(collection) as lease,
        pytest.raises(AttributeError),
    ):
        lease.collection_name = "another_collection"  # type: ignore[misc]


def test_close_is_idempotent_and_does_not_close_a_reused_fd(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    lease = store.acquire_write_lease(corpus_intent().collection_name)
    lock_fd = lease._lock_fd  # noqa: SLF001
    root_fd = lease.root_fd
    lease.close()
    _assert_closed(lock_fd)
    _assert_closed(root_fd)

    reused = os.open(os.devnull, os.O_RDONLY)
    try:
        lease.close()
        os.fstat(reused)
    finally:
        os.close(reused)


def test_concurrent_close_retires_descriptors_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import corpus_store as corpus_store_module

    store = CorpusManifestStore(tmp_path / "corpus")
    lease = store.acquire_write_lease(corpus_intent().collection_name)
    lock_fd = lease._lock_fd  # noqa: SLF001
    root_fd = lease.root_fd
    state = lease._state  # noqa: SLF001
    original_close = corpus_store_module.os.close
    original_flock = corpus_store_module.fcntl.flock
    call_lock = threading.Lock()
    close_calls = {lock_fd: 0, root_fd: 0}
    unlock_calls = 0
    active_entered = threading.Event()
    allow_active_exit = threading.Event()
    active_errors: list[BaseException] = []

    def hold_active_operation() -> None:
        try:
            with lease.active_operation(corpus_intent().collection_name):
                active_entered.set()
                if not allow_active_exit.wait(timeout=2):
                    raise TimeoutError("active-operation test barrier timed out")
        except BaseException as error:
            active_errors.append(error)

    holder = threading.Thread(target=hold_active_operation)
    holder.start()
    assert active_entered.wait(timeout=1)

    def controlled_close(descriptor: int) -> None:
        if descriptor not in close_calls:
            original_close(descriptor)
            return
        with call_lock:
            close_calls[descriptor] += 1
            first = close_calls[descriptor] == 1
        if first:
            original_close(descriptor)

    def controlled_flock(descriptor: int, operation: int) -> None:
        nonlocal unlock_calls
        if descriptor != lock_fd or operation != corpus_store_module.fcntl.LOCK_UN:
            original_flock(descriptor, operation)
            return
        with call_lock:
            unlock_calls += 1
            first = unlock_calls == 1
        if first:
            original_flock(descriptor, operation)

    monkeypatch.setattr(corpus_store_module.os, "close", controlled_close)
    monkeypatch.setattr(corpus_store_module.fcntl, "flock", controlled_flock)
    errors: list[BaseException] = []

    def close_lease() -> None:
        try:
            lease.close()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=close_lease) for _ in range(2)]
    for thread in threads:
        thread.start()

    with state.condition:
        closing_started = state.condition.wait_for(lambda: state.closing, timeout=1)
    assert closing_started
    assert all(thread.is_alive() for thread in threads)
    assert unlock_calls == 0
    assert close_calls == {lock_fd: 0, root_fd: 0}
    with (
        pytest.raises(CollectionLeaseError, match="closing"),
        lease.active_operation(corpus_intent().collection_name),
    ):
        pass

    allow_active_exit.set()
    holder.join(timeout=1)
    for thread in threads:
        thread.join(timeout=1)

    assert not holder.is_alive()
    assert not any(thread.is_alive() for thread in threads)
    assert active_errors == []
    assert errors == []
    assert unlock_calls == 1
    assert close_calls == {lock_fd: 1, root_fd: 1}
    _assert_closed(lock_fd)
    _assert_closed(root_fd)

    reused = os.open(os.devnull, os.O_RDONLY)
    try:
        lease.close()
        os.fstat(reused)
    finally:
        original_close(reused)


def test_close_attempts_both_descriptors_when_unlock_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import corpus_store as corpus_store_module

    store = CorpusManifestStore(tmp_path / "corpus")
    lease = store.acquire_write_lease(corpus_intent().collection_name)
    lock_fd = lease._lock_fd  # noqa: SLF001
    root_fd = lease.root_fd
    original_flock = corpus_store_module.fcntl.flock

    def failing_unlock(descriptor: int, operation: int) -> None:
        if descriptor == lock_fd and operation == corpus_store_module.fcntl.LOCK_UN:
            raise OSError("forced unlock failure")
        original_flock(descriptor, operation)

    monkeypatch.setattr(corpus_store_module.fcntl, "flock", failing_unlock)

    with pytest.raises(OSError, match="forced unlock failure"):
        lease.close()
    _assert_closed(lock_fd)
    _assert_closed(root_fd)


def test_close_attempts_root_close_when_lock_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import corpus_store as corpus_store_module

    store = CorpusManifestStore(tmp_path / "corpus")
    lease = store.acquire_write_lease(corpus_intent().collection_name)
    lock_fd = lease._lock_fd  # noqa: SLF001
    root_fd = lease.root_fd
    original_close = corpus_store_module.os.close
    attempted: list[int] = []

    def failing_lock_close(descriptor: int) -> None:
        attempted.append(descriptor)
        if descriptor == lock_fd:
            raise OSError("forced lock close failure")
        original_close(descriptor)

    monkeypatch.setattr(corpus_store_module.os, "close", failing_lock_close)
    try:
        with pytest.raises(OSError, match="forced lock close failure"):
            lease.close()
    finally:
        monkeypatch.undo()
        original_close(lock_fd)

    assert root_fd in attempted
    _assert_closed(root_fd)


@pytest.mark.parametrize("attack", ["bad-mode", "hardlink", "fifo"])
def test_lock_file_must_be_private_regular_and_single_link(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name
    with store.acquire_write_lease(collection):
        lock_file = next((root / ".locks").iterdir())

    if attack == "bad-mode":
        lock_file.chmod(0o640)
    elif attack == "hardlink":
        os.link(lock_file, tmp_path / "lock-copy")
    else:
        lock_file.unlink()
        os.mkfifo(lock_file, mode=0o600)

    with pytest.raises(FileExistsError):
        store.acquire_write_lease(collection, blocking=False)


def test_locks_directory_requires_exact_private_mode(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name
    with store.acquire_write_lease(collection):
        pass
    (root / ".locks").chmod(0o750)

    with pytest.raises(PermissionError):
        store.acquire_write_lease(collection)


@pytest.mark.parametrize("corruption", ["canonical", "version", "filename"])
def test_writer_gate_fails_closed_on_any_invalid_registry_record(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    _create_manifest(store)
    record = next(item for item in root.iterdir() if item.suffix == ".json")
    if corruption == "canonical":
        record.write_bytes(record.read_bytes() + b"\n")
        expected: type[BaseException] = ValueError
    elif corruption == "version":
        record.write_bytes(
            record.read_bytes().replace(
                b'"schema_version":"corpus-manifest/v1"',
                b'"schema_version":"corpus-manifest/v999"',
            )
        )
        expected = UnsupportedCorpusManifestVersionError
    else:
        record.rename(root / f"{'9' * 64}.json")
        expected = ValueError

    with pytest.raises(expected):
        store.acquire_write_lease("unrelated_collection")


def test_invalid_collection_name_is_rejected_before_filesystem_use(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")

    with pytest.raises(ValueError, match="collection name"):
        store.acquire_write_lease("../collection")
