from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.deployment.ready import (
    ReadyMarker,
    ReadyMarkerStore,
    require_ready_corpus,
)
from specpilot.manifests.corpus_store import CorpusManifestStore
from tests.helpers.corpus_manifest_factory import corpus_draft


def _marker(**changes: object) -> ReadyMarker:
    values: dict[str, object] = {
        "source_manifest_ids": ("a" * 64,),
        "corpus_manifest_id": "b" * 64,
        "collection_name": "specpilot_fixture",
        "point_count": 5,
        "inventory_root_sha256": "c" * 64,
        "mode": "fixture",
    }
    values.update(changes)
    return ReadyMarker.create(**values)  # type: ignore[arg-type]


def test_marker_id_is_the_canonical_hash_of_the_bound_identity() -> None:
    marker = _marker()
    payload = marker.model_dump(mode="json", exclude={"ready_id"})
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    assert marker.ready_id == hashlib.sha256(encoded).hexdigest()
    assert "path" not in marker.model_dump_json()


def test_marker_is_closed_and_rejects_a_forged_id() -> None:
    marker = _marker()
    with pytest.raises(ValidationError):
        ReadyMarker.model_validate({**marker.model_dump(), "host_path": "/secret"})
    with pytest.raises(ValidationError):
        ReadyMarker.model_validate({**marker.model_dump(), "ready_id": "d" * 64})


def test_store_creates_private_root_and_atomic_private_record(tmp_path: Path) -> None:
    root = tmp_path / "ready"
    marker = _marker()

    stored = ReadyMarkerStore(root).publish(marker)

    record = root / f"{marker.ready_id}.json"
    assert stored == marker
    assert ReadyMarkerStore(root).read() == marker
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert [path.name for path in root.iterdir()] == [record.name]


def test_identical_publish_is_idempotent_without_rewriting(tmp_path: Path) -> None:
    root = tmp_path / "ready"
    marker = _marker()
    store = ReadyMarkerStore(root)
    store.publish(marker)
    record = root / f"{marker.ready_id}.json"
    fixed = 1_700_000_000_000_000_000
    os.utime(record, ns=(fixed, fixed))

    assert store.publish(marker) == marker
    assert record.stat().st_mtime_ns == fixed


def test_store_refuses_a_differently_bound_marker(tmp_path: Path) -> None:
    store = ReadyMarkerStore(tmp_path / "ready")
    existing = _marker()
    store.publish(existing)

    with pytest.raises(FileExistsError):
        store.publish(_marker(inventory_root_sha256="d" * 64))

    assert store.read() == existing


def test_store_refuses_symlink_roots_and_records(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o700)
    root_link = tmp_path / "ready-link"
    root_link.symlink_to(victim, target_is_directory=True)
    with pytest.raises((FileExistsError, OSError)):
        ReadyMarkerStore(root_link).publish(_marker())

    root = tmp_path / "ready"
    root.mkdir(mode=0o700)
    marker = _marker()
    record_victim = tmp_path / "record-victim"
    record_victim.write_bytes(b"unchanged")
    (root / f"{marker.ready_id}.json").symlink_to(record_victim)
    with pytest.raises(FileExistsError):
        ReadyMarkerStore(root).publish(marker)
    assert record_victim.read_bytes() == b"unchanged"


def test_require_rejects_stale_or_missing_identity(tmp_path: Path) -> None:
    store = ReadyMarkerStore(tmp_path / "ready")
    marker = _marker()
    with pytest.raises(FileNotFoundError):
        store.require(marker)
    store.publish(marker)
    assert store.require(marker) == marker
    with pytest.raises(ValueError, match="identity"):
        store.require(_marker(mode="real"))


def test_runtime_gate_requires_exact_corpus_and_mode(tmp_path: Path) -> None:
    corpus_store = CorpusManifestStore(tmp_path / "corpus")
    draft = corpus_draft(point_count=5)
    with corpus_store.acquire_freeze_lease(draft.collection_name) as lease:
        corpus = corpus_store.create(draft, lease=lease)
    marker = ReadyMarker.create(
        source_manifest_ids=corpus.source_manifest_ids,
        corpus_manifest_id=corpus.manifest_id,
        collection_name=corpus.collection_name,
        point_count=corpus.point_count,
        inventory_root_sha256=corpus.inventory_root_sha256,
        mode="fixture",
    )
    ready_store = ReadyMarkerStore(tmp_path / "ready")
    ready_store.publish(marker)

    assert (
        require_ready_corpus(
            ready_dir=tmp_path / "ready",
            ready_id=marker.ready_id,
            corpus=corpus,
            source_manifest_ids=corpus.source_manifest_ids,
            mode="fixture",
        )
        == marker
    )
    with pytest.raises(ValueError, match="mode"):
        require_ready_corpus(
            ready_dir=tmp_path / "ready",
            ready_id=marker.ready_id,
            corpus=corpus,
            source_manifest_ids=corpus.source_manifest_ids,
            mode="real",
        )
    with pytest.raises(ValueError, match="corpus"):
        require_ready_corpus(
            ready_dir=tmp_path / "ready",
            ready_id=marker.ready_id,
            corpus=corpus.model_copy(
                update={"inventory_root_sha256": "d" * 64},
            ),
            source_manifest_ids=corpus.source_manifest_ids,
            mode="fixture",
        )
