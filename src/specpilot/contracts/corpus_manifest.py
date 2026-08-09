from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from specpilot.contracts.manifests import Identifier, Sha256
from specpilot.manifests.canonical import canonical_sha256

_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        if _RFC3339_TIMESTAMP.fullmatch(value) is None:
            raise ValueError("created_at must be an RFC3339 timestamp")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("created_at must be an RFC3339 timestamp") from error
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("created_at must be timezone-aware")
    return value.astimezone(UTC)


CollectionName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
]
SnapshotName = CollectionName


class CorpusComponentVersions(_FrozenModel):
    parser: Identifier
    chunker: Identifier
    index_text: Identifier
    embedding_pipeline: Identifier


class Bm25Binding(_FrozenModel):
    tokenizer_version: Identifier
    k1: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    b: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    index_fingerprint: Sha256


class DenseQueryParameters(_FrozenModel):
    hnsw_ef: Annotated[int, Field(strict=True, gt=0)] | None
    exact: StrictBool
    indexed_only: StrictBool


class RetrievalProtocolBinding(_FrozenModel):
    dense_top_k: Annotated[int, Field(strict=True, gt=0)]
    bm25_top_k: Annotated[int, Field(strict=True, gt=0)]
    rrf_k: Annotated[int, Field(strict=True, gt=0)]
    final_top_k: Annotated[int, Field(strict=True, gt=0)]
    deduplication_key: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    stable_tie_key: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    dense_query: DenseQueryParameters


class HnswSchema(_FrozenModel):
    m: Annotated[int, Field(strict=True, gt=0)]
    ef_construct: Annotated[int, Field(strict=True, gt=0)]
    full_scan_threshold: Annotated[int, Field(strict=True, ge=0)]
    max_indexing_threads: Annotated[int, Field(strict=True, ge=0)] | None
    on_disk: StrictBool
    payload_m: Annotated[int, Field(strict=True, gt=0)] | None


class DenseVectorSchema(_FrozenModel):
    name: None = None
    size: Annotated[int, Field(strict=True, gt=0)]
    distance: Literal["cosine"]
    datatype: Literal["float32"]
    on_disk: StrictBool
    vector_quantization_sha256: Sha256 | None


class SparseVectorSchema(_FrozenModel):
    name: Identifier
    config_sha256: Sha256


class PayloadIndexSchema(_FrozenModel):
    field_name: Identifier
    data_type: Identifier
    params_sha256: Sha256 | None


class LocatorFieldSchema(_FrozenModel):
    name: Identifier
    value_type: Literal["keyword", "integer"]
    nullable: StrictBool
    payload_indexed: StrictBool


class QdrantCollectionSchema(_FrozenModel):
    dense_vector: DenseVectorSchema
    hnsw: HnswSchema
    collection_quantization_sha256: Sha256 | None
    sparse_vectors: tuple[SparseVectorSchema, ...]
    payload_indexes: tuple[PayloadIndexSchema, ...]
    locator_payload: tuple[LocatorFieldSchema, ...]

    @model_validator(mode="after")
    def _canonical_schema_order(self) -> Self:
        if tuple(item.name for item in self.sparse_vectors) != tuple(
            sorted(item.name for item in self.sparse_vectors)
        ):
            raise ValueError("sparse vector schema is not canonically ordered")
        if tuple(item.field_name for item in self.payload_indexes) != tuple(
            sorted(item.field_name for item in self.payload_indexes)
        ):
            raise ValueError("payload index schema is not canonically ordered")
        expected = (
            "unit_id",
            "kind",
            "document_id",
            "document_version",
            "section_number",
            "section_path",
        )
        if tuple(item.name for item in self.locator_payload) != expected:
            raise ValueError("locator payload schema is not locator-payload/v1")
        return self


class ParseQaEvidence(_FrozenModel):
    source_manifest_id: Sha256
    evidence_sha256: Sha256


class QdrantSnapshotBinding(_FrozenModel):
    name: SnapshotName
    checksum: Sha256
    size_bytes: Annotated[int, Field(strict=True, gt=0)]


class CorpusManifestIntent(_FrozenModel):
    schema_version: Literal["corpus-manifest/v1"] = "corpus-manifest/v1"
    predecessor_manifest_id: Sha256 | None = None
    source_manifest_ids: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    versions: CorpusComponentVersions
    embedding_weights_sha256: Sha256
    bm25: Bm25Binding
    retrieval: RetrievalProtocolBinding
    collection_name: CollectionName
    collection_schema: QdrantCollectionSchema
    point_count: Annotated[int, Field(strict=True, gt=0)]
    derived_corpus_sha256: Sha256
    inventory_root_sha256: Sha256
    parse_qa: Annotated[tuple[ParseQaEvidence, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _cross_validate(self) -> Self:
        if len(set(self.source_manifest_ids)) != len(self.source_manifest_ids):
            raise ValueError("a source manifest is listed twice")
        if (
            tuple(item.source_manifest_id for item in self.parse_qa)
            != self.source_manifest_ids
        ):
            raise ValueError("parse QA must cover sources in canonical order")
        if self.collection_schema.dense_vector.size != 1024:
            raise ValueError("the corpus dense vector must be 1024 wide")
        if self.collection_schema.sparse_vectors:
            raise ValueError("corpus-manifest/v1 does not support sparse vectors")
        return self


class CorpusManifestDraft(CorpusManifestIntent):
    snapshot: QdrantSnapshotBinding
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def _normalize_created_at(cls, value: object) -> datetime:
        return _utc_timestamp(value)

    @property
    def intent(self) -> CorpusManifestIntent:
        return CorpusManifestIntent.model_validate(
            self.model_dump(exclude={"snapshot", "created_at", "manifest_id"})
        )


class CorpusManifest(CorpusManifestDraft):
    manifest_id: Sha256

    @model_validator(mode="after")
    def _verify_manifest_id(self) -> Self:
        if self.manifest_id != canonical_sha256(self):
            raise ValueError("manifest_id does not match canonical content")
        return self

    @classmethod
    def from_draft(cls, draft: CorpusManifestDraft) -> CorpusManifest:
        return cls(manifest_id=canonical_sha256(draft), **draft.model_dump())
