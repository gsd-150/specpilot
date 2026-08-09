from datetime import UTC, datetime

from specpilot.contracts.corpus_manifest import (
    Bm25Binding,
    CorpusComponentVersions,
    CorpusManifestDraft,
    CorpusManifestIntent,
    DenseQueryParameters,
    DenseVectorSchema,
    HnswSchema,
    LocatorFieldSchema,
    ParseQaEvidence,
    QdrantCollectionSchema,
    QdrantSnapshotBinding,
    RetrievalProtocolBinding,
)

SOURCE_IDS = ("a" * 64, "b" * 64)


def corpus_intent(**changes: object) -> CorpusManifestIntent:
    values: dict[str, object] = {
        "predecessor_manifest_id": None,
        "source_manifest_ids": SOURCE_IDS,
        "versions": CorpusComponentVersions(
            parser="rfcxml-v3/v1",
            chunker="rfc-clause-table/v1",
            index_text="index-text/v1",
            embedding_pipeline="clause/v1",
        ),
        "embedding_weights_sha256": "c" * 64,
        "bm25": Bm25Binding(
            tokenizer_version="bm25-rfc/v1",
            k1=1.2,
            b=0.75,
            index_fingerprint="d" * 64,
        ),
        "retrieval": RetrievalProtocolBinding(
            dense_top_k=20,
            bm25_top_k=20,
            rrf_k=60,
            final_top_k=5,
            deduplication_key=(
                "corpus_manifest_id", "document_id", "clause_id", "child_span"
            ),
            stable_tie_key=("document_id", "numeric_clause_path", "child_start"),
            dense_query=DenseQueryParameters(
                hnsw_ef=None,
                exact=False,
                indexed_only=False,
            ),
        ),
        "collection_name": "specpilot_0123456789abcdef0123456789abcdef",
        "collection_schema": QdrantCollectionSchema(
            dense_vector=DenseVectorSchema(
                name=None,
                size=1024,
                distance="cosine",
                datatype="float32",
                on_disk=False,
                vector_quantization_sha256=None,
            ),
            hnsw=HnswSchema(
                m=16,
                ef_construct=100,
                full_scan_threshold=10000,
                max_indexing_threads=0,
                on_disk=False,
                payload_m=None,
            ),
            collection_quantization_sha256=None,
            sparse_vectors=(),
            payload_indexes=(),
            locator_payload=(
                LocatorFieldSchema(
                    name="unit_id",
                    value_type="keyword",
                    nullable=False,
                    payload_indexed=False,
                ),
                LocatorFieldSchema(
                    name="kind",
                    value_type="keyword",
                    nullable=False,
                    payload_indexed=False,
                ),
                LocatorFieldSchema(
                    name="document_id",
                    value_type="keyword",
                    nullable=False,
                    payload_indexed=False,
                ),
                LocatorFieldSchema(
                    name="document_version",
                    value_type="keyword",
                    nullable=False,
                    payload_indexed=False,
                ),
                LocatorFieldSchema(
                    name="section_number",
                    value_type="keyword",
                    nullable=True,
                    payload_indexed=False,
                ),
                LocatorFieldSchema(
                    name="section_path",
                    value_type="keyword",
                    nullable=False,
                    payload_indexed=False,
                ),
            ),
        ),
        "point_count": 1922,
        "derived_corpus_sha256": "e" * 64,
        "inventory_root_sha256": "f" * 64,
        "parse_qa": (
            ParseQaEvidence(
                source_manifest_id=SOURCE_IDS[0], evidence_sha256="1" * 64
            ),
            ParseQaEvidence(
                source_manifest_id=SOURCE_IDS[1], evidence_sha256="2" * 64
            ),
        ),
    }
    values.update(changes)
    return CorpusManifestIntent(**values)


def corpus_draft(**changes: object) -> CorpusManifestDraft:
    values = corpus_intent().model_dump()
    values.update(
        snapshot=QdrantSnapshotBinding(
            name="specpilot.snapshot", checksum="3" * 64, size_bytes=4096
        ),
        created_at=datetime(2026, 8, 9, 11, tzinfo=UTC),
    )
    values.update(changes)
    return CorpusManifestDraft(**values)
