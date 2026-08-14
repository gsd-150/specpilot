from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from specpilot.deployment.initialize import (
    FixtureInitializationRequest,
    InitializationRefusal,
    RealInitializationRequest,
    initialize_fixture,
    initialize_real,
)
from specpilot.deployment.ready import ReadyMarker, ReadyMarkerStore
from specpilot.manifests.corpus_store import CollectionFrozenError, CorpusManifestStore
from specpilot.retrieval.dense import DenseIndexWriter
from tests.integration.qdrant.test_corpus_freeze import (
    _real_freeze_context,
    _synthetic_corpus,
)

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def fixture_request(
    tmp_path: Path, qdrant_url: str
) -> Iterator[FixtureInitializationRequest]:
    request = FixtureInitializationRequest(
        fixture_dir=_ROOT / "fixtures" / "demo",
        source_manifest_dir=tmp_path / "source-manifests",
        corpus_manifest_dir=tmp_path / "corpus-manifests",
        ready_dir=tmp_path / "ready",
        qdrant_url=qdrant_url,
    )
    marker = None
    try:
        yield request
        try:
            marker = ReadyMarkerStore(request.ready_dir).read()
        except (FileNotFoundError, ValueError):
            marker = None
    finally:
        if marker is not None:
            admin = QdrantClient(url=qdrant_url, trust_env=False)
            try:
                if admin.collection_exists(marker.collection_name):
                    for snapshot in admin.list_snapshots(marker.collection_name):
                        admin.delete_snapshot(
                            collection_name=marker.collection_name,
                            snapshot_name=snapshot.name,
                            wait=True,
                        )
                    admin.delete_collection(marker.collection_name)
            finally:
                admin.close()


def test_fixture_initializes_exact_collection_and_replays_without_upsert(
    fixture_request: FixtureInitializationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = initialize_fixture(fixture_request)

    def reject_upsert(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("identical initialization must not upsert")

    monkeypatch.setattr(DenseIndexWriter, "upsert", reject_upsert)
    second = initialize_fixture(fixture_request)

    assert second == first
    assert first.mode == "fixture"
    assert ReadyMarkerStore(fixture_request.ready_dir).read() == first


def test_fixture_refuses_partial_existing_collection_without_repair(
    fixture_request: FixtureInitializationRequest,
) -> None:
    marker = initialize_fixture(fixture_request)
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        points, _ = admin.scroll(
            marker.collection_name,
            limit=1,
            with_payload=False,
            with_vectors=False,
        )
        admin.delete(
            marker.collection_name,
            points_selector=[points[0].id],
            wait=True,
        )
    finally:
        admin.close()

    with pytest.raises(InitializationRefusal) as raised:
        initialize_fixture(fixture_request)

    assert raised.value.code == "dense_point_count_mismatch"


def test_fixture_refuses_same_size_wrong_inventory(
    fixture_request: FixtureInitializationRequest,
) -> None:
    marker = initialize_fixture(fixture_request)
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        points, _ = admin.scroll(
            marker.collection_name,
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        changed = dict(points[0].payload or {})
        changed["section_path"] = "Changed synthetic locator"
        admin.overwrite_payload(
            collection_name=marker.collection_name,
            payload=changed,
            points=[points[0].id],
            wait=True,
        )
    finally:
        admin.close()

    with pytest.raises(InitializationRefusal) as raised:
        initialize_fixture(fixture_request)

    assert raised.value.code == "dense_point_inventory_mismatch"


def test_fixture_ready_marker_refuses_a_stale_mode_before_qdrant(
    fixture_request: FixtureInitializationRequest,
) -> None:
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    stale = ReadyMarker.create(
        source_manifest_ids=(fixture["source"]["manifest_id"],),
        corpus_manifest_id="f" * 64,
        collection_name=fixture["collection_name"],
        point_count=fixture["dense_points"]["point_count"],
        inventory_root_sha256=fixture["inventory_root_sha256"],
        mode="real",
    )
    ReadyMarkerStore(fixture_request.ready_dir).publish(stale)

    with pytest.raises(InitializationRefusal) as raised:
        initialize_fixture(fixture_request)

    assert raised.value.code == "ready_marker_mismatch"


def test_fixture_freeze_permanently_revokes_collection_writers(
    fixture_request: FixtureInitializationRequest,
) -> None:
    marker = initialize_fixture(fixture_request)

    with pytest.raises(CollectionFrozenError):
        CorpusManifestStore(fixture_request.corpus_manifest_dir).acquire_write_lease(
            marker.collection_name
        )


def test_fixture_refuses_wrong_existing_schema_without_recreating(
    fixture_request: FixtureInitializationRequest,
) -> None:
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    collection = fixture["collection_name"]
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        admin.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=2, distance=Distance.COSINE),
        )
        with pytest.raises(InitializationRefusal) as raised:
            initialize_fixture(fixture_request)
        assert raised.value.code == "dense_collection_schema_mismatch"
        assert admin.get_collection(collection).config.params.vectors.size == 2
    finally:
        if admin.collection_exists(collection):
            admin.delete_collection(collection)
        admin.close()


def test_real_initialization_verifies_existing_freeze_and_writes_real_marker(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "real-corpus"
    with _real_freeze_context(
        root,
        qdrant_url,
        monkeypatch,
        number="990001",
    ) as frozen:
        sources = root / "sources"
        model = root / "model"
        sources.mkdir(mode=0o700)
        model.mkdir(mode=0o700)
        source_path = sources / f"{frozen.manifest.source_manifest_ids[0]}.xml"
        shutil.copyfile(frozen.verify_request.sources[0].xml_path, source_path)
        source_path.chmod(0o600)
        request = RealInitializationRequest(
            corpus_dir=root,
            ready_dir=tmp_path / "ready-real",
            qdrant_url=qdrant_url,
        )

        first = initialize_real(request)
        second = initialize_real(request)

        assert first == second
        assert first.mode == "real"
        assert first.corpus_manifest_id == frozen.manifest.manifest_id


def test_real_initialization_builds_only_the_derived_absent_collection(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "real-new"
    synthetic = _synthetic_corpus(root, "990002")
    (root / "corpus-manifests").mkdir(mode=0o700)
    sources = root / "sources"
    model = root / "model"
    sources.mkdir(mode=0o700)
    model.mkdir(mode=0o700)
    source_path = sources / f"{synthetic.source_manifest_id}.xml"
    shutil.copyfile(synthetic.xml_path, source_path)
    source_path.chmod(0o600)
    points_path = root / "dense-points.jsonl"
    points_path.write_text(
        "".join(
            json.dumps(
                {
                    "payload": point.payload,
                    "unit_id": point.unit_id,
                    "vector": point.vector,
                },
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for point in synthetic.points
        ),
        encoding="utf-8",
    )
    points_path.chmod(0o600)
    monkeypatch.setattr(
        "specpilot.corpus.freezing.load_token_counter",
        lambda path: lambda text: len(text.split()),
    )
    monkeypatch.setattr(
        "specpilot.corpus.freezing.weights_sha256",
        lambda path: "a" * 64,
    )
    request = RealInitializationRequest(
        corpus_dir=root,
        ready_dir=tmp_path / "ready-new-real",
        qdrant_url=qdrant_url,
    )
    admin = QdrantClient(url=qdrant_url, trust_env=False)
    try:
        marker = initialize_real(request)

        assert marker.mode == "real"
        assert marker.collection_name == synthetic.collection
        assert admin.collection_exists(synthetic.collection)
        assert admin.count(synthetic.collection, exact=True).count == len(
            synthetic.points
        )
    finally:
        if admin.collection_exists(synthetic.collection):
            for snapshot in admin.list_snapshots(synthetic.collection):
                admin.delete_snapshot(
                    collection_name=synthetic.collection,
                    snapshot_name=snapshot.name,
                    wait=True,
                )
            admin.delete_collection(synthetic.collection)
        admin.close()
