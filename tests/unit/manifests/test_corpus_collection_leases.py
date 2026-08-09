from __future__ import annotations

import copy
import errno
import os
import pickle
import subprocess
import sys
import threading
import time
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

FROZEN_EXIT = 74
CONTENTION_MISSED_EXIT = 75
PROBE_FAILURE_EXIT = 76
WRITER_PROBE = f"""
import sys
from pathlib import Path

from specpilot.manifests.corpus_store import (
    CollectionFrozenError,
    CollectionLeaseUnavailableError,
    CorpusManifestStore,
)

store = CorpusManifestStore(Path(sys.argv[1]))
try:
    uncontended = store.acquire_write_lease(sys.argv[2], blocking=False)
except CollectionLeaseUnavailableError:
    pass
except BaseException:
    raise SystemExit({PROBE_FAILURE_EXIT})
else:
    uncontended.close()
    raise SystemExit({CONTENTION_MISSED_EXIT})

Path(sys.argv[3]).write_text("contended", encoding="utf-8")
Path(sys.argv[4]).write_text("blocking-started", encoding="utf-8")
try:
    with store.acquire_write_lease(sys.argv[2]):
        pass
except CollectionFrozenError:
    raise SystemExit({FROZEN_EXIT})
except BaseException:
    raise SystemExit({PROBE_FAILURE_EXIT})
raise SystemExit(0)
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


class _RacingClosedState:
    def __init__(self) -> None:
        self._read_barrier = threading.Barrier(2)
        self._write_barrier = threading.Barrier(2)
        self._value = False

    def __get__(self, instance: object, owner: type[object]) -> object:
        del owner
        if instance is None:
            return self
        value = self._value
        if not value:
            self._read_barrier.wait(timeout=1)
        return value

    def __set__(self, instance: object, value: bool) -> None:
        del instance
        if value:
            self._write_barrier.wait(timeout=1)
        self._value = value


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


def test_waiting_writer_rechecks_registry_after_freeze_publishes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    collection = corpus_intent().collection_name
    contended = tmp_path / "writer-contended"
    blocking_started = tmp_path / "writer-blocking-started"
    waiting: subprocess.Popen[bytes] | None = None
    try:
        with store.acquire_freeze_lease(collection) as freeze_lease:
            waiting = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    WRITER_PROBE,
                    str(root),
                    collection,
                    str(contended),
                    str(blocking_started),
                ]
            )
            deadline = time.monotonic() + 1
            while (
                not (contended.exists() and blocking_started.exists())
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert contended.exists()
            assert blocking_started.exists()
            assert waiting.poll() is None
            store.create(corpus_draft(), lease=freeze_lease)
        assert _wait_or_terminate(waiting) == FROZEN_EXIT
        waiting = None
    finally:
        if waiting is not None and waiting.poll() is None:
            waiting.terminate()
            _wait_or_terminate(waiting, timeout=1)


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
    lease_base = type(lease).__mro__[1]
    racing_closed = _RacingClosedState()
    original_close = corpus_store_module.os.close
    original_flock = corpus_store_module.fcntl.flock
    call_lock = threading.Lock()
    close_calls = {lock_fd: 0, root_fd: 0}
    unlock_calls = 0

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

    monkeypatch.setattr(lease_base, "_closed", racing_closed, raising=False)
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
    for thread in threads:
        thread.join(timeout=1)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert unlock_calls == 1
    assert close_calls == {lock_fd: 1, root_fd: 1}


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
