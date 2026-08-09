from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
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


def _unit_vector(seed: int) -> tuple[float, ...]:
    raw = [math.sin((seed + 1) * (index + 1)) for index in range(VECTOR_SIZE)]
    norm = math.sqrt(sum(value * value for value in raw))
    return tuple(value / norm for value in raw)


@pytest.fixture
def real_freeze(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RealFreeze]:
    number = str(900_000 + uuid4().int % 100_000_000)
    xml_bytes = rfc_factory.QA_RFC_XML.replace(
        'number="9999"', f'number="{number}"'
    ).encode()
    xml_path = rfc_factory.write(tmp_path, f"rfc{number}.xml", xml_bytes)

    source_store = ManifestStore(tmp_path / "source-manifests")
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
    corpus_store = CorpusManifestStore(tmp_path / "corpus-manifests")
    points = tuple(
        DensePoint(unit.unit_id, _unit_vector(index), point_payload(unit))
        for index, unit in enumerate(corpus.units())
    )
    admin = QdrantClient(url=qdrant_url, trust_env=False)
    try:
        with corpus_store.acquire_write_lease(
            name
        ) as write_lease, DenseIndexWriter.create(
            qdrant_url,
            name,
            corpus_store,
            write_lease,
        ) as writer:
            writer.upsert(points)

        monkeypatch.setattr(
            "specpilot.corpus.freezing.load_token_counter",
            lambda path: lambda text: len(text.split()),
        )
        monkeypatch.setattr(
            "specpilot.corpus.freezing.weights_sha256",
            lambda path: "a" * 64,
        )
        sources = (CorpusSourceInput(source_manifest.manifest_id, xml_path),)
        result = freeze_corpus(
            FreezeCorpusRequest(
                sources=sources,
                model_dir=tmp_path / "model",
                qdrant_url=qdrant_url,
                collection_name=name,
                predecessor_manifest_id=None,
                created_at=datetime(2026, 8, 10, 2, tzinfo=UTC),
            ),
            source_store=source_store,
            corpus_store=corpus_store,
        )
        first = points[0]
        yield RealFreeze(
            collection=name,
            manifest=result.manifest,
            snapshot_name=result.manifest.snapshot.name,
            verify_request=VerifyCorpusRequest(
                manifest_id=result.manifest.manifest_id,
                sources=sources,
                model_dir=tmp_path / "model",
                qdrant_url=qdrant_url,
            ),
            source_store=source_store,
            corpus_store=corpus_store,
            first_point=DenseRecord(
                point_id_for_unit(first.unit_id),
                first.payload,
                first.vector,
            ),
            admin=admin,
        )
    finally:
        try:
            if admin.collection_exists(name):
                for snapshot in admin.list_snapshots(collection_name=name):
                    admin.delete_snapshot(
                        collection_name=name,
                        snapshot_name=snapshot.name,
                        wait=True,
                    )
                admin.delete_collection(name)
        finally:
            admin.close()


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

    _assert_verify_refuses(real_freeze, "dense_point_inventory_mismatch")


def test_vector_only_drift_is_detected(real_freeze: RealFreeze) -> None:
    point = real_freeze.first_point
    changed = list(point.vector)
    changed[0] = changed[0] + 0.125
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

    _assert_verify_refuses(real_freeze, "dense_point_inventory_mismatch")


def test_missing_snapshot_is_detected(real_freeze: RealFreeze) -> None:
    real_freeze.admin.delete_snapshot(
        collection_name=real_freeze.collection,
        snapshot_name=real_freeze.snapshot_name,
        wait=True,
    )

    _assert_verify_refuses(real_freeze, "corpus_snapshot_mismatch")
