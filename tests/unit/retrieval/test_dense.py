from __future__ import annotations

import math
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Datatype,
    Distance,
    HnswConfigDiff,
    KeywordIndexParams,
    KeywordIndexType,
    MultiVectorComparator,
    MultiVectorConfig,
    PayloadIndexInfo,
    PayloadSchemaType,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.indexable import IndexTextPolicy, build_index_units
from specpilot.manifests.corpus_store import (
    CollectionLeaseError,
    CorpusManifestStore,
)
from specpilot.retrieval.dense import (
    BASELINE_DENSE_QUERY,
    VECTOR_SIZE,
    DenseBackendUnavailable,
    DenseIndex,
    DenseIndexWriter,
    DensePoint,
    DenseSnapshotAdmin,
    collection_name,
    normalize_collection_schema,
    point_id_for_unit,
    point_payload,
)
from tests.helpers import rfc_factory

WEIGHTS = "a" * 64


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "units.xml", rfc_factory.QA_RFC_XML)


def units(document: Path, policy: IndexTextPolicy | None = None):  # type: ignore[no-untyped-def]
    return build_index_units(document, RfcLimits(), ClauseLimits(), policy)


def test_a_section_heading_joins_the_indexed_text_but_not_the_source_text(
    document: Path,
) -> None:
    """A section number is a locator and never appears in its own body, so
    without this a question naming a section is unanswerable by retrieval."""
    first = units(document)[0]

    assert first.indexed.startswith("1 One")
    assert "1 One" not in first.text


def test_the_policy_can_be_turned_off_and_then_the_texts_match(
    document: Path,
) -> None:
    """It is a recorded decision, not a property of the parser."""
    plain = units(document, IndexTextPolicy(include_section_heading=False))[0]

    assert plain.indexed == plain.text


def test_tables_become_units_alongside_clauses(document: Path) -> None:
    kinds = {unit.kind for unit in units(document)}

    assert kinds == {"clause", "table"}


def test_every_unit_id_is_distinct(document: Path) -> None:
    all_units = units(document)

    assert len({unit.unit_id for unit in all_units}) == len(all_units)


def test_every_index_unit_carries_the_publication_version(document: Path) -> None:
    assert {unit.document_version for unit in units(document)} == {"2026-08"}


def test_the_collection_name_carries_the_corpus_and_pipeline_versions() -> None:
    name = collection_name("c" * 64, "clause/v1", "index-text/v1")

    assert name.startswith("specpilot_")
    assert collection_name("d" * 64, "clause/v1", "index-text/v1") != name
    assert collection_name("c" * 64, "clause/v2", "index-text/v1") != name
    assert collection_name("c" * 64, "clause/v1", "index-text/v2") != name


def test_the_collection_name_is_stable_for_the_same_versions() -> None:
    first = collection_name("c" * 64, "clause/v1", "index-text/v1")
    second = collection_name("c" * 64, "clause/v1", "index-text/v1")

    assert first == second


def test_the_collection_name_is_a_legal_qdrant_identifier() -> None:
    name = collection_name("c" * 64, "clause/v1", "index-text/v1")

    assert name.replace("_", "").isalnum()
    assert len(name) <= 255


def test_a_point_payload_holds_locators_and_never_clause_text(
    document: Path,
) -> None:
    """The payload comes back on every hit. Section 8.1's field rule applies to
    it exactly as it applies to an annotation record."""
    unit = units(document)[0]

    payload = point_payload(unit)

    assert set(payload) == {
        "unit_id",
        "kind",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
    }
    assert "Prose" not in str(payload)
    assert unit.text not in str(payload)


def test_a_point_carries_a_vector_of_the_models_width() -> None:
    point = DensePoint(unit_id="u1", vector=(0.0,) * VECTOR_SIZE, payload={})

    assert len(point.vector) == VECTOR_SIZE


def test_a_vector_of_the_wrong_width_is_refused() -> None:
    """A silently reshaped vector would index a document nothing can match."""
    with pytest.raises(ValueError, match="dimension"):
        DensePoint(unit_id="u1", vector=(0.0, 1.0), payload={})


def _collection_info(
    *,
    vectors: object | None = None,
    sparse_vectors: object | None = None,
    hnsw_config: object | None = None,
    quantization_config: object | None = None,
    payload_schema: dict[str, object] | None = None,
) -> SimpleNamespace:
    if vectors is None:
        vectors = VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)
    if hnsw_config is None:
        hnsw_config = SimpleNamespace(
            m=16,
            ef_construct=100,
            full_scan_threshold=10000,
            max_indexing_threads=0,
            on_disk=None,
            payload_m=None,
        )
    return SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=vectors,
                sparse_vectors=sparse_vectors,
            ),
            hnsw_config=hnsw_config,
            quantization_config=quantization_config,
        ),
        payload_schema={} if payload_schema is None else payload_schema,
    )


_DEFAULT_VECTOR = object()


def _record(
    *,
    point_id: object = 1,
    unit_id: object = "u1",
    vector: object = _DEFAULT_VECTOR,
) -> SimpleNamespace:
    if vector is _DEFAULT_VECTOR:
        vector = [0.0] * VECTOR_SIZE
    return SimpleNamespace(
        id=point_id,
        payload={"unit_id": unit_id},
        vector=vector,
    )


class StubClient:
    def __init__(self, *, exists: bool = False) -> None:
        self.exists = exists
        self.closed = 0
        self.created = 0
        self.deleted = 0
        self.upserts = 0
        self.query_kwargs: dict[str, object] | None = None
        self.scroll_responses: list[tuple[list[object], object | None]] = []

    def collection_exists(self, name: str) -> bool:
        del name
        return self.exists

    def create_collection(self, **kwargs: object) -> bool:
        del kwargs
        self.created += 1
        self.exists = True
        return True

    def delete_collection(self, name: str) -> bool:
        del name
        self.deleted += 1
        self.exists = False
        return True

    def upsert(self, **kwargs: object) -> None:
        del kwargs
        self.upserts += 1

    def get_collection(self, name: str) -> object:
        del name
        return _collection_info()

    def count(self, name: str, *, exact: bool) -> SimpleNamespace:
        del name
        assert exact is True
        return SimpleNamespace(count=2)

    def scroll(self, **kwargs: object) -> tuple[list[object], object | None]:
        del kwargs
        if self.scroll_responses:
            return self.scroll_responses.pop(0)
        return [], None

    def list_snapshots(self, name: str) -> list[object]:
        del name
        return []

    def query_points(self, **kwargs: object) -> SimpleNamespace:
        self.query_kwargs = kwargs
        return SimpleNamespace(points=[])

    def create_snapshot(self, name: str, *, wait: bool) -> object:
        del name
        assert wait is True
        return SimpleNamespace(name="snapshot", checksum="a" * 64, size=1)

    def close(self) -> None:
        self.closed += 1


def test_read_only_index_has_no_mutation_surface() -> None:
    for name in ("create", "upsert", "drop", "freeze"):
        assert not hasattr(DenseIndex, name)


def test_default_vector_schema_is_normalized() -> None:
    schema = normalize_collection_schema(_collection_info())

    assert schema.dense_vector.name is None
    assert schema.dense_vector.size == VECTOR_SIZE
    assert schema.dense_vector.distance == "cosine"
    assert schema.dense_vector.datatype == "float32"
    assert schema.dense_vector.on_disk is False
    assert schema.sparse_vectors == ()
    assert schema.hnsw.on_disk is False


def test_vector_hnsw_overrides_and_quantization_are_normalized_separately() -> None:
    vector_quantization = ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            quantile=0.99,
            always_ram=True,
        )
    )
    collection_quantization = ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            quantile=0.95,
            always_ram=False,
        )
    )
    vectors = VectorParams(
        size=VECTOR_SIZE,
        distance=Distance.COSINE,
        datatype=Datatype.FLOAT32,
        on_disk=True,
        hnsw_config=HnswConfigDiff(
            m=32,
            ef_construct=None,
            full_scan_threshold=0,
            max_indexing_threads=4,
            on_disk=False,
            payload_m=8,
        ),
        quantization_config=vector_quantization,
    )

    schema = normalize_collection_schema(
        _collection_info(
            vectors=vectors,
            quantization_config=collection_quantization,
        )
    )

    assert schema.hnsw.model_dump() == {
        "m": 32,
        "ef_construct": 100,
        "full_scan_threshold": 0,
        "max_indexing_threads": 4,
        "on_disk": False,
        "payload_m": 8,
    }
    assert schema.dense_vector.on_disk is True
    assert (
        schema.dense_vector.vector_quantization_sha256
        == "bd012d9e2eab22410e54b92d5ef0f51493b4e4f753f7a8595df735df7109a104"
    )
    assert schema.collection_quantization_sha256 is not None
    assert (
        schema.collection_quantization_sha256
        != schema.dense_vector.vector_quantization_sha256
    )


def test_payload_indexes_are_sorted_ignore_point_counts_and_set_locator_flags() -> None:
    params = KeywordIndexParams(
        type=KeywordIndexType.KEYWORD,
        is_tenant=True,
        on_disk=False,
    )
    payload_schema = {
        "unit_id": PayloadIndexInfo(
            data_type=PayloadSchemaType.KEYWORD,
            params=params,
            points=2,
        ),
        "document_id": PayloadIndexInfo(
            data_type=PayloadSchemaType.KEYWORD,
            params=None,
            points=19,
        ),
    }

    first = normalize_collection_schema(
        _collection_info(payload_schema=payload_schema)
    )
    payload_schema["unit_id"] = PayloadIndexInfo(
        data_type=PayloadSchemaType.KEYWORD,
        params=params,
        points=999,
    )
    second = normalize_collection_schema(
        _collection_info(payload_schema=payload_schema)
    )

    assert first == second
    assert [item.field_name for item in first.payload_indexes] == [
        "document_id",
        "unit_id",
    ]
    assert first.payload_indexes[1].params_sha256 == (
        "04742e674b1ebebe1c0a193a33e8c070343f18b003b53610d2f4316ac4383726"
    )
    assert tuple(item.name for item in first.locator_payload) == (
        "unit_id",
        "kind",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
    )
    assert tuple(item.payload_indexed for item in first.locator_payload) == (
        True,
        False,
        True,
        False,
        False,
        False,
    )
    assert tuple(item.nullable for item in first.locator_payload) == (
        False,
        False,
        False,
        False,
        True,
        False,
    )


@pytest.mark.parametrize(
    "vectors",
    [
        {"named": VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)},
        VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE,
            datatype=Datatype.FLOAT16,
        ),
        VectorParams(size=VECTOR_SIZE, distance=Distance.DOT),
        VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE,
            multivector_config=MultiVectorConfig(
                comparator=MultiVectorComparator.MAX_SIM
            ),
        ),
    ],
    ids=("named", "float16", "dot", "multivector"),
)
def test_unsupported_dense_schemas_are_refused(vectors: object) -> None:
    with pytest.raises(ValueError):
        normalize_collection_schema(_collection_info(vectors=vectors))


def test_nonempty_sparse_schema_is_refused() -> None:
    with pytest.raises(ValueError):
        normalize_collection_schema(
            _collection_info(sparse_vectors={"sparse": SimpleNamespace()})
        )


def test_baseline_search_parameters_are_sent_explicitly() -> None:
    client = StubClient()
    index = DenseIndex("collection", client)

    index.search([0.0] * VECTOR_SIZE, 3)

    assert BASELINE_DENSE_QUERY.model_dump() == {
        "hnsw_ef": None,
        "exact": False,
        "indexed_only": False,
    }
    assert client.query_kwargs is not None
    params = client.query_kwargs["search_params"]
    assert params.hnsw_ef is None
    assert params.exact is False
    assert params.indexed_only is False


def test_point_id_for_unit_preserves_the_existing_id_algorithm() -> None:
    assert point_id_for_unit("u1") == "bb82030d-bc2b-caba-32a9-0bf2e207a84a"


def test_record_iteration_scrolls_all_pages_with_payloads_and_vectors() -> None:
    client = StubClient()
    calls: list[dict[str, object]] = []
    responses = [
        ([_record(point_id=1, unit_id="u1")], "page-2"),
        ([_record(point_id="point-2", unit_id="u2")], None),
    ]

    def scroll(**kwargs: object) -> tuple[list[object], object | None]:
        calls.append(kwargs)
        return responses.pop(0)

    client.scroll = scroll  # type: ignore[method-assign]

    records = tuple(DenseIndex("collection", client).iter_records(batch_size=1))

    assert [record.point_id for record in records] == [1, "point-2"]
    assert [record.payload["unit_id"] for record in records] == ["u1", "u2"]
    assert [call["offset"] for call in calls] == [None, "page-2"]
    assert all(call["limit"] == 1 for call in calls)
    assert all(call["with_payload"] is True for call in calls)
    assert all(call["with_vectors"] is True for call in calls)


@pytest.mark.parametrize(
    "point",
    [
        _record(point_id=None),
        _record(point_id=True),
        _record(unit_id=""),
        _record(unit_id="   "),
        _record(vector=None),
        _record(vector={"named": [0.0] * VECTOR_SIZE}),
        _record(vector=[[0.0] * VECTOR_SIZE]),
        _record(vector=[0.0] * (VECTOR_SIZE - 1)),
        _record(vector=[math.nan] + [0.0] * (VECTOR_SIZE - 1)),
        _record(vector=[math.inf] + [0.0] * (VECTOR_SIZE - 1)),
        _record(vector=[True] + [0.0] * (VECTOR_SIZE - 1)),
    ],
    ids=(
        "absent-point-id",
        "boolean-point-id",
        "absent-unit-id",
        "blank-unit-id",
        "absent-vector",
        "named-vector",
        "multivector",
        "wrong-width",
        "nan",
        "infinity",
        "boolean-value",
    ),
)
def test_invalid_dense_records_are_refused(point: object) -> None:
    client = StubClient()
    client.scroll_responses = [([point], None)]

    with pytest.raises(ValueError):
        tuple(DenseIndex("collection", client).iter_records())


@pytest.mark.parametrize(
    "points",
    [
        [_record(point_id=1, unit_id="u1"), _record(point_id=1, unit_id="u2")],
        [_record(point_id=1, unit_id="u1"), _record(point_id=2, unit_id="u1")],
    ],
    ids=("point-id", "unit-id"),
)
def test_duplicate_dense_record_identity_is_refused(points: list[object]) -> None:
    client = StubClient()
    client.scroll_responses = [(points, None)]

    with pytest.raises(ValueError, match="duplicate identity"):
        tuple(DenseIndex("collection", client).iter_records())


def test_record_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        tuple(DenseIndex("collection", StubClient()).iter_records(batch_size=0))


def test_snapshot_metadata_is_validated_and_sorted() -> None:
    client = StubClient()
    client.list_snapshots = lambda name: [  # type: ignore[method-assign]
        SimpleNamespace(name="z", checksum="b" * 64, size=2),
        SimpleNamespace(name="a", checksum="a" * 64, size=1),
    ]

    snapshots = DenseIndex("collection", client).snapshots()

    assert [snapshot.name for snapshot in snapshots] == ["a", "z"]


@pytest.mark.parametrize(
    "snapshot",
    [
        SimpleNamespace(name="", checksum="a" * 64, size=1),
        SimpleNamespace(name="snapshot", checksum="A" * 64, size=1),
        SimpleNamespace(name="snapshot", checksum="a" * 63, size=1),
        SimpleNamespace(name="snapshot", checksum="a" * 64, size=0),
    ],
)
def test_incomplete_snapshot_metadata_is_refused(snapshot: object) -> None:
    client = StubClient()
    client.list_snapshots = lambda name: [snapshot]  # type: ignore[method-assign]

    with pytest.raises(ValueError):
        DenseIndex("collection", client).snapshots()


def test_backend_errors_are_redacted_without_wrapping_schema_errors() -> None:
    client = StubClient()

    def unavailable(name: str) -> object:
        del name
        raise UnexpectedResponse(503, "secret backend", b"secret", httpx.Headers())

    client.get_collection = unavailable  # type: ignore[method-assign]
    with pytest.raises(DenseBackendUnavailable) as captured:
        DenseIndex("collection", client).collection_schema()
    assert str(captured.value) == "dense backend unavailable"
    assert "secret" not in str(captured.value)

    client.get_collection = lambda name: _collection_info(  # type: ignore[method-assign]
        vectors={"named": VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)}
    )
    with pytest.raises(ValueError) as mismatch:
        DenseIndex("collection", client).collection_schema()
    assert type(mismatch.value) is ValueError


def test_os_errors_from_backend_calls_use_the_same_stable_error() -> None:
    client = StubClient()

    def unavailable(name: str, *, exact: bool) -> object:
        del name, exact
        raise OSError("secret socket path")

    client.count = unavailable  # type: ignore[method-assign]

    with pytest.raises(DenseBackendUnavailable) as captured:
        DenseIndex("collection", client).point_count()
    assert str(captured.value) == "dense backend unavailable"
    assert "secret" not in str(captured.value)


def _install_client(
    monkeypatch: pytest.MonkeyPatch,
    client: StubClient,
) -> None:
    def construct(*, url: str, trust_env: bool) -> StubClient:
        assert url == "http://127.0.0.1:6333"
        assert trust_env is False
        return client

    monkeypatch.setattr("specpilot.retrieval.dense.QdrantClient", construct)


def test_writer_create_requires_an_exact_live_matching_write_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "specpilot_collection"
    store = CorpusManifestStore(tmp_path / "manifests")
    client = StubClient()
    _install_client(monkeypatch, client)

    foreign = store.acquire_write_lease("another_collection")
    try:
        with pytest.raises(CollectionLeaseError):
            DenseIndexWriter.create("http://127.0.0.1:6333", name, foreign)
    finally:
        foreign.close()

    freeze = store.acquire_freeze_lease(name)
    try:
        with pytest.raises(CollectionLeaseError):
            DenseIndexWriter.create(  # type: ignore[arg-type]
                "http://127.0.0.1:6333", name, freeze
            )
    finally:
        freeze.close()

    closed = store.acquire_write_lease(name)
    closed.close()
    with pytest.raises(CollectionLeaseError):
        DenseIndexWriter.create("http://127.0.0.1:6333", name, closed)

    assert client.created == 0


def test_direct_capability_construction_cannot_bypass_exact_lease_types(
    tmp_path: Path,
) -> None:
    name = "specpilot_collection"
    store = CorpusManifestStore(tmp_path / "manifests")
    client = StubClient()

    with store.acquire_freeze_lease(name) as freeze, pytest.raises(
        CollectionLeaseError
    ):
        DenseIndexWriter(name, client, freeze)  # type: ignore[arg-type]
    with store.acquire_write_lease(name) as writer, pytest.raises(
        CollectionLeaseError
    ):
        DenseSnapshotAdmin(name, client, writer)  # type: ignore[arg-type]


def test_existing_collection_is_never_deleted_by_writer_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "specpilot_collection"
    store = CorpusManifestStore(tmp_path / "manifests")
    client = StubClient(exists=True)
    _install_client(monkeypatch, client)

    with store.acquire_write_lease(name) as lease, pytest.raises(FileExistsError):
        DenseIndexWriter.create("http://127.0.0.1:6333", name, lease)

    assert client.deleted == 0
    assert client.created == 0
    assert client.closed == 1


def test_escaped_writer_rejects_every_mutation_after_lease_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "specpilot_collection"
    store = CorpusManifestStore(tmp_path / "manifests")
    client = StubClient()
    _install_client(monkeypatch, client)
    lease = store.acquire_write_lease(name)
    writer = DenseIndexWriter.create("http://127.0.0.1:6333", name, lease)
    lease.close()

    with pytest.raises(CollectionLeaseError):
        writer.upsert([])
    with pytest.raises(CollectionLeaseError):
        writer.drop()

    assert client.upserts == 0
    assert client.deleted == 0
    writer.close()


def test_writer_and_reader_contexts_close_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "specpilot_collection"
    store = CorpusManifestStore(tmp_path / "manifests")
    writer_client = StubClient()
    _install_client(monkeypatch, writer_client)
    with store.acquire_write_lease(name) as lease, DenseIndexWriter.create(
        "http://127.0.0.1:6333", name, lease
    ) as writer:
        assert writer.name == name
    assert writer_client.closed == 1

    reader_client = StubClient()
    _install_client(monkeypatch, reader_client)
    with DenseIndex.open("http://127.0.0.1:6333", name) as reader:
        assert reader.name == name
    assert reader_client.closed == 1


class BlockingClient(StubClient):
    def __init__(self) -> None:
        super().__init__(exists=True)
        self.started = threading.Event()
        self.release = threading.Event()

    def upsert(self, **kwargs: object) -> None:
        del kwargs
        self.started.set()
        assert self.release.wait(timeout=2)
        self.upserts += 1

    def create_snapshot(self, name: str, *, wait: bool) -> object:
        del name
        assert wait is True
        self.started.set()
        assert self.release.wait(timeout=2)
        return SimpleNamespace(name="snapshot", checksum="a" * 64, size=1)


def _assert_close_waits_for_operation(
    lease: Any,
    client: BlockingClient,
    operation: Callable[[], object],
    refused_operation: Callable[[], object],
) -> None:
    operation_errors: list[BaseException] = []
    close_errors: list[BaseException] = []
    close_done = threading.Event()

    def run_operation() -> None:
        try:
            operation()
        except BaseException as error:
            operation_errors.append(error)

    def close_lease() -> None:
        try:
            lease.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            close_done.set()

    worker = threading.Thread(target=run_operation)
    worker.start()
    assert client.started.wait(timeout=1)
    closer = threading.Thread(target=close_lease)
    closer.start()
    with lease._state.condition:  # noqa: SLF001
        assert lease._state.condition.wait_for(  # noqa: SLF001
            lambda: lease._state.closing,  # noqa: SLF001
            timeout=1,
        )
    assert not close_done.is_set()
    with pytest.raises(CollectionLeaseError):
        refused_operation()
    client.release.set()
    worker.join(timeout=2)
    closer.join(timeout=2)
    assert not worker.is_alive()
    assert not closer.is_alive()
    assert close_done.is_set()
    assert operation_errors == []
    assert close_errors == []


def test_upsert_holds_the_write_lease_until_the_backend_call_finishes(
    tmp_path: Path,
) -> None:
    name = "specpilot_collection"
    lease = CorpusManifestStore(tmp_path / "manifests").acquire_write_lease(name)
    client = BlockingClient()
    writer = DenseIndexWriter(name, client, lease)
    point = DensePoint("u1", (0.0,) * VECTOR_SIZE, {"unit_id": "u1"})

    _assert_close_waits_for_operation(
        lease,
        client,
        lambda: writer.upsert([point]),
        writer.drop,
    )

    assert client.upserts == 1
    assert client.deleted == 0
    writer.close()


def test_snapshot_holds_the_freeze_lease_until_the_backend_call_finishes(
    tmp_path: Path,
) -> None:
    name = "specpilot_collection"
    lease = CorpusManifestStore(tmp_path / "manifests").acquire_freeze_lease(name)
    client = BlockingClient()
    admin = DenseSnapshotAdmin(name, client, lease)

    _assert_close_waits_for_operation(
        lease,
        client,
        admin.create_snapshot,
        admin.create_snapshot,
    )

    admin.close()
