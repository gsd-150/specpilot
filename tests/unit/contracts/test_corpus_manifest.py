from __future__ import annotations

from datetime import UTC, datetime
from math import inf, nan

import pytest
from pydantic import ValidationError

from specpilot.contracts.corpus_manifest import (
    Bm25Binding,
    CorpusManifest,
    CorpusManifestDraft,
    CorpusManifestIntent,
    DenseVectorSchema,
    LocatorFieldSchema,
    QdrantCollectionSchema,
    QdrantSnapshotBinding,
)
from specpilot.manifests.canonical import canonical_json, canonical_sha256
from tests.helpers.corpus_manifest_factory import (
    SOURCE_IDS,
    corpus_draft,
    corpus_intent,
)

GOLDEN_DRAFT_BYTES = 2585
GOLDEN_MANIFEST_ID = "d477eed26ce3a56d41286f18fbba711926abe9b38f0430af8c451c9a48a277bf"


def test_manifest_is_content_addressed_and_round_trips() -> None:
    draft = corpus_draft()
    manifest = CorpusManifest.from_draft(draft)

    assert manifest.manifest_id == canonical_sha256(draft)
    assert CorpusManifest.model_validate_json(
        canonical_json(manifest, include_manifest_id=True)
    ) == manifest
    with pytest.raises(ValidationError):
        manifest.point_count = 1  # type: ignore[misc]


def test_canonical_draft_bytes_and_id_are_frozen() -> None:
    draft = corpus_draft()

    assert len(canonical_json(draft)) == GOLDEN_DRAFT_BYTES
    assert canonical_sha256(draft) == GOLDEN_MANIFEST_ID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_weights_sha256", "9" * 64),
        ("point_count", 1923),
        ("inventory_root_sha256", "8" * 64),
        ("created_at", datetime(2026, 8, 9, 12, tzinfo=UTC)),
        (
            "snapshot",
            QdrantSnapshotBinding(
                name="other.snapshot", checksum="7" * 64, size_bytes=4096
            ),
        ),
    ],
)
def test_every_bound_change_produces_a_new_manifest_id(
    field: str, value: object
) -> None:
    first = CorpusManifest.from_draft(corpus_draft())
    second = CorpusManifest.from_draft(corpus_draft(**{field: value}))

    assert second.manifest_id != first.manifest_id


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CorpusManifestIntent(**corpus_intent().model_dump(), unexpected="unsafe")


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        ("2026-08-09T19:00:00+08:00", datetime(2026, 8, 9, 11, tzinfo=UTC)),
        (datetime(2026, 8, 9, 19, tzinfo=UTC), datetime(2026, 8, 9, 19, tzinfo=UTC)),
    ],
)
def test_created_at_normalizes_to_utc(
    created_at: str | datetime, expected: datetime
) -> None:
    assert corpus_draft(created_at=created_at).created_at == expected


@pytest.mark.parametrize("created_at", ["2026-08-09T11:00:00", "not-a-time"])
def test_created_at_requires_a_timezone_aware_rfc3339_timestamp(
    created_at: str,
) -> None:
    with pytest.raises(ValidationError):
        corpus_draft(created_at=created_at)


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_bm25_rejects_non_finite_values(value: float) -> None:
    values = corpus_intent().bm25.model_dump()
    values["k1"] = value

    with pytest.raises(ValidationError):
        Bm25Binding(**values)


@pytest.mark.parametrize("field", ["k1", "b"])
def test_bm25_rejects_out_of_range_values(field: str) -> None:
    values = corpus_intent().bm25.model_dump()
    values[field] = -0.1

    with pytest.raises(ValidationError):
        Bm25Binding(**values)


@pytest.mark.parametrize("field", ["point_count", "dense_top_k", "m"])
def test_positive_integer_contract_fields_reject_zero(field: str) -> None:
    values = corpus_draft().model_dump()
    if field == "dense_top_k":
        retrieval = values["retrieval"]
        assert isinstance(retrieval, dict)
        retrieval[field] = 0
    elif field == "m":
        collection_schema = values["collection_schema"]
        assert isinstance(collection_schema, dict)
        hnsw = collection_schema["hnsw"]
        assert isinstance(hnsw, dict)
        hnsw[field] = 0
    else:
        values[field] = 0

    with pytest.raises(ValidationError):
        CorpusManifestDraft(**values)


def test_sources_are_unique_and_parse_qa_covers_them_in_order() -> None:
    with pytest.raises(ValidationError):
        corpus_intent(source_manifest_ids=(SOURCE_IDS[0], SOURCE_IDS[0]))

    with pytest.raises(ValidationError):
        corpus_intent(parse_qa=tuple(reversed(corpus_intent().parse_qa)))


def test_collection_schema_requires_canonical_locator_order() -> None:
    values = corpus_intent().collection_schema.model_dump()
    locator_payload = values["locator_payload"]
    assert isinstance(locator_payload, tuple)
    values["locator_payload"] = tuple(reversed(locator_payload))

    with pytest.raises(ValidationError):
        QdrantCollectionSchema(**values)


def test_collection_schema_rejects_duplicate_locator_field() -> None:
    values = corpus_intent().collection_schema.model_dump()
    locator_payload = values["locator_payload"]
    assert isinstance(locator_payload, tuple)
    duplicate = LocatorFieldSchema(
        name="unit_id", value_type="keyword", nullable=False, payload_indexed=False
    )
    values["locator_payload"] = (*locator_payload[:-1], duplicate)

    with pytest.raises(ValidationError):
        QdrantCollectionSchema(**values)


def test_manifest_requires_a_1024_wide_dense_vector() -> None:
    schema_values = corpus_intent().collection_schema.model_dump()
    dense_values = schema_values["dense_vector"]
    assert isinstance(dense_values, dict)
    dense_values["size"] = 768
    schema_values["dense_vector"] = DenseVectorSchema(**dense_values)

    with pytest.raises(ValidationError):
        corpus_intent(collection_schema=QdrantCollectionSchema(**schema_values))
