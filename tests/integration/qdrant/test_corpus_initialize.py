from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from specpilot.contracts.manifests import ProviderRouteBinding, ProviderUse
from specpilot.deployment.initialize import (
    FixtureInitializationRequest,
    InitializationRefusal,
    RealInitializationRequest,
    initialize_fixture,
    initialize_real,
)
from specpilot.deployment.ready import ReadyMarker, ReadyMarkerStore
from specpilot.manifests.corpus_store import (
    CollectionFreezeLease,
    CollectionFrozenError,
    CorpusManifestStore,
)
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.dense import DenseIndexWriter
from tests.integration.qdrant.test_corpus_freeze import (
    SyntheticCorpus,
    _real_freeze_context,
    _synthetic_corpus,
)

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]


def _inject_post_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    if failure_point == "record_publish":
        original_create = CorpusManifestStore.create

        def create_then_fail(
            store: CorpusManifestStore,
            *args: object,
            **kwargs: object,
        ) -> None:
            original_create(store, *args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("injected after durable manifest publish")

        monkeypatch.setattr(CorpusManifestStore, "create", create_then_fail)
        return

    original_close = CollectionFreezeLease.close
    armed = True

    def close_then_fail(lease: CollectionFreezeLease) -> None:
        nonlocal armed
        original_close(lease)
        if armed:
            armed = False
            raise RuntimeError("injected freeze lease exit failure")

    monkeypatch.setattr(CollectionFreezeLease, "close", close_then_fail)


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
    source_id = first.source_manifest_ids[0]
    source = ManifestStore(fixture_request.source_manifest_dir).read_source(source_id)
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    route = ProviderRouteBinding(
        provider_id="fixture-provider",
        endpoint_purpose="fixture-smoke",
        use=ProviderUse.ONLINE_MAIN,
    )
    assert source_id != fixture["source"]["manifest_id"]
    assert source.predecessor_manifest_id == fixture["source"]["manifest_id"]
    assert source.provider_route_binding == route
    assert source.authorizes(route, at=source.created_at)


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


def test_fixture_runtime_fault_removes_created_mutable_collection(
    fixture_request: FixtureInitializationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    collection = fixture["collection_name"]

    def fail_after_create(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected observation fault")

    monkeypatch.setattr(
        "specpilot.deployment.initialize._observe_fixture",
        fail_after_create,
    )
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        with pytest.raises(RuntimeError, match="injected observation fault"):
            initialize_fixture(fixture_request)

        assert not admin.collection_exists(collection)
    finally:
        if admin.collection_exists(collection):
            admin.delete_collection(collection)
        admin.close()


def test_fixture_cancellation_keeps_primary_error_and_uses_bounded_cleanup(
    fixture_request: FixtureInitializationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    collection = fixture["collection_name"]

    class PrimaryCancellation(BaseException):
        pass

    def cancel_after_create(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PrimaryCancellation

    original_delete = QdrantClient.delete_collection
    cleanup_timeouts: list[int | None] = []

    def observed_delete(
        client: QdrantClient,
        collection_name: str,
        timeout: int | None = None,
        **kwargs: object,
    ) -> bool:
        cleanup_timeouts.append(timeout)
        return original_delete(
            client,
            collection_name,
            timeout=timeout,
            **kwargs,
        )

    monkeypatch.setattr(
        "specpilot.deployment.initialize._observe_fixture",
        cancel_after_create,
    )
    monkeypatch.setattr(QdrantClient, "delete_collection", observed_delete)
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        with pytest.raises(PrimaryCancellation):
            initialize_fixture(fixture_request)

        assert cleanup_timeouts == [10]
        assert not admin.collection_exists(collection)
    finally:
        if admin.collection_exists(collection):
            original_delete(admin, collection)
        admin.close()


@pytest.mark.parametrize("failure_point", ["record_publish", "lease_exit"])
def test_fixture_post_publish_failure_preserves_collection_and_recovers(
    fixture_request: FixtureInitializationRequest,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    collection = fixture["collection_name"]
    _inject_post_publish_failure(monkeypatch, failure_point)
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            initialize_fixture(fixture_request)

        manifests = CorpusManifestStore(
            fixture_request.corpus_manifest_dir
        ).read_all()
        assert len(manifests) == 1
        assert manifests[0].collection_name == collection
        assert admin.collection_exists(collection)

        recovered = initialize_fixture(fixture_request)
        assert recovered.corpus_manifest_id == manifests[0].manifest_id
        assert admin.collection_exists(collection)
    finally:
        if admin.collection_exists(collection):
            for snapshot in admin.list_snapshots(collection):
                admin.delete_snapshot(
                    collection_name=collection,
                    snapshot_name=snapshot.name,
                    wait=True,
                )
            admin.delete_collection(collection)
        admin.close()


def test_fixture_cleanup_uncertainty_preserves_created_collection(
    fixture_request: FixtureInitializationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (fixture_request.fixture_dir / "fixture-manifest.json").read_text()
    )
    collection = fixture["collection_name"]

    def fail_before_freeze(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected primary observation failure")

    def fail_manifest_check(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("injected manifest check failure")

    monkeypatch.setattr(
        "specpilot.deployment.initialize._observe_fixture",
        fail_before_freeze,
    )
    monkeypatch.setattr(
        CorpusManifestStore,
        "has_collection_binding",
        fail_manifest_check,
    )
    admin = QdrantClient(url=fixture_request.qdrant_url, trust_env=False)
    try:
        with pytest.raises(RuntimeError, match="primary observation"):
            initialize_fixture(fixture_request)

        assert admin.collection_exists(collection)
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
        corpus_output = tmp_path / "existing-corpus-manifests"
        shutil.copytree(root / "corpus-manifests", corpus_output)
        request = RealInitializationRequest(
            corpus_dir=root,
            corpus_manifest_dir=corpus_output,
            ready_dir=tmp_path / "ready-real",
            qdrant_url=qdrant_url,
        )

        first = initialize_real(request)
        second = initialize_real(request)

        assert first == second
        assert first.mode == "real"
        assert first.corpus_manifest_id == frozen.manifest.manifest_id


@pytest.mark.parametrize("unsafe_shape", ["world_readable", "hardlink"])
def test_existing_real_freeze_refuses_insecure_source_before_verify(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_shape: str,
) -> None:
    freeze_root = tmp_path / f"freeze-{unsafe_shape}"
    with _real_freeze_context(
        freeze_root,
        qdrant_url,
        monkeypatch,
        number="990003" if unsafe_shape == "world_readable" else "990004",
    ) as frozen:
        real_input = tmp_path / f"input-{unsafe_shape}"
        real_input.mkdir(mode=0o700)
        shutil.copytree(
            freeze_root / "source-manifests",
            real_input / "source-manifests",
        )
        (real_input / "sources").mkdir(mode=0o700)
        (real_input / "model").mkdir(mode=0o700)
        source_path = (
            real_input
            / "sources"
            / f"{frozen.manifest.source_manifest_ids[0]}.xml"
        )
        shutil.copyfile(frozen.verify_request.sources[0].xml_path, source_path)
        source_path.chmod(0o600)
        if unsafe_shape == "world_readable":
            source_path.chmod(0o644)
        else:
            os.link(source_path, tmp_path / "source-hardlink.xml")
        corpus_output = tmp_path / f"output-{unsafe_shape}"
        shutil.copytree(freeze_root / "corpus-manifests", corpus_output)
        request = RealInitializationRequest(
            corpus_dir=real_input,
            corpus_manifest_dir=corpus_output,
            ready_dir=tmp_path / f"ready-{unsafe_shape}",
            qdrant_url=qdrant_url,
        )

        with pytest.raises(InitializationRefusal) as raised:
            initialize_real(request)

        assert raised.value.code == "real_corpus_unavailable"


def _new_real_request(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    number: str,
) -> tuple[RealInitializationRequest, SyntheticCorpus]:
    root = tmp_path / f"real-new-{number}"
    synthetic = _synthetic_corpus(root, number)
    corpus_output = tmp_path / f"corpus-output-{number}"
    corpus_output.mkdir(mode=0o700)
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
    return (
        RealInitializationRequest(
            corpus_dir=root,
            corpus_manifest_dir=corpus_output,
            ready_dir=tmp_path / f"ready-new-{number}",
            qdrant_url=qdrant_url,
        ),
        synthetic,
    )


def test_real_initialization_builds_only_the_derived_absent_collection(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, synthetic = _new_real_request(
        tmp_path,
        qdrant_url,
        monkeypatch,
        number="990002",
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


def test_real_runtime_fault_removes_created_mutable_collection(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, synthetic = _new_real_request(
        tmp_path,
        qdrant_url,
        monkeypatch,
        number="990005",
    )

    def fail_after_create(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected real observation fault")

    monkeypatch.setattr(
        "specpilot.deployment.initialize._observe_real_collection",
        fail_after_create,
    )
    admin = QdrantClient(url=qdrant_url, trust_env=False)
    try:
        with pytest.raises(InitializationRefusal) as raised:
            initialize_real(request)

        assert raised.value.code == "real_corpus_unavailable"
        assert not admin.collection_exists(synthetic.collection)
    finally:
        if admin.collection_exists(synthetic.collection):
            admin.delete_collection(synthetic.collection)
        admin.close()


@pytest.mark.parametrize("failure_point", ["record_publish", "lease_exit"])
def test_real_post_publish_failure_preserves_collection_and_recovers(
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    request, synthetic = _new_real_request(
        tmp_path,
        qdrant_url,
        monkeypatch,
        number="990006" if failure_point == "record_publish" else "990007",
    )
    _inject_post_publish_failure(monkeypatch, failure_point)
    admin = QdrantClient(url=qdrant_url, trust_env=False)
    try:
        with pytest.raises(InitializationRefusal) as raised:
            initialize_real(request)

        assert raised.value.code == "real_corpus_unavailable"
        manifests = CorpusManifestStore(request.corpus_manifest_dir).read_all()
        assert len(manifests) == 1
        assert manifests[0].collection_name == synthetic.collection
        assert admin.collection_exists(synthetic.collection)

        recovered = initialize_real(request)
        assert recovered.corpus_manifest_id == manifests[0].manifest_id
        assert admin.collection_exists(synthetic.collection)
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
