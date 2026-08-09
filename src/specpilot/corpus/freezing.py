"""Seal and verify one exact local/RFC/Qdrant corpus state."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Never

from specpilot.contracts.corpus_manifest import (
    Bm25Binding,
    CorpusComponentVersions,
    CorpusManifest,
    CorpusManifestDraft,
    CorpusManifestIntent,
    ParseQaEvidence,
    QdrantCollectionSchema,
    QdrantSnapshotBinding,
    RetrievalProtocolBinding,
)
from specpilot.contracts.manifests import RfcSourceManifest
from specpilot.contracts.rfc import RfcLimits, UnsafeRfcError
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import (
    build_dense_inventory,
    derived_corpus_sha256,
)
from specpilot.corpus.indexable import CHUNKER_VERSION, IndexTextPolicy
from specpilot.corpus.qa import QaThresholds, qa_evidence_sha256, run_parse_qa
from specpilot.corpus.walk import (
    RFCXML_PARSER_VERSION,
    InvalidDocumentIdentityError,
    document_identity,
)
from specpilot.embedding.local_encoder import load_token_counter
from specpilot.embedding.throughput import PIPELINE_VERSION, weights_sha256
from specpilot.ingestion.rfc import VerifiedRfc, read_rfc_snapshot, verify_rfc_snapshot
from specpilot.manifests.corpus_store import (
    CorpusManifestStore,
    CorpusPredecessorError,
    UnsupportedCorpusManifestVersionError,
)
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.bm25 import TOKENIZER_VERSION, Bm25Index
from specpilot.retrieval.dense import (
    BASELINE_DENSE_QUERY,
    DenseIndex,
    DenseSnapshot,
    DenseSnapshotAdmin,
)
from specpilot.retrieval.dense import (
    collection_name as derive_collection_name,
)
from specpilot.retrieval.hybrid import RrfParameters
from specpilot.retrieval.local import LocalCorpus


@dataclass(frozen=True, slots=True)
class CorpusSourceInput:
    manifest_id: str
    xml_path: Path


@dataclass(frozen=True, slots=True)
class FreezeCorpusRequest:
    sources: tuple[CorpusSourceInput, ...]
    model_dir: Path
    qdrant_url: str
    collection_name: str
    predecessor_manifest_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VerifyCorpusRequest:
    manifest_id: str
    sources: tuple[CorpusSourceInput, ...]
    model_dir: Path
    qdrant_url: str


@dataclass(frozen=True, slots=True)
class FreezeResult:
    manifest: CorpusManifest
    replayed: bool


@dataclass(slots=True)
class VerifiedCorpus:
    manifest: CorpusManifest
    corpus: LocalCorpus
    bm25: Bm25Index
    dense: DenseIndex

    def close(self) -> None:
        self.dense.close()


type CorpusRefusalCode = Literal[
    "unsupported_corpus_manifest_version",
    "corpus_source_mismatch",
    "corpus_qa_mismatch",
    "corpus_model_mismatch",
    "corpus_configuration_mismatch",
    "dense_collection_name_mismatch",
    "dense_collection_schema_mismatch",
    "dense_point_count_mismatch",
    "dense_point_inventory_mismatch",
    "corpus_snapshot_mismatch",
    "corpus_predecessor_mismatch",
    "collection_changed_during_freeze",
]


class CorpusManifestRefusal(ValueError):
    def __init__(self, code: CorpusRefusalCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PreparedCorpus:
    source_manifest_ids: tuple[str, ...]
    parse_qa: tuple[ParseQaEvidence, ...]
    embedding_weights_sha256: str
    corpus: LocalCorpus
    bm25: Bm25Index
    derived_corpus_sha256: str
    versions: CorpusComponentVersions


@dataclass(frozen=True, slots=True)
class DenseObservation:
    collection_schema: QdrantCollectionSchema
    point_count: int
    inventory_root_sha256: str


def _current_versions() -> CorpusComponentVersions:
    return CorpusComponentVersions(
        parser=RFCXML_PARSER_VERSION,
        chunker=CHUNKER_VERSION,
        index_text=IndexTextPolicy().version,
        embedding_pipeline=PIPELINE_VERSION,
    )


def _refuse(code: CorpusRefusalCode) -> Never:
    raise CorpusManifestRefusal(code)


def _prepare(
    *,
    sources: tuple[CorpusSourceInput, ...],
    model_dir: Path,
    expected_collection_name: str,
    source_store: ManifestStore,
) -> PreparedCorpus:
    if not sources or len({item.manifest_id for item in sources}) != len(sources):
        _refuse("corpus_source_mismatch")

    resolved: list[tuple[RfcSourceManifest, VerifiedRfc]] = []
    document_ids: set[str] = set()
    for source_input in sources:
        try:
            manifest = source_store.read_source(source_input.manifest_id)
            if type(manifest) is not RfcSourceManifest:
                _refuse("corpus_source_mismatch")
            source_manifest = manifest
            snapshot = read_rfc_snapshot(source_input.xml_path, RfcLimits())
            if snapshot.document_sha256 != source_manifest.xml_sha256:
                _refuse("corpus_source_mismatch")
            document = verify_rfc_snapshot(snapshot)
            identity = document_identity(document.root)
            if identity != (
                source_manifest.document_id,
                source_manifest.document_version,
            ):
                _refuse("corpus_source_mismatch")
        except CorpusManifestRefusal:
            raise
        except (
            FileNotFoundError,
            UnsafeRfcError,
            InvalidDocumentIdentityError,
            ValueError,
        ):
            _refuse("corpus_source_mismatch")
        if source_manifest.document_id in document_ids:
            _refuse("corpus_source_mismatch")
        document_ids.add(source_manifest.document_id)
        resolved.append((source_manifest, document))

    resolved.sort(
        key=lambda item: (
            item[0].document_id,
            item[0].document_version,
            item[0].manifest_id,
        )
    )
    counter = load_token_counter(model_dir)
    weight_hash = weights_sha256(model_dir)
    clause_limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
    source_manifest_ids: list[str] = []
    evidence: list[ParseQaEvidence] = []
    documents = []
    for manifest, document in resolved:
        try:
            report = run_parse_qa(
                document,
                RfcLimits(),
                clause_limits,
                QaThresholds(),
                count_tokens=counter,
            )
            digest = qa_evidence_sha256(manifest.manifest_id, report)
        except ValueError:
            _refuse("corpus_qa_mismatch")
        source_manifest_ids.append(manifest.manifest_id)
        evidence.append(
            ParseQaEvidence(
                source_manifest_id=manifest.manifest_id,
                evidence_sha256=digest,
            )
        )
        documents.append((document, clause_limits))

    try:
        corpus = LocalCorpus.load(documents, RfcLimits(), IndexTextPolicy())
        bm25 = Bm25Index.build(corpus.indexable())
        derived_hash = derived_corpus_sha256(corpus.units())
    except ValueError:
        _refuse("corpus_source_mismatch")
    derived_name = derive_collection_name(
        derived_hash,
        PIPELINE_VERSION,
        IndexTextPolicy().version,
    )
    if derived_name != expected_collection_name:
        _refuse("dense_collection_name_mismatch")
    return PreparedCorpus(
        source_manifest_ids=tuple(source_manifest_ids),
        parse_qa=tuple(evidence),
        embedding_weights_sha256=weight_hash,
        corpus=corpus,
        bm25=bm25,
        derived_corpus_sha256=derived_hash,
        versions=_current_versions(),
    )


def _observe(prepared: PreparedCorpus, reader: DenseIndex) -> DenseObservation:
    try:
        schema = reader.collection_schema()
        if schema.dense_vector.size != 1024 or schema.sparse_vectors:
            _refuse("dense_collection_schema_mismatch")
    except CorpusManifestRefusal:
        raise
    except ValueError:
        _refuse("dense_collection_schema_mismatch")
    point_count = reader.point_count()
    if point_count != prepared.corpus.unit_count():
        _refuse("dense_point_count_mismatch")
    try:
        inventory = build_dense_inventory(
            prepared.corpus.units(), reader.iter_records()
        )
    except ValueError:
        _refuse("dense_point_inventory_mismatch")
    if inventory.point_count != point_count:
        _refuse("dense_point_count_mismatch")
    return DenseObservation(
        collection_schema=schema,
        point_count=point_count,
        inventory_root_sha256=inventory.inventory_root_sha256,
    )


def _retrieval_binding() -> RetrievalProtocolBinding:
    return RetrievalProtocolBinding(
        dense_top_k=20,
        bm25_top_k=20,
        rrf_k=RrfParameters().k,
        final_top_k=5,
        deduplication_key=(
            "corpus_manifest_id",
            "document_id",
            "clause_id",
            "child_span",
        ),
        stable_tie_key=("document_id", "numeric_clause_path", "child_start"),
        dense_query=BASELINE_DENSE_QUERY,
    )


def _intent(
    prepared: PreparedCorpus,
    observation: DenseObservation,
    collection_name: str,
    predecessor_manifest_id: str | None,
) -> CorpusManifestIntent:
    parameters = prepared.bm25.parameters
    return CorpusManifestIntent(
        predecessor_manifest_id=predecessor_manifest_id,
        source_manifest_ids=prepared.source_manifest_ids,
        versions=prepared.versions,
        embedding_weights_sha256=prepared.embedding_weights_sha256,
        bm25=Bm25Binding(
            tokenizer_version=TOKENIZER_VERSION,
            k1=parameters.k1,
            b=parameters.b,
            index_fingerprint=prepared.bm25.fingerprint,
        ),
        retrieval=_retrieval_binding(),
        collection_name=collection_name,
        collection_schema=observation.collection_schema,
        point_count=observation.point_count,
        derived_corpus_sha256=prepared.derived_corpus_sha256,
        inventory_root_sha256=observation.inventory_root_sha256,
        parse_qa=prepared.parse_qa,
    )


def _snapshot_binding(snapshot: DenseSnapshot) -> QdrantSnapshotBinding:
    return QdrantSnapshotBinding(
        name=snapshot.name,
        checksum=snapshot.checksum,
        size_bytes=snapshot.size_bytes,
    )


def _require_snapshot(
    expected: QdrantSnapshotBinding,
    snapshots: tuple[DenseSnapshot, ...],
) -> None:
    matches = tuple(item for item in snapshots if item.name == expected.name)
    if len(matches) != 1 or _snapshot_binding(matches[0]) != expected:
        _refuse("corpus_snapshot_mismatch")


def freeze_corpus(
    request: FreezeCorpusRequest,
    *,
    source_store: ManifestStore,
    corpus_store: CorpusManifestStore,
) -> FreezeResult:
    with corpus_store.acquire_freeze_lease(request.collection_name) as lease:
        prepared = _prepare(
            sources=request.sources,
            model_dir=request.model_dir,
            expected_collection_name=request.collection_name,
            source_store=source_store,
        )
        with DenseIndex.open(request.qdrant_url, request.collection_name) as reader:
            before = _observe(prepared, reader)
            intent = _intent(
                prepared,
                before,
                request.collection_name,
                request.predecessor_manifest_id,
            )
            try:
                existing = corpus_store.find_by_intent(intent, lease=lease)
                if existing is not None:
                    _require_snapshot(existing.snapshot, reader.snapshots())
                    return FreezeResult(existing, replayed=True)
                corpus_store.require_publishable_intent(intent, lease=lease)
            except CorpusPredecessorError:
                _refuse("corpus_predecessor_mismatch")
            with DenseSnapshotAdmin.open(
                request.qdrant_url,
                request.collection_name,
                corpus_store,
                lease,
            ) as admin:
                snapshot = admin.create_snapshot()
            after = _observe(prepared, reader)
            if after != before:
                _refuse("collection_changed_during_freeze")
            try:
                manifest = corpus_store.create(
                    CorpusManifestDraft(
                        **intent.model_dump(),
                        snapshot=_snapshot_binding(snapshot),
                        created_at=request.created_at,
                    ),
                    lease=lease,
                )
            except CorpusPredecessorError:
                _refuse("corpus_predecessor_mismatch")
            return FreezeResult(manifest, replayed=False)


def _read_manifest(
    corpus_store: CorpusManifestStore,
    manifest_id: str,
) -> CorpusManifest:
    try:
        return corpus_store.read(manifest_id)
    except UnsupportedCorpusManifestVersionError:
        _refuse("unsupported_corpus_manifest_version")


def verify_corpus(
    request: VerifyCorpusRequest,
    *,
    source_store: ManifestStore,
    corpus_store: CorpusManifestStore,
) -> VerifiedCorpus:
    manifest = _read_manifest(corpus_store, request.manifest_id)
    requested_ids = tuple(item.manifest_id for item in request.sources)
    if len(set(requested_ids)) != len(requested_ids) or set(requested_ids) != set(
        manifest.source_manifest_ids
    ):
        _refuse("corpus_source_mismatch")
    if _current_versions() != manifest.versions:
        _refuse("corpus_configuration_mismatch")
    prepared = _prepare(
        sources=request.sources,
        model_dir=request.model_dir,
        expected_collection_name=manifest.collection_name,
        source_store=source_store,
    )
    if prepared.source_manifest_ids != manifest.source_manifest_ids:
        _refuse("corpus_source_mismatch")
    if prepared.parse_qa != manifest.parse_qa:
        _refuse("corpus_qa_mismatch")
    if prepared.embedding_weights_sha256 != manifest.embedding_weights_sha256:
        _refuse("corpus_model_mismatch")
    expected_bm25 = Bm25Binding(
        tokenizer_version=TOKENIZER_VERSION,
        k1=prepared.bm25.parameters.k1,
        b=prepared.bm25.parameters.b,
        index_fingerprint=prepared.bm25.fingerprint,
    )
    if (
        prepared.versions != manifest.versions
        or expected_bm25 != manifest.bm25
        or _retrieval_binding() != manifest.retrieval
        or prepared.derived_corpus_sha256 != manifest.derived_corpus_sha256
    ):
        _refuse("corpus_configuration_mismatch")

    reader = DenseIndex.open(request.qdrant_url, manifest.collection_name)
    try:
        _require_snapshot(manifest.snapshot, reader.snapshots())
        observation = _observe(prepared, reader)
        if observation.collection_schema != manifest.collection_schema:
            _refuse("dense_collection_schema_mismatch")
        if observation.point_count != manifest.point_count:
            _refuse("dense_point_count_mismatch")
        if observation.inventory_root_sha256 != manifest.inventory_root_sha256:
            _refuse("dense_point_inventory_mismatch")
        reconstructed = _intent(
            prepared,
            observation,
            manifest.collection_name,
            manifest.predecessor_manifest_id,
        )
        if reconstructed.predecessor_manifest_id != manifest.predecessor_manifest_id:
            _refuse("corpus_predecessor_mismatch")
        if reconstructed != manifest.intent:
            _refuse("corpus_configuration_mismatch")
    except BaseException:
        with suppress(Exception):
            reader.close()
        raise
    return VerifiedCorpus(manifest, prepared.corpus, prepared.bm25, reader)
