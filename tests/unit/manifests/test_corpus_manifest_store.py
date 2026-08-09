from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specpilot.contracts.corpus_manifest import (
    CorpusManifest,
    CorpusManifestDraft,
    ParseQaEvidence,
    QdrantSnapshotBinding,
)
from specpilot.manifests.canonical import canonical_json
from specpilot.manifests.corpus_store import (
    CollectionLeaseError,
    CorpusManifestIntentConflictError,
    CorpusManifestStore,
    CorpusPredecessorError,
    UnsupportedCorpusManifestVersionError,
)
from tests.helpers.corpus_manifest_factory import (
    SOURCE_IDS,
    corpus_draft,
    corpus_intent,
)


def _record_path(root: Path, manifest: CorpusManifest) -> Path:
    return root / f"{manifest.manifest_id}.json"


def _private_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _successor_draft(
    predecessor: CorpusManifest,
    **changes: object,
) -> CorpusManifestDraft:
    values: dict[str, object] = {
        "predecessor_manifest_id": predecessor.manifest_id,
        "inventory_root_sha256": "8" * 64,
        "snapshot": QdrantSnapshotBinding(
            name="successor.snapshot",
            checksum="7" * 64,
            size_bytes=8192,
        ),
        "created_at": datetime(2026, 8, 9, 12, tzinfo=UTC),
    }
    values.update(changes)
    return corpus_draft(**values)


def _create(store: CorpusManifestStore, draft: CorpusManifestDraft) -> CorpusManifest:
    with store.acquire_freeze_lease(draft.collection_name) as lease:
        return store.create(draft, lease=lease)


def test_create_round_trips_canonical_private_records(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    draft = corpus_draft()

    with store.acquire_freeze_lease(draft.collection_name) as lease:
        stored = store.create(draft, lease=lease)

    record = _record_path(root, stored)
    lock_file = root / ".locks" / (
        "f954a9444778ccc7b7cf9163eed41baae53a8f9e775870abaf00934202033b4d.lock"
    )
    assert store.read(stored.manifest_id) == stored
    assert store.read_all() == (stored,)
    assert record.read_bytes() == canonical_json(stored, include_manifest_id=True)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / ".locks").stat().st_mode) == 0o700
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert record.stat().st_nlink == 1
    assert lock_file.is_file()
    assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600
    assert lock_file.stat().st_nlink == 1


def test_create_requires_an_active_matching_freeze_lease(tmp_path: Path) -> None:
    first_store = CorpusManifestStore(tmp_path / "first")
    second_store = CorpusManifestStore(tmp_path / "second")
    draft = corpus_draft()

    with first_store.acquire_freeze_lease(draft.collection_name) as lease:
        with pytest.raises(CollectionLeaseError):
            second_store.create(draft, lease=lease)
        stored = first_store.create(draft, lease=lease)

    assert first_store.read(stored.manifest_id) == stored
    with pytest.raises(CollectionLeaseError):
        first_store.create(draft, lease=lease)


def test_create_rejects_a_freeze_lease_for_another_collection(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")

    with (
        store.acquire_freeze_lease("another_collection") as lease,
        pytest.raises(CollectionLeaseError),
    ):
        store.create(corpus_draft(), lease=lease)


def test_byte_identical_replay_returns_the_existing_manifest(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    draft = corpus_draft()

    with store.acquire_freeze_lease(draft.collection_name) as lease:
        first = store.create(draft, lease=lease)
        inode = _record_path(tmp_path / "corpus", first).stat().st_ino
        replay = store.create(draft, lease=lease)

    assert replay == first
    assert _record_path(tmp_path / "corpus", first).stat().st_ino == inode
    assert store.read_all() == (first,)


def test_same_intent_cannot_bind_two_snapshots(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    first = corpus_draft()
    second = corpus_draft(
        snapshot=QdrantSnapshotBinding(
            name="other.snapshot", checksum="9" * 64, size_bytes=8192
        )
    )

    with store.acquire_freeze_lease(first.collection_name) as lease:
        store.create(first, lease=lease)
        with pytest.raises(CorpusManifestIntentConflictError):
            store.create(second, lease=lease)


def test_source_manifest_order_is_preserved(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    reversed_ids = tuple(reversed(SOURCE_IDS))
    draft = corpus_draft(
        source_manifest_ids=reversed_ids,
        parse_qa=(
            ParseQaEvidence(
                source_manifest_id=reversed_ids[0], evidence_sha256="2" * 64
            ),
            ParseQaEvidence(
                source_manifest_id=reversed_ids[1], evidence_sha256="1" * 64
            ),
        ),
    )

    stored = _create(store, draft)

    assert stored.source_manifest_ids == reversed_ids
    assert store.read(stored.manifest_id).source_manifest_ids == reversed_ids


def test_new_manifest_for_a_frozen_collection_requires_explicit_predecessor(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    first = _create(store, corpus_draft())
    unrelated_intent = corpus_draft(
        inventory_root_sha256="8" * 64,
        snapshot=QdrantSnapshotBinding(
            name="new.snapshot", checksum="7" * 64, size_bytes=8192
        ),
    )

    with (
        store.acquire_freeze_lease(first.collection_name) as lease,
        pytest.raises(CorpusPredecessorError, match="explicit predecessor"),
    ):
        store.create(unrelated_intent, lease=lease)


def test_predecessor_must_exist(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    draft = corpus_draft(predecessor_manifest_id="4" * 64)

    with (
        store.acquire_freeze_lease(draft.collection_name) as lease,
        pytest.raises(CorpusPredecessorError, match="does not exist"),
    ):
        store.create(draft, lease=lease)


def test_predecessor_must_bind_the_same_collection(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    predecessor = _create(store, corpus_draft(collection_name="first_collection"))
    draft = corpus_draft(
        collection_name="second_collection",
        predecessor_manifest_id=predecessor.manifest_id,
    )

    with (
        store.acquire_freeze_lease(draft.collection_name) as lease,
        pytest.raises(CorpusPredecessorError, match="another collection"),
    ):
        store.create(draft, lease=lease)


def test_successor_round_trip_validates_the_complete_predecessor_graph(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    predecessor = _create(store, corpus_draft())
    successor = _create(store, _successor_draft(predecessor))

    assert store.read_all() == tuple(
        sorted((predecessor, successor), key=lambda item: item.manifest_id)
    )


def test_exact_replay_fails_if_its_predecessor_record_disappears(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    predecessor = _create(store, corpus_draft())
    draft = _successor_draft(predecessor)
    _create(store, draft)
    _record_path(root, predecessor).unlink()

    with (
        store.acquire_freeze_lease(draft.collection_name) as lease,
        pytest.raises(CorpusPredecessorError, match="does not exist"),
    ):
        store.create(draft, lease=lease)


def test_find_by_intent_requires_an_active_exclusive_owned_lease(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    intent = corpus_intent()

    with (
        store.acquire_write_lease(intent.collection_name) as write_lease,
        pytest.raises(CollectionLeaseError),
    ):
        store.find_by_intent(intent, lease=write_lease)  # type: ignore[arg-type]

    with store.acquire_freeze_lease(intent.collection_name) as freeze_lease:
        assert store.find_by_intent(intent, lease=freeze_lease) is None
    with pytest.raises(CollectionLeaseError):
        store.find_by_intent(intent, lease=freeze_lease)


def test_read_rejects_unknown_schema_before_content_id_dispatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    manifest = _create(store, corpus_draft())
    raw = json.loads(_record_path(root, manifest).read_bytes())
    raw["schema_version"] = "corpus-manifest/v999"
    _record_path(root, manifest).write_bytes(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    )

    with pytest.raises(UnsupportedCorpusManifestVersionError):
        store.read(manifest.manifest_id)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda data: data + b"\n",
        lambda data: data.replace(b'"point_count":1922', b'"point_count":1921'),
    ],
    ids=["noncanonical", "content-id"],
)
def test_read_rejects_tampered_records(
    tmp_path: Path,
    tamper: Callable[[bytes], bytes],
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    manifest = _create(store, corpus_draft())
    record = _record_path(root, manifest)
    record.write_bytes(tamper(record.read_bytes()))

    with pytest.raises(ValueError, match="stored corpus manifest"):
        store.read(manifest.manifest_id)


def test_read_rejects_filename_content_id_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    manifest = _create(store, corpus_draft())
    _record_path(root, manifest).rename(root / f"{'9' * 64}.json")

    with pytest.raises(ValueError, match="filename"):
        store.read_all()


def test_every_registry_consumer_fails_closed_on_one_corrupt_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    first = _create(store, corpus_draft(collection_name="first_collection"))
    second_draft = corpus_draft(collection_name="second_collection")
    second = _create(store, second_draft)
    first_record = _record_path(root, first)
    first_record.write_bytes(first_record.read_bytes() + b"\n")

    with pytest.raises(ValueError):
        store.read(second.manifest_id)
    with pytest.raises(ValueError):
        store.read_all()
    with store.acquire_freeze_lease(second.collection_name) as lease:
        with pytest.raises(ValueError):
            store.find_by_intent(second_draft.intent, lease=lease)
        with pytest.raises(ValueError):
            store.create(second_draft, lease=lease)
    with pytest.raises(ValueError):
        store.acquire_write_lease("unrelated_collection")


@pytest.mark.parametrize("unsafe_entry", ["unrelated", "record-hardlink"])
def test_registry_scan_rejects_unsafe_entries(
    tmp_path: Path,
    unsafe_entry: str,
) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    manifest = _create(store, corpus_draft())
    if unsafe_entry == "unrelated":
        _private_file(root / "unexpected", b"attacker")
    else:
        os.link(_record_path(root, manifest), tmp_path / "record-copy")

    with pytest.raises(FileExistsError):
        store.read_all()


def test_registry_scan_requires_the_validated_locks_child(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    store = CorpusManifestStore(root)
    manifest = _create(store, corpus_draft())
    locks = root / ".locks"
    moved_locks = root / ".real-locks"
    locks.rename(moved_locks)
    locks.symlink_to(moved_locks, target_is_directory=True)

    with pytest.raises(FileExistsError):
        store.read(manifest.manifest_id)


def test_read_validates_manifest_id_before_scanning(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        store.read("../not-an-id")
