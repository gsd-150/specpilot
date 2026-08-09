from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from specpilot.manifests.corpus_store import (
    CollectionFreezeLease,
    CorpusManifestStore,
)
from specpilot.retrieval.dense import (
    VECTOR_SIZE,
    DenseIndex,
    DenseIndexWriter,
    DensePoint,
    DenseSnapshotAdmin,
    point_id_for_unit,
)

pytestmark = pytest.mark.integration


def unit_vector(seed: int) -> tuple[float, ...]:
    """A deterministic normalized vector, so a run is reproducible.

    Synthetic on purpose. This exercises the collection, not the encoder —
    loading BGE-M3 here would make a plumbing test depend on two gigabytes of
    weights and would test the model twice.
    """
    raw = [math.sin(seed * (index + 1)) for index in range(VECTOR_SIZE)]
    norm = math.sqrt(sum(value * value for value in raw))
    return tuple(value / norm for value in raw)


def payload(unit_id: str, number: str) -> dict[str, object]:
    return {
        "unit_id": unit_id,
        "kind": "clause",
        "document_id": "ietf-rfc-9110",
        "document_version": "2022-06",
        "section_number": number,
        "section_path": "Fields",
    }


@pytest.fixture
def manifest_store(tmp_path: Path) -> CorpusManifestStore:
    return CorpusManifestStore(tmp_path / "manifests")


@pytest.fixture
def index(
    qdrant_url: str,
    manifest_store: CorpusManifestStore,
) -> Iterator[DenseIndex]:
    collection_name = "specpilot_test_collection"
    raw = QdrantClient(url=qdrant_url, trust_env=False)
    try:
        if raw.collection_exists(collection_name):
            raw.delete_collection(collection_name)
    finally:
        raw.close()

    with manifest_store.acquire_write_lease(
        collection_name
    ) as write_lease, DenseIndexWriter.create(
        qdrant_url,
        collection_name,
        write_lease,
    ) as writer:
        writer.upsert(
            [
                DensePoint("u1", unit_vector(1), payload("u1", "5.6.1")),
                DensePoint("u2", unit_vector(2), payload("u2", "5.6.2")),
            ]
        )

    try:
        with DenseIndex.open(qdrant_url, collection_name) as reader:
            yield reader
    finally:
        cleanup = QdrantClient(url=qdrant_url, trust_env=False)
        try:
            if cleanup.collection_exists(collection_name):
                cleanup.delete_collection(collection_name)
        finally:
            cleanup.close()


@pytest.fixture
def freeze_lease(
    index: DenseIndex,
    manifest_store: CorpusManifestStore,
) -> Iterator[CollectionFreezeLease]:
    with manifest_store.acquire_freeze_lease(index.name) as lease:
        yield lease


def test_a_created_collection_has_the_models_vector_width(
    index: DenseIndex,
) -> None:
    assert index.vector_size() == VECTOR_SIZE


def test_a_local_collection_ignores_host_proxy_settings(
    index: DenseIndex,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")

    with DenseIndex.open(qdrant_url, index.name) as reopened:
        assert reopened.vector_size() == VECTOR_SIZE


def test_upserted_points_are_counted(index: DenseIndex) -> None:
    assert index.point_count() == 2


def test_search_returns_the_nearest_unit_with_its_payload(
    index: DenseIndex,
) -> None:
    hits = index.search(unit_vector(2), k=1)

    assert [hit.unit_id for hit in hits] == ["u2"]
    assert hits[0].payload["section_number"] == "5.6.2"


def test_a_hit_never_carries_clause_text_back(index: DenseIndex) -> None:
    hit = index.search(unit_vector(1), k=1)[0]

    assert set(hit.payload) == {
        "unit_id",
        "kind",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
    }


def test_upserting_the_same_point_twice_does_not_duplicate_it(
    index: DenseIndex,
    qdrant_url: str,
    manifest_store: CorpusManifestStore,
) -> None:
    """A rebuild that doubled the point count would break §6.4's load check."""
    point = DensePoint("u1", unit_vector(1), payload("u1", "5.6.1"))

    with manifest_store.acquire_write_lease(
        index.name
    ) as lease, DenseIndexWriter.open(qdrant_url, index.name, lease) as writer:
        writer.upsert([point])
        writer.upsert([point])

    assert index.point_count() == 2


def test_a_vector_of_the_wrong_width_never_reaches_the_server(
    index: DenseIndex,
) -> None:
    with pytest.raises(ValueError, match="dimension"):
        DensePoint("u1", (0.0, 1.0), payload("u1", "5.6.1"))

    assert index.point_count() == 2


def test_iter_records_returns_deterministic_point_ids(index: DenseIndex) -> None:
    records = tuple(index.iter_records(batch_size=1))

    expected = sorted(
        [
            (point_id_for_unit("u1"), "u1"),
            (point_id_for_unit("u2"), "u2"),
        ]
    )
    assert [
        (record.point_id, record.payload["unit_id"]) for record in records
    ] == expected


def test_real_snapshot_has_a_checksum(
    qdrant_url: str,
    index: DenseIndex,
    freeze_lease: CollectionFreezeLease,
) -> None:
    with DenseSnapshotAdmin.open(qdrant_url, index.name, freeze_lease) as admin:
        created = admin.create_snapshot()

    assert created in index.snapshots()
    assert len(created.checksum) == 64
    assert created.size_bytes > 0


def test_second_create_does_not_delete_the_existing_collection(
    qdrant_url: str,
    index: DenseIndex,
    manifest_store: CorpusManifestStore,
) -> None:
    with manifest_store.acquire_write_lease(
        index.name
    ) as lease, pytest.raises(FileExistsError):
        DenseIndexWriter.create(qdrant_url, index.name, lease)

    assert index.point_count() == 2
    assert {record.payload["unit_id"] for record in index.iter_records()} == {
        "u1",
        "u2",
    }
