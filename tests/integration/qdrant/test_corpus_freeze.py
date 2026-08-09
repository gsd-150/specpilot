from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from specpilot.contracts.corpus_manifest import CorpusManifest
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import derived_corpus_sha256
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
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.manifests.corpus_store import (
    CollectionFrozenError,
    CorpusManifestStore,
)
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.dense import (
    VECTOR_SIZE,
    DenseIndex,
    DenseIndexWriter,
    DensePoint,
    DenseRecord,
    collection_name,
    point_id_for_unit,
    point_payload,
)
from specpilot.retrieval.local import LocalCorpus
from tests.helpers import rfc_factory

pytestmark = pytest.mark.integration


@dataclass(slots=True)
class RealFreeze:
    collection: str
    manifest: CorpusManifest
    snapshot_name: str
    verify_request: VerifyCorpusRequest
    source_store: ManifestStore
    corpus_store: CorpusManifestStore
    first_point: DenseRecord
    admin: QdrantClient


@dataclass(slots=True)
class SyntheticCorpus:
    xml_path: Path
    source_store: ManifestStore
    source_manifest_id: str
    corpus: LocalCorpus
    collection: str
    points: tuple[DensePoint, ...]


def _unit_vector(seed: int) -> tuple[float, ...]:
    raw = [math.sin((seed + 1) * (index + 1)) for index in range(VECTOR_SIZE)]
    norm = math.sqrt(sum(value * value for value in raw))
    return tuple(value / norm for value in raw)


def _synthetic_corpus(root: Path, number: str) -> SyntheticCorpus:
    root.mkdir(mode=0o700, exist_ok=True)
    xml_bytes = rfc_factory.QA_RFC_XML.replace(
        'number="9999"', f'number="{number}"'
    ).encode()
    xml_path = rfc_factory.write(root, f"rfc{number}.xml", xml_bytes)
    source_store = ManifestStore(root / "source-manifests")
    source_manifest = source_store.create_source_v2(
        RfcSourceManifestDraft(
            document_id=f"ietf-rfc-{number}",
            document_version="2026-08",
            text_url=f"https://example.test/rfc{number}.txt",
            xml_url=f"https://example.test/rfc{number}.xml",
            text_sha256="f" * 64,
            xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
            downloaded_at=datetime(2026, 8, 10, tzinfo=UTC),
            created_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
        )
    )
    document = load_verified_rfc(xml_path, RfcLimits())
    corpus = LocalCorpus.load(
        [(document, ClauseLimits(excluded_sections=EXCLUDED_SECTIONS))],
        RfcLimits(),
        IndexTextPolicy(),
    )
    derived_hash = derived_corpus_sha256(corpus.units())
    name = collection_name(
        derived_hash,
        PIPELINE_VERSION,
        IndexTextPolicy().version,
    )
    return SyntheticCorpus(
        xml_path=xml_path,
        source_store=source_store,
        source_manifest_id=source_manifest.manifest_id,
        corpus=corpus,
        collection=name,
        points=tuple(
            DensePoint(unit.unit_id, _unit_vector(index), point_payload(unit))
            for index, unit in enumerate(corpus.units())
        ),
    )


@contextmanager
def _real_freeze_context(
    root: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    number: str,
) -> Iterator[RealFreeze]:
    synthetic = _synthetic_corpus(root, number)
    name = synthetic.collection
    corpus_store = CorpusManifestStore(root / "corpus-manifests")
    admin = QdrantClient(url=qdrant_url, trust_env=False)
    owned = False
    try:
        with corpus_store.acquire_write_lease(name) as write_lease:
            writer = DenseIndexWriter.create(
                qdrant_url,
                name,
                corpus_store,
                write_lease,
            )
            owned = True
            with writer:
                writer.upsert(synthetic.points)

        monkeypatch.setattr(
            "specpilot.corpus.freezing.load_token_counter",
            lambda path: lambda text: len(text.split()),
        )
        monkeypatch.setattr(
            "specpilot.corpus.freezing.weights_sha256",
            lambda path: "a" * 64,
        )
        sources = (
            CorpusSourceInput(
                synthetic.source_manifest_id,
                synthetic.xml_path,
            ),
        )
        result = freeze_corpus(
            FreezeCorpusRequest(
                sources=sources,
                model_dir=root / "model",
                qdrant_url=qdrant_url,
                collection_name=name,
                predecessor_manifest_id=None,
                created_at=datetime(2026, 8, 10, 2, tzinfo=UTC),
            ),
            source_store=synthetic.source_store,
            corpus_store=corpus_store,
        )
        expected_first = synthetic.points[0]
        expected_point_id = point_id_for_unit(expected_first.unit_id)
        with DenseIndex.open(qdrant_url, name) as reader:
            first = next(
                record
                for record in reader.iter_records()
                if record.point_id == expected_point_id
            )
        assert first.point_id == expected_point_id
        assert first.payload == expected_first.payload
        yield RealFreeze(
            collection=name,
            manifest=result.manifest,
            snapshot_name=result.manifest.snapshot.name,
            verify_request=VerifyCorpusRequest(
                manifest_id=result.manifest.manifest_id,
                sources=sources,
                model_dir=root / "model",
                qdrant_url=qdrant_url,
            ),
            source_store=synthetic.source_store,
            corpus_store=corpus_store,
            first_point=first,
            admin=admin,
        )
    finally:
        try:
            if owned and admin.collection_exists(name):
                for snapshot in admin.list_snapshots(collection_name=name):
                    admin.delete_snapshot(
                        collection_name=name,
                        snapshot_name=snapshot.name,
                        wait=True,
                    )
                admin.delete_collection(name)
        finally:
            admin.close()


@pytest.fixture
def real_freeze(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RealFreeze]:
    number = str(900_000 + uuid4().int % 100_000_000)
    with _real_freeze_context(
        tmp_path,
        qdrant_url,
        monkeypatch,
        number=number,
    ) as frozen:
        yield frozen


def _stored_record(frozen: RealFreeze, point_id: int | str) -> DenseRecord:
    with DenseIndex.open(
        frozen.verify_request.qdrant_url,
        frozen.collection,
    ) as reader:
        return next(
            record for record in reader.iter_records() if record.point_id == point_id
        )


def _assert_verify_refuses(real_freeze: RealFreeze, code: str) -> None:
    with pytest.raises(CorpusManifestRefusal) as raised:
        verify_corpus(
            real_freeze.verify_request,
            source_store=real_freeze.source_store,
            corpus_store=real_freeze.corpus_store,
        )
    assert raised.value.code == code


def test_frozen_corpus_verifies_and_permanently_revokes_writers(
    real_freeze: RealFreeze,
) -> None:
    verified = verify_corpus(
        real_freeze.verify_request,
        source_store=real_freeze.source_store,
        corpus_store=real_freeze.corpus_store,
    )
    try:
        assert verified.manifest == real_freeze.manifest
    finally:
        verified.close()

    with pytest.raises(CollectionFrozenError):
        real_freeze.corpus_store.acquire_write_lease(real_freeze.collection)


def test_freeze_fixture_uses_the_record_actually_stored_by_qdrant(
    real_freeze: RealFreeze,
) -> None:
    assert _stored_record(
        real_freeze,
        real_freeze.first_point.point_id,
    ) == real_freeze.first_point


def test_create_conflict_never_cleans_up_an_existing_collection(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    number = str(900_000 + uuid4().int % 100_000_000)
    existing = _synthetic_corpus(tmp_path / "existing", number)
    owner_store = CorpusManifestStore(tmp_path / "owner-store")
    admin = QdrantClient(url=qdrant_url, trust_env=False)
    try:
        with owner_store.acquire_write_lease(
            existing.collection
        ) as lease, DenseIndexWriter.create(
            qdrant_url,
            existing.collection,
            owner_store,
            lease,
        ) as writer:
            writer.upsert((existing.points[0],))
        snapshot = admin.create_snapshot(
            collection_name=existing.collection,
            wait=True,
        )

        with pytest.raises(FileExistsError), _real_freeze_context(
            tmp_path / "conflicting-attempt",
            qdrant_url,
            monkeypatch,
            number=number,
        ):
            pass

        assert admin.collection_exists(existing.collection)
        assert admin.count(existing.collection, exact=True).count == 1
        assert snapshot.name in {
            item.name
            for item in admin.list_snapshots(collection_name=existing.collection)
        }
    finally:
        try:
            if admin.collection_exists(existing.collection):
                for item in admin.list_snapshots(
                    collection_name=existing.collection
                ):
                    admin.delete_snapshot(
                        collection_name=existing.collection,
                        snapshot_name=item.name,
                        wait=True,
                    )
                admin.delete_collection(existing.collection)
        finally:
            admin.close()


def test_payload_only_drift_is_detected(real_freeze: RealFreeze) -> None:
    point = real_freeze.first_point
    changed_payload = dict(point.payload)
    changed_payload["section_path"] = "Changed locator"
    real_freeze.admin.upsert(
        collection_name=real_freeze.collection,
        points=[
            PointStruct(
                id=point.point_id,
                vector=point.vector,
                payload=changed_payload,
            )
        ],
        wait=True,
    )
    stored = _stored_record(real_freeze, point.point_id)
    assert stored.point_id == point.point_id
    assert stored.payload == changed_payload
    assert stored.vector == pytest.approx(point.vector, rel=0.0, abs=1e-7)

    _assert_verify_refuses(real_freeze, "dense_point_inventory_mismatch")


def test_vector_only_drift_is_detected(real_freeze: RealFreeze) -> None:
    point = real_freeze.first_point
    changed = list(point.vector)
    changed[0] = changed[0] + 0.125
    assert changed[0] != point.vector[0]
    real_freeze.admin.upsert(
        collection_name=real_freeze.collection,
        points=[
            PointStruct(
                id=point.point_id,
                vector=changed,
                payload=point.payload,
            )
        ],
        wait=True,
    )
    stored = _stored_record(real_freeze, point.point_id)
    assert stored.point_id == point.point_id
    assert stored.payload == point.payload
    assert stored.vector != pytest.approx(point.vector, rel=0.0, abs=1e-7)

    _assert_verify_refuses(real_freeze, "dense_point_inventory_mismatch")


def test_missing_snapshot_is_detected(real_freeze: RealFreeze) -> None:
    real_freeze.admin.delete_snapshot(
        collection_name=real_freeze.collection,
        snapshot_name=real_freeze.snapshot_name,
        wait=True,
    )

    _assert_verify_refuses(real_freeze, "corpus_snapshot_mismatch")
