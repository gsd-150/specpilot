from __future__ import annotations

import copy
import errno
import os
import pickle
import subprocess
import sys
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
WRITER_PROBE = """
import sys
from pathlib import Path

from specpilot.manifests.corpus_store import CollectionFrozenError, CorpusManifestStore

Path(sys.argv[3]).write_text("ready", encoding="utf-8")
try:
    with CorpusManifestStore(Path(sys.argv[1])).acquire_write_lease(sys.argv[2]):
        pass
except CollectionFrozenError:
    raise SystemExit(74)
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
    ready = tmp_path / "writer-ready"
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
                    str(ready),
                ]
            )
            deadline = time.monotonic() + 1
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
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


def test_private_lease_factory_rejects_an_untrusted_issuer() -> None:
    with pytest.raises(CollectionLeaseError, match="issuer"):
        CollectionFreezeLease._issue(  # noqa: SLF001
            issue_token=object(),
            collection_name=corpus_intent().collection_name,
            owner_token=object(),
            root_fd=-1,
            lock_fd=-1,
            locks_identity=(0, 0),
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
