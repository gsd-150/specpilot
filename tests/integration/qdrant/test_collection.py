from __future__ import annotations

import math
from collections.abc import Iterator

import pytest

from specpilot.retrieval.dense import (
    VECTOR_SIZE,
    CollectionFrozenError,
    DenseIndex,
    DensePoint,
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
def index(qdrant_url: str) -> Iterator[DenseIndex]:
    created = DenseIndex.create(qdrant_url, "specpilot_test_collection")
    yield created
    created.drop()


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

    reopened = DenseIndex.open(qdrant_url, index.name, frozen=True)

    assert reopened.vector_size() == VECTOR_SIZE


def test_upserted_points_are_counted(index: DenseIndex) -> None:
    index.upsert(
        [
            DensePoint("u1", unit_vector(1), payload("u1", "5.6.1")),
            DensePoint("u2", unit_vector(2), payload("u2", "5.6.2")),
        ]
    )

    assert index.point_count() == 2


def test_search_returns_the_nearest_unit_with_its_payload(
    index: DenseIndex,
) -> None:
    index.upsert(
        [
            DensePoint("u1", unit_vector(1), payload("u1", "5.6.1")),
            DensePoint("u2", unit_vector(2), payload("u2", "5.6.2")),
        ]
    )

    hits = index.search(unit_vector(2), k=1)

    assert [hit.unit_id for hit in hits] == ["u2"]
    assert hits[0].payload["section_number"] == "5.6.2"


def test_a_hit_never_carries_clause_text_back(index: DenseIndex) -> None:
    index.upsert([DensePoint("u1", unit_vector(1), payload("u1", "5.6.1"))])

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
) -> None:
    """A rebuild that doubled the point count would break §6.4's load check."""
    point = DensePoint("u1", unit_vector(1), payload("u1", "5.6.1"))

    index.upsert([point])
    index.upsert([point])

    assert index.point_count() == 1


def test_a_frozen_collection_refuses_a_write(index: DenseIndex) -> None:
    index.upsert([DensePoint("u1", unit_vector(1), payload("u1", "5.6.1"))])
    index.freeze()

    with pytest.raises(CollectionFrozenError):
        index.upsert([DensePoint("u2", unit_vector(2), payload("u2", "5.6.2"))])

    assert index.point_count() == 1


def test_a_frozen_collection_still_serves_reads(index: DenseIndex) -> None:
    index.upsert([DensePoint("u1", unit_vector(1), payload("u1", "5.6.1"))])
    index.freeze()

    assert index.search(unit_vector(1), k=1)[0].unit_id == "u1"


def test_a_vector_of_the_wrong_width_never_reaches_the_server(
    index: DenseIndex,
) -> None:
    with pytest.raises(ValueError, match="dimension"):
        index.upsert([DensePoint("u1", (0.0, 1.0), payload("u1", "5.6.1"))])

    assert index.point_count() == 0
