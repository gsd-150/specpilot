from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from specpilot.contracts.corpus_manifest import QdrantCollectionSchema
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.freezing import (
    CorpusManifestRefusal,
    CorpusSourceInput,
    FreezeCorpusRequest,
    VerifyCorpusRequest,
    freeze_corpus,
    verify_corpus,
)
from specpilot.corpus.indexable import IndexTextPolicy
from specpilot.embedding.throughput import PIPELINE_VERSION
from specpilot.manifests.corpus_store import CorpusManifestStore, CorpusPredecessorError
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.dense import (
    DenseRecord,
    DenseSnapshot,
    collection_name,
    point_id_for_unit,
    point_payload,
)
from specpilot.retrieval.local import LocalCorpus
from tests.helpers.corpus_manifest_factory import corpus_intent


def _xml(number: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rfc version="3" number="{number}">'
        '<front><title>Test</title><date year="2022" month="June"/></front>'
        '<middle><section anchor="s1" numbered="true" pn="section-1">'
        '<name>Rules</name><t pn="section-1-1">A client MUST comply.</t>'
        "</section></middle></rfc>"
    ).encode()


class FakeDense:
    def __init__(
        self,
        schema: QdrantCollectionSchema,
        records: tuple[DenseRecord, ...],
    ) -> None:
        self.schema = schema
        self.records = records
        self.reported_count = len(records)
        self.snapshot_values: tuple[DenseSnapshot, ...] = ()
        self.snapshot_calls = 0
        self.after_snapshot: Callable[[], None] | None = None
        self.closed = 0
        self.calls: list[str] = []

    def collection_schema(self) -> QdrantCollectionSchema:
        self.calls.append("schema")
        return self.schema

    def point_count(self) -> int:
        self.calls.append("count")
        return self.reported_count

    def iter_records(self, *, batch_size: int = 256) -> Iterator[DenseRecord]:
        del batch_size
        self.calls.append("records")
        yield from self.records

    def snapshots(self) -> tuple[DenseSnapshot, ...]:
        self.calls.append("snapshots")
        return self.snapshot_values

    def create_snapshot(self) -> DenseSnapshot:
        self.snapshot_calls += 1
        snapshot = DenseSnapshot(
            f"fake-{self.snapshot_calls}.snapshot",
            format(self.snapshot_calls, "064x"),
            4096 + self.snapshot_calls,
        )
        self.snapshot_values = (*self.snapshot_values, snapshot)
        if self.after_snapshot is not None:
            self.after_snapshot()
        return snapshot

    def close(self) -> None:
        self.closed += 1

    def __enter__(self) -> FakeDense:
        return self

    def __exit__(self, *ignored: object) -> None:
        del ignored


@dataclass
class FreezeFixture:
    request: FreezeCorpusRequest
    source_store: ManifestStore
    corpus_store: CorpusManifestStore
    corpus_root: Path
    dense: FakeDense


@pytest.fixture
def freeze_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FreezeFixture:
    source_store = ManifestStore(tmp_path / "sources")
    inputs: list[CorpusSourceInput] = []
    verified_documents = []
    for number in ("9999", "9998"):
        xml_path = tmp_path / f"rfc{number}.xml"
        data = _xml(number)
        xml_path.write_bytes(data)
        manifest = source_store.create_source_v2(
            RfcSourceManifestDraft(
                document_id=f"ietf-rfc-{number}",
                document_version="2022-06",
                text_url=f"https://example.test/rfc{number}.txt",
                xml_url=f"https://example.test/rfc{number}.xml",
                text_sha256="f" * 64,
                xml_sha256=hashlib.sha256(data).hexdigest(),
                downloaded_at=datetime(2026, 8, 9, tzinfo=UTC),
                created_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
            )
        )
        inputs.append(CorpusSourceInput(manifest.manifest_id, xml_path))
        from specpilot.ingestion.rfc import load_verified_rfc

        verified_documents.append(
            (manifest.document_id, load_verified_rfc(xml_path, RfcLimits()))
        )

    # Canonical document order is 9998, 9999; request order intentionally is not.
    verified_documents.sort(key=lambda item: item[0])
    limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
    corpus = LocalCorpus.load(
        [(document, limits) for _, document in verified_documents], RfcLimits()
    )
    records = tuple(
        DenseRecord(
            point_id_for_unit(unit.unit_id),
            point_payload(unit),
            (0.0,) * 1024,
        )
        for unit in corpus.units()
    )
    dense = FakeDense(corpus_intent().collection_schema, records)
    derived = __import__(
        "specpilot.corpus.dense_inventory", fromlist=["derived_corpus_sha256"]
    ).derived_corpus_sha256(corpus.units())
    name = collection_name(derived, PIPELINE_VERSION, IndexTextPolicy().version)
    request = FreezeCorpusRequest(
        sources=tuple(inputs),
        model_dir=tmp_path / "model",
        qdrant_url="http://qdrant.test:6333",
        collection_name=name,
        predecessor_manifest_id=None,
        created_at=datetime(2026, 8, 9, 2, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "specpilot.corpus.freezing.load_token_counter",
        lambda path: lambda text: len(text.split()),
    )
    monkeypatch.setattr(
        "specpilot.corpus.freezing.weights_sha256", lambda path: "c" * 64
    )
    monkeypatch.setattr(
        "specpilot.corpus.freezing.DenseIndex.open", lambda url, name: dense
    )
    monkeypatch.setattr(
        "specpilot.corpus.freezing.DenseSnapshotAdmin.open",
        lambda url, name, store, lease: dense,
    )
    corpus_root = tmp_path / "corpus"
    return FreezeFixture(
        request=request,
        source_store=source_store,
        corpus_store=CorpusManifestStore(corpus_root),
        corpus_root=corpus_root,
        dense=dense,
    )


def _freeze(fixture: FreezeFixture, request: FreezeCorpusRequest | None = None):
    return freeze_corpus(
        request or fixture.request,
        source_store=fixture.source_store,
        corpus_store=fixture.corpus_store,
    )


def _verify(fixture: FreezeFixture, manifest_id: str):
    return verify_corpus(
        VerifyCorpusRequest(
            manifest_id=manifest_id,
            sources=fixture.request.sources,
            model_dir=fixture.request.model_dir,
            qdrant_url=fixture.request.qdrant_url,
        ),
        source_store=fixture.source_store,
        corpus_store=fixture.corpus_store,
    )


def _assert_refusal(code: str, operation: Callable[[], object]) -> None:
    with pytest.raises(CorpusManifestRefusal) as caught:
        operation()
    assert caught.value.code == code


def test_freeze_canonicalizes_sources_and_verify_returns_read_only_corpus(
    freeze_fixture: FreezeFixture,
) -> None:
    result = _freeze(freeze_fixture)
    manifests = tuple(
        freeze_fixture.source_store.read_source(item.manifest_id)
        for item in freeze_fixture.request.sources
    )
    assert result.replayed is False
    assert result.manifest.source_manifest_ids == tuple(
        item.manifest_id
        for item in sorted(
            manifests,
            key=lambda item: (
                item.document_id,
                item.document_version,
                item.manifest_id,
            ),
        )
    )
    assert freeze_fixture.dense.snapshot_calls == 1

    verified = _verify(freeze_fixture, result.manifest.manifest_id)
    assert verified.manifest == result.manifest
    assert verified.corpus.unit_count() == 2
    assert verified.bm25.document_count == 2
    assert verified.dense is freeze_fixture.dense
    assert not hasattr(verified, "writer")
    verified.close()
    assert freeze_fixture.dense.closed == 1


def test_successful_replay_ignores_new_created_at_and_does_not_snapshot(
    freeze_fixture: FreezeFixture,
) -> None:
    first = _freeze(freeze_fixture)
    second = _freeze(
        freeze_fixture,
        replace(
            freeze_fixture.request,
            created_at=freeze_fixture.request.created_at + timedelta(hours=1),
        ),
    )
    assert second.replayed is True
    assert second.manifest == first.manifest
    assert freeze_fixture.dense.snapshot_calls == 1


def test_matching_intent_with_missing_snapshot_fails_without_new_snapshot(
    freeze_fixture: FreezeFixture,
) -> None:
    _freeze(freeze_fixture)
    freeze_fixture.dense.snapshot_values = ()
    _assert_refusal("corpus_snapshot_mismatch", lambda: _freeze(freeze_fixture))
    assert freeze_fixture.dense.snapshot_calls == 1


def test_missing_snapshot_can_only_be_replaced_by_explicit_successor(
    freeze_fixture: FreezeFixture,
) -> None:
    first = _freeze(freeze_fixture)
    freeze_fixture.dense.snapshot_values = ()
    successor = _freeze(
        freeze_fixture,
        replace(
            freeze_fixture.request,
            predecessor_manifest_id=first.manifest.manifest_id,
            created_at=freeze_fixture.request.created_at + timedelta(hours=1),
        ),
    )
    assert successor.replayed is False
    assert successor.manifest.predecessor_manifest_id == first.manifest.manifest_id
    assert successor.manifest.snapshot.name == "fake-2.snapshot"


def test_collection_name_is_derived_not_trusted(freeze_fixture: FreezeFixture) -> None:
    request = replace(freeze_fixture.request, collection_name="specpilot_wrong")
    _assert_refusal(
        "dense_collection_name_mismatch", lambda: _freeze(freeze_fixture, request)
    )
    assert freeze_fixture.dense.snapshot_calls == 0


def test_source_bytes_are_bound_before_parsing(freeze_fixture: FreezeFixture) -> None:
    freeze_fixture.request.sources[0].xml_path.write_bytes(_xml("9997"))
    _assert_refusal("corpus_source_mismatch", lambda: _freeze(freeze_fixture))


def test_duplicate_document_identity_is_refused(
    freeze_fixture: FreezeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = freeze_fixture.request.sources[0]
    first_manifest = freeze_fixture.source_store.read_source(first.manifest_id)
    duplicate_manifest = freeze_fixture.source_store.create_source_v2(
        RfcSourceManifestDraft(
            document_id=first_manifest.document_id,
            document_version=first_manifest.document_version,
            text_url="https://mirror.example.test/duplicate.txt",
            xml_url="https://mirror.example.test/duplicate.xml",
            text_sha256="e" * 64,
            xml_sha256=hashlib.sha256(first.xml_path.read_bytes()).hexdigest(),
            downloaded_at=datetime(2026, 8, 10, tzinfo=UTC),
            created_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
        )
    )
    duplicate = CorpusSourceInput(duplicate_manifest.manifest_id, first.xml_path)
    assert duplicate.manifest_id != first.manifest_id
    assert duplicate_manifest.document_id == first_manifest.document_id
    request = replace(freeze_fixture.request, sources=(first, duplicate))
    monkeypatch.setattr(
        "specpilot.corpus.freezing.LocalCorpus.load",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate identity reached local corpus construction")
        ),
    )
    _assert_refusal("corpus_source_mismatch", lambda: _freeze(freeze_fixture, request))


@pytest.mark.parametrize(
    ("document_id", "document_version"),
    [("ietf-rfc-7777", "2022-06"), ("ietf-rfc-9999", "2022-07")],
)
def test_source_manifest_identity_must_match_parsed_xml(
    freeze_fixture: FreezeFixture,
    document_id: str,
    document_version: str,
) -> None:
    source = freeze_fixture.request.sources[0]
    mismatch = freeze_fixture.source_store.create_source_v2(
        RfcSourceManifestDraft(
            document_id=document_id,
            document_version=document_version,
            text_url="https://example.test/mismatch.txt",
            xml_url="https://example.test/mismatch.xml",
            text_sha256="d" * 64,
            xml_sha256=hashlib.sha256(source.xml_path.read_bytes()).hexdigest(),
            downloaded_at=datetime(2026, 8, 11, tzinfo=UTC),
            created_at=datetime(2026, 8, 11, 1, tzinfo=UTC),
        )
    )
    request = replace(
        freeze_fixture.request,
        sources=(CorpusSourceInput(mismatch.manifest_id, source.xml_path),),
    )
    _assert_refusal("corpus_source_mismatch", lambda: _freeze(freeze_fixture, request))


def test_first_freeze_refuses_failed_qa(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "specpilot.corpus.freezing.load_token_counter", lambda path: None
    )
    _assert_refusal("corpus_qa_mismatch", lambda: _freeze(freeze_fixture))


def test_first_freeze_refuses_invalid_dense_schema(
    freeze_fixture: FreezeFixture,
) -> None:
    freeze_fixture.dense.schema = freeze_fixture.dense.schema.model_copy(
        update={
            "dense_vector": freeze_fixture.dense.schema.dense_vector.model_copy(
                update={"size": 512}
            )
        }
    )
    _assert_refusal("dense_collection_schema_mismatch", lambda: _freeze(freeze_fixture))


def test_first_freeze_refuses_count_and_inventory_disagreement(
    freeze_fixture: FreezeFixture,
) -> None:
    freeze_fixture.dense.reported_count += 1
    _assert_refusal("dense_point_count_mismatch", lambda: _freeze(freeze_fixture))


def test_first_freeze_refuses_inventory_mismatch(freeze_fixture: FreezeFixture) -> None:
    record = freeze_fixture.dense.records[0]
    payload = dict(record.payload)
    payload["section_path"] = "Wrong path"
    freeze_fixture.dense.records = (
        replace(record, payload=payload),
        *freeze_fixture.dense.records[1:],
    )
    _assert_refusal("dense_point_inventory_mismatch", lambda: _freeze(freeze_fixture))


def test_snapshot_boundary_drift_never_publishes(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drift() -> None:
        record = freeze_fixture.dense.records[0]
        freeze_fixture.dense.records = (
            replace(record, vector=(1.0, *record.vector[1:])),
            *freeze_fixture.dense.records[1:],
        )

    freeze_fixture.dense.after_snapshot = drift
    published = 0
    original = freeze_fixture.corpus_store.create

    def counted(*args: object, **kwargs: object):
        nonlocal published
        published += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(freeze_fixture.corpus_store, "create", counted)
    _assert_refusal("collection_changed_during_freeze", lambda: _freeze(freeze_fixture))
    assert published == 0


def test_wrong_predecessor_is_refused_before_snapshot(
    freeze_fixture: FreezeFixture,
) -> None:
    request = replace(freeze_fixture.request, predecessor_manifest_id="a" * 64)
    _assert_refusal(
        "corpus_predecessor_mismatch", lambda: _freeze(freeze_fixture, request)
    )
    assert freeze_fixture.dense.snapshot_calls == 0


def test_changed_collection_requires_explicit_successor(
    freeze_fixture: FreezeFixture,
) -> None:
    first = _freeze(freeze_fixture)
    record = freeze_fixture.dense.records[0]
    freeze_fixture.dense.records = (
        replace(record, vector=(1.0, *record.vector[1:])),
        *freeze_fixture.dense.records[1:],
    )
    _assert_refusal("corpus_predecessor_mismatch", lambda: _freeze(freeze_fixture))
    successor = _freeze(
        freeze_fixture,
        replace(
            freeze_fixture.request,
            predecessor_manifest_id=first.manifest.manifest_id,
            created_at=freeze_fixture.request.created_at + timedelta(hours=1),
        ),
    )
    assert successor.manifest.predecessor_manifest_id == first.manifest.manifest_id
    assert successor.manifest.manifest_id != first.manifest.manifest_id
    assert freeze_fixture.dense.snapshot_calls == 2


def test_verify_leaves_corrupt_predecessor_graph_as_raw_storage_error(
    freeze_fixture: FreezeFixture,
) -> None:
    first = _freeze(freeze_fixture)
    record = freeze_fixture.dense.records[0]
    freeze_fixture.dense.records = (
        replace(record, vector=(1.0, *record.vector[1:])),
        *freeze_fixture.dense.records[1:],
    )
    successor = _freeze(
        freeze_fixture,
        replace(
            freeze_fixture.request,
            predecessor_manifest_id=first.manifest.manifest_id,
            created_at=freeze_fixture.request.created_at + timedelta(hours=1),
        ),
    )
    (freeze_fixture.corpus_root / f"{first.manifest.manifest_id}.json").unlink()

    with pytest.raises(CorpusPredecessorError, match="does not exist") as caught:
        _verify(freeze_fixture, successor.manifest.manifest_id)
    assert not isinstance(caught.value, CorpusManifestRefusal)


def test_publication_crash_leaves_orphan_non_authoritative(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = freeze_fixture.corpus_store.create
    monkeypatch.setattr(
        freeze_fixture.corpus_store,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError):
        _freeze(freeze_fixture)
    assert freeze_fixture.dense.snapshot_calls == 1
    monkeypatch.setattr(freeze_fixture.corpus_store, "create", original)
    result = _freeze(freeze_fixture)
    assert result.manifest.snapshot.name == "fake-2.snapshot"
    assert freeze_fixture.dense.snapshot_calls == 2


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("schema", "dense_collection_schema_mismatch"),
        ("count", "dense_point_count_mismatch"),
        ("payload", "dense_point_inventory_mismatch"),
        ("vector", "dense_point_inventory_mismatch"),
        ("snapshot_missing", "corpus_snapshot_mismatch"),
        ("snapshot_checksum", "corpus_snapshot_mismatch"),
        ("snapshot_size", "corpus_snapshot_mismatch"),
    ],
)
def test_verify_maps_live_drift_to_narrow_code(
    freeze_fixture: FreezeFixture, mutation: str, code: str
) -> None:
    result = _freeze(freeze_fixture)
    record = freeze_fixture.dense.records[0]
    if mutation == "schema":
        freeze_fixture.dense.schema = freeze_fixture.dense.schema.model_copy(
            update={
                "hnsw": freeze_fixture.dense.schema.hnsw.model_copy(update={"m": 32})
            }
        )
    elif mutation == "count":
        freeze_fixture.dense.reported_count += 1
    elif mutation == "payload":
        payload = dict(record.payload)
        payload["section_path"] = "Changed"
        freeze_fixture.dense.records = (
            replace(record, payload=payload),
            *freeze_fixture.dense.records[1:],
        )
    elif mutation == "vector":
        freeze_fixture.dense.records = (
            replace(record, vector=(1.0, *record.vector[1:])),
            *freeze_fixture.dense.records[1:],
        )
    elif mutation == "snapshot_missing":
        freeze_fixture.dense.snapshot_values = ()
    else:
        snapshot = freeze_fixture.dense.snapshot_values[0]
        freeze_fixture.dense.snapshot_values = (
            replace(
                snapshot,
                checksum="f" * 64
                if mutation == "snapshot_checksum"
                else snapshot.checksum,
                size_bytes=999 if mutation == "snapshot_size" else snapshot.size_bytes,
            ),
        )
    _assert_refusal(code, lambda: _verify(freeze_fixture, result.manifest.manifest_id))


def test_verify_checks_snapshot_before_full_live_inventory(
    freeze_fixture: FreezeFixture,
) -> None:
    result = _freeze(freeze_fixture)
    freeze_fixture.dense.snapshot_values = ()
    freeze_fixture.dense.calls.clear()
    _assert_refusal(
        "corpus_snapshot_mismatch",
        lambda: _verify(freeze_fixture, result.manifest.manifest_id),
    )
    assert freeze_fixture.dense.calls == ["snapshots"]


@pytest.mark.parametrize(
    ("symbol", "replacement", "code"),
    [
        ("RFCXML_PARSER_VERSION", "parser/v2", "corpus_configuration_mismatch"),
        ("CHUNKER_VERSION", "chunker/v2", "corpus_configuration_mismatch"),
        ("TOKENIZER_VERSION", "tokenizer/v2", "corpus_configuration_mismatch"),
        ("PIPELINE_VERSION", "pipeline/v2", "corpus_configuration_mismatch"),
    ],
)
def test_verify_rejects_code_owned_version_drift(
    freeze_fixture: FreezeFixture,
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
    replacement: str,
    code: str,
) -> None:
    result = _freeze(freeze_fixture)
    monkeypatch.setattr(f"specpilot.corpus.freezing.{symbol}", replacement)
    _assert_refusal(code, lambda: _verify(freeze_fixture, result.manifest.manifest_id))


def test_verify_rejects_model_drift(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _freeze(freeze_fixture)
    monkeypatch.setattr(
        "specpilot.corpus.freezing.weights_sha256", lambda path: "d" * 64
    )
    _assert_refusal(
        "corpus_model_mismatch",
        lambda: _verify(freeze_fixture, result.manifest.manifest_id),
    )


def test_verify_rejects_qa_drift(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _freeze(freeze_fixture)
    monkeypatch.setattr(
        "specpilot.corpus.freezing.qa_evidence_sha256", lambda *args: "e" * 64
    )
    _assert_refusal(
        "corpus_qa_mismatch",
        lambda: _verify(freeze_fixture, result.manifest.manifest_id),
    )


def test_verify_rejects_bm25_fingerprint_drift(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _freeze(freeze_fixture)
    from specpilot.corpus import freezing

    original = freezing.Bm25Index.build
    monkeypatch.setattr(
        freezing.Bm25Index,
        "build",
        lambda units: replace(original(units), fingerprint="9" * 64),
    )
    _assert_refusal(
        "corpus_configuration_mismatch",
        lambda: _verify(freeze_fixture, result.manifest.manifest_id),
    )


def test_verify_rejects_retrieval_protocol_drift(
    freeze_fixture: FreezeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _freeze(freeze_fixture)
    from specpilot.corpus import freezing

    monkeypatch.setattr(
        freezing,
        "BASELINE_DENSE_QUERY",
        freezing.BASELINE_DENSE_QUERY.model_copy(update={"exact": True}),
    )
    _assert_refusal(
        "corpus_configuration_mismatch",
        lambda: _verify(freeze_fixture, result.manifest.manifest_id),
    )


def test_verify_rejects_source_id_set_before_opening_dense(
    freeze_fixture: FreezeFixture,
) -> None:
    result = _freeze(freeze_fixture)
    request = VerifyCorpusRequest(
        manifest_id=result.manifest.manifest_id,
        sources=(freeze_fixture.request.sources[0],),
        model_dir=freeze_fixture.request.model_dir,
        qdrant_url=freeze_fixture.request.qdrant_url,
    )
    _assert_refusal(
        "corpus_source_mismatch",
        lambda: verify_corpus(
            request,
            source_store=freeze_fixture.source_store,
            corpus_store=freeze_fixture.corpus_store,
        ),
    )
