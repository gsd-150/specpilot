"""The dense retrieval route: a versioned Qdrant collection.

The collection's name is derived from the things that would invalidate it — the
corpus hash, the chunking version, and the index-text policy. That makes a
rebuilt index a different collection rather than an in-place overwrite, so a
service pinned to one manifest cannot silently start reading vectors built from
a different split of the same document.

**The payload is locators only.** It comes back on every hit, so §8.1's
committable-field rule applies to it exactly as it applies to an annotation
record: unit id, kind, document, version, section number, section path. Never
the text. A retriever that returned clause prose in its payload would have moved
the corpus into every trace and every log that records a search.

Freezing is enforced on the write path rather than in a comment. §6.4 says
ingestion loses write access once the manifest is sealed and serving is
read-only from then on; a late upsert would change what the manifest attests to
while leaving its hash unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from enum import Enum
from types import TracebackType
from typing import Any, Literal, Never, Self, final

from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ApiException
from qdrant_client.models import (
    Datatype,
    Distance,
    PointStruct,
    SearchParams,
    VectorParams,
)

from specpilot.contracts.corpus_manifest import (
    DenseQueryParameters,
    DenseVectorSchema,
    HnswSchema,
    LocatorFieldSchema,
    PayloadIndexSchema,
    QdrantCollectionSchema,
)
from specpilot.manifests.corpus_store import (
    CollectionFreezeLease,
    CollectionLeaseError,
    CollectionWriteLease,
    CorpusManifestStore,
)

# BGE-M3's dense width. A vector of any other size is a bug upstream, and
# reshaping it here would index a document nothing can ever match.
VECTOR_SIZE = 1024

_NAME_PREFIX = "specpilot"
_NAME_DIGEST_CHARS = 32
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_LOCATOR_KEYS = (
    "unit_id",
    "kind",
    "document_id",
    "document_version",
    "section_number",
    "section_path",
)

BASELINE_DENSE_QUERY = DenseQueryParameters(
    hnsw_ef=None,
    exact=False,
    indexed_only=False,
)

class DenseBackendUnavailable(RuntimeError):
    """The local Qdrant transport or API did not complete an operation."""


def _qdrant_call[T](
    operation: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    try:
        return operation(*args, **kwargs)
    except (ApiException, OSError):
        raise DenseBackendUnavailable("dense backend unavailable") from None


def _new_client(url: str) -> Any:
    # Qdrant is a local/private service. Host proxy settings must never
    # redirect corpus inventory or vectors through an egress proxy.
    return _qdrant_call(QdrantClient, url=url, trust_env=False)


def _close_after_failure(client: Any) -> None:
    """Best-effort cleanup that never replaces the factory's primary error."""
    with suppress(Exception):
        _qdrant_call(client.close)


def _close_on_exit(client: Any, exc_type: type[BaseException] | None) -> None:
    if exc_type is None:
        _qdrant_call(client.close)
    else:
        _close_after_failure(client)


def _optional_bool(value: object, *, field_name: str) -> bool:
    if value is None:
        return False
    if type(value) is not bool:
        raise ValueError(f"Qdrant returned an invalid {field_name}")
    return value


def _canonical_config_sha256(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        normalized = value.model_dump(mode="json", exclude_none=True)
    else:
        model_dump = getattr(value, "model_dump", None)
        if not callable(model_dump):
            raise ValueError("Qdrant returned an unsupported configuration")
        normalized = model_dump(mode="json", exclude_none=True)
    try:
        encoded = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Qdrant returned an invalid configuration") from error
    return hashlib.sha256(encoded).hexdigest()


def _enum_value(value: object, *, field_name: str) -> str:
    if not isinstance(value, Enum) or not isinstance(value.value, str):
        raise ValueError(f"Qdrant returned an invalid {field_name}")
    return value.value


def normalize_collection_schema(info: Any) -> QdrantCollectionSchema:
    """Normalize the exact unnamed-vector schema bound by corpus-manifest/v1."""
    try:
        config = info.config
        params = config.params
        vectors = params.vectors
        sparse_vectors = params.sparse_vectors
        collection_hnsw = config.hnsw_config
        collection_quantization = config.quantization_config
        payload_schema = info.payload_schema
    except AttributeError as error:
        raise ValueError("Qdrant returned an incomplete collection schema") from error

    if not isinstance(vectors, VectorParams):
        raise ValueError("corpus-manifest/v1 requires one unnamed dense vector")
    if vectors.multivector_config is not None:
        raise ValueError("corpus-manifest/v1 does not support multivectors")
    if sparse_vectors is not None and not isinstance(sparse_vectors, Mapping):
        raise ValueError("Qdrant returned an invalid sparse vector schema")
    if sparse_vectors:
        raise ValueError("corpus-manifest/v1 does not support sparse vectors")

    distance = _enum_value(vectors.distance, field_name="vector distance").lower()
    if distance != Distance.COSINE.value.lower():
        raise ValueError("corpus-manifest/v1 requires cosine distance")
    if vectors.datatype is None:
        datatype = Datatype.FLOAT32.value
    else:
        datatype = _enum_value(vectors.datatype, field_name="vector datatype")
    if datatype != Datatype.FLOAT32.value:
        raise ValueError("corpus-manifest/v1 requires float32 vectors")

    hnsw_fields = (
        "m",
        "ef_construct",
        "full_scan_threshold",
        "max_indexing_threads",
        "on_disk",
        "payload_m",
    )
    try:
        effective_hnsw = {
            field_name: getattr(collection_hnsw, field_name)
            for field_name in hnsw_fields
        }
    except AttributeError as error:
        raise ValueError("Qdrant returned an incomplete HNSW schema") from error
    if vectors.hnsw_config is not None:
        try:
            for field_name in hnsw_fields:
                override = getattr(vectors.hnsw_config, field_name)
                if override is not None:
                    effective_hnsw[field_name] = override
        except AttributeError as error:
            raise ValueError("Qdrant returned an incomplete HNSW override") from error
    effective_hnsw["on_disk"] = _optional_bool(
        effective_hnsw["on_disk"],
        field_name="HNSW on_disk value",
    )
    vector_on_disk = _optional_bool(
        vectors.on_disk,
        field_name="vector on_disk value",
    )

    if not isinstance(payload_schema, Mapping):
        raise ValueError("Qdrant returned an invalid payload schema")
    if any(not isinstance(field_name, str) for field_name in payload_schema):
        raise ValueError("Qdrant returned an invalid payload index name")
    payload_indexes: list[PayloadIndexSchema] = []
    for field_name in sorted(payload_schema):
        descriptor = payload_schema[field_name]
        try:
            data_type = _enum_value(
                descriptor.data_type,
                field_name="payload index datatype",
            )
            params_sha256 = _canonical_config_sha256(descriptor.params)
        except AttributeError as error:
            raise ValueError("Qdrant returned an invalid payload index") from error
        payload_indexes.append(
            PayloadIndexSchema(
                field_name=field_name,
                data_type=data_type,
                params_sha256=params_sha256,
            )
        )
    indexed_fields = {descriptor.field_name for descriptor in payload_indexes}
    locator_specs: tuple[
        tuple[str, Literal["keyword", "integer"], bool], ...
    ] = (
        ("unit_id", "keyword", False),
        ("kind", "keyword", False),
        ("document_id", "keyword", False),
        ("document_version", "keyword", False),
        ("section_number", "keyword", True),
        ("section_path", "keyword", False),
    )

    return QdrantCollectionSchema(
        dense_vector=DenseVectorSchema(
            name=None,
            size=vectors.size,
            distance="cosine",
            datatype="float32",
            on_disk=vector_on_disk,
            vector_quantization_sha256=_canonical_config_sha256(
                vectors.quantization_config
            ),
        ),
        hnsw=HnswSchema(
            m=effective_hnsw["m"],
            ef_construct=effective_hnsw["ef_construct"],
            full_scan_threshold=effective_hnsw["full_scan_threshold"],
            max_indexing_threads=effective_hnsw["max_indexing_threads"],
            on_disk=effective_hnsw["on_disk"],
            payload_m=effective_hnsw["payload_m"],
        ),
        collection_quantization_sha256=_canonical_config_sha256(
            collection_quantization
        ),
        sparse_vectors=(),
        payload_indexes=tuple(payload_indexes),
        locator_payload=tuple(
            LocatorFieldSchema(
                name=name,
                value_type=value_type,
                nullable=nullable,
                payload_indexed=name in indexed_fields,
            )
            for name, value_type, nullable in locator_specs
        ),
    )


def collection_name(
    corpus_sha256: str, pipeline_version: str, index_text_version: str
) -> str:
    """Derive a collection name from everything that would invalidate it.

    Hashed rather than concatenated because Qdrant names allow a narrow
    alphabet and the inputs contain slashes and dots. Two versions that differ
    anywhere produce different names, which is the property that matters.
    """
    joined = "\x1f".join((corpus_sha256, pipeline_version, index_text_version))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"{_NAME_PREFIX}_{digest[:_NAME_DIGEST_CHARS]}"


def point_payload(unit: Any) -> dict[str, Any]:
    """Locators only. Never `text`, and never `indexed`."""
    return {
        "unit_id": unit.unit_id,
        "kind": unit.kind,
        "document_id": unit.document_id,
        "document_version": unit.document_version,
        "section_number": unit.section_number,
        "section_path": unit.section_path,
    }


def _locator_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_LOCATOR_KEYS):
        raise ValueError("dense point payload is not locator-payload/v1")
    payload = dict(value)
    for field_name in (
        "unit_id",
        "document_id",
        "document_version",
        "section_path",
    ):
        item = payload[field_name]
        if not isinstance(item, str) or not item.strip():
            raise ValueError("dense point payload is not locator-payload/v1")
    kind = payload["kind"]
    if not isinstance(kind, str) or kind not in {"clause", "table"}:
        raise ValueError("dense point payload is not locator-payload/v1")
    section_number = payload["section_number"]
    if section_number is not None and (
        not isinstance(section_number, str) or not section_number.strip()
    ):
        raise ValueError("dense point payload is not locator-payload/v1")
    return payload


@dataclass(frozen=True, slots=True)
class DensePoint:
    unit_id: str
    vector: tuple[float, ...]
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        payload = _locator_payload(self.payload)
        if payload["unit_id"] != self.unit_id:
            raise ValueError("dense point unit_id does not match its payload")
        if (
            len(self.vector) != VECTOR_SIZE
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                for value in self.vector
            )
        ):
            raise ValueError(
                f"vector dimension {len(self.vector)} is not {VECTOR_SIZE}"
            )
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True, slots=True)
class DenseHit:
    unit_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DenseRecord:
    point_id: int | str
    payload: dict[str, Any]
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DenseSnapshot:
    name: str
    checksum: str
    size_bytes: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or _SNAPSHOT_NAME.fullmatch(self.name) is None
        ):
            raise ValueError("Qdrant returned incomplete snapshot metadata")
        if (
            not isinstance(self.checksum, str)
            or _SHA256.fullmatch(self.checksum) is None
        ):
            raise ValueError("Qdrant returned incomplete snapshot metadata")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes <= 0
        ):
            raise ValueError("Qdrant returned incomplete snapshot metadata")


def point_id_for_unit(unit_id: str) -> str:
    """A UUID derived from the unit id, since Qdrant ids are UUID or integer.

    Derived rather than random so that re-upserting the same unit overwrites
    its point instead of adding a second one — a rebuild that doubled the point
    count would break §6.4's load-time check for no real reason.
    """
    digest = hashlib.sha256(unit_id.encode("utf-8")).hexdigest()
    return (
        f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
    )


def _validate_query(vector: Sequence[float], k: int) -> None:
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k must be positive")
    if len(vector) != VECTOR_SIZE:
        raise ValueError(f"vector dimension {len(vector)} is not {VECTOR_SIZE}")
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        for value in vector
    ):
        raise ValueError("query vector is invalid")


def _record_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, (int, float)) or isinstance(item, bool)
        for item in value
    ):
        raise ValueError("dense point does not hold one unnamed vector")
    try:
        numeric = tuple(float(item) for item in value)
    except (OverflowError, ValueError) as error:
        raise ValueError("dense point vector is invalid") from error
    if len(numeric) != VECTOR_SIZE or any(
        not math.isfinite(item) for item in numeric
    ):
        raise ValueError("dense point vector is invalid")
    return numeric


@dataclass(slots=True)
class DenseIndex:
    """Read-only access to one Qdrant collection."""

    name: str
    _client: Any = field(repr=False)

    @classmethod
    def open(cls, url: str, name: str) -> DenseIndex:
        return cls(name=name, _client=_new_client(url))

    def collection_schema(self) -> QdrantCollectionSchema:
        info = _qdrant_call(self._client.get_collection, self.name)
        return normalize_collection_schema(info)

    def vector_size(self) -> int:
        return self.collection_schema().dense_vector.size

    def point_count(self) -> int:
        value = _qdrant_call(self._client.count, self.name, exact=True)
        return int(value.count)

    def unit_ids(self) -> frozenset[str]:
        """Read the complete payload inventory without retrieving vectors."""
        found: set[str] = set()
        offset: Any = None
        while True:
            points, offset = _qdrant_call(
                self._client.scroll,
                collection_name=self.name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = _locator_payload(point.payload or {})
                found.add(payload["unit_id"])
            if offset is None:
                return frozenset(found)

    def iter_records(self, *, batch_size: int = 256) -> Iterator[DenseRecord]:
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size <= 0
        ):
            raise ValueError("record batch size must be positive")
        point_ids: set[int | str] = set()
        unit_ids: set[str] = set()
        offset: Any = None
        while True:
            points, offset = _qdrant_call(
                self._client.scroll,
                collection_name=self.name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for point in points:
                point_id = point.id
                if type(point_id) not in (int, str):
                    raise ValueError("dense point id is invalid")
                payload = _locator_payload(point.payload or {})
                unit_id = payload["unit_id"]
                if point_id in point_ids or unit_id in unit_ids:
                    raise ValueError("dense collection has a duplicate identity")
                vector = _record_vector(point.vector)
                point_ids.add(point_id)
                unit_ids.add(unit_id)
                yield DenseRecord(point_id, payload, vector)
            if offset is None:
                return

    def snapshots(self) -> tuple[DenseSnapshot, ...]:
        values = _qdrant_call(self._client.list_snapshots, self.name)
        result: list[DenseSnapshot] = []
        for value in values:
            result.append(
                DenseSnapshot(
                    name=value.name,
                    checksum=value.checksum,
                    size_bytes=value.size,
                )
            )
        result.sort(key=lambda item: item.name)
        return tuple(result)

    def search(self, vector: Sequence[float], k: int) -> list[DenseHit]:
        _validate_query(vector, k)
        response = _qdrant_call(
            self._client.query_points,
            collection_name=self.name,
            query=list(vector),
            limit=k,
            with_payload=True,
            search_params=SearchParams(
                hnsw_ef=BASELINE_DENSE_QUERY.hnsw_ef,
                exact=BASELINE_DENSE_QUERY.exact,
                indexed_only=BASELINE_DENSE_QUERY.indexed_only,
            ),
        )
        result: list[DenseHit] = []
        for point in response.points:
            payload = _locator_payload(point.payload or {})
            result.append(
                DenseHit(
                    unit_id=payload["unit_id"],
                    score=float(point.score),
                    payload=payload,
                )
            )
        return result

    def close(self) -> None:
        _qdrant_call(self._client.close)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        _close_on_exit(self._client, exc_type)


def _require_write_lease(lease: object) -> CollectionWriteLease:
    if type(lease) is not CollectionWriteLease:
        raise CollectionLeaseError("dense writer requires a write lease")
    return lease


def _require_freeze_lease(lease: object) -> CollectionFreezeLease:
    if type(lease) is not CollectionFreezeLease:
        raise CollectionLeaseError("snapshot admin requires a freeze lease")
    return lease


def _require_store(store: object) -> CorpusManifestStore:
    if type(store) is not CorpusManifestStore:
        raise CollectionLeaseError("dense capability requires its issuing store")
    return store


@contextmanager
def _write_operation(
    store: object,
    lease: object,
    name: str,
) -> Iterator[None]:
    owner = _require_store(store)
    writer = _require_write_lease(lease)
    with CorpusManifestStore.write_operation(owner, writer, name):
        yield


@contextmanager
def _freeze_operation(
    store: object,
    lease: object,
    name: str,
) -> Iterator[None]:
    owner = _require_store(store)
    freezer = _require_freeze_lease(lease)
    with CorpusManifestStore.freeze_operation(owner, freezer, name):
        yield


@final
@dataclass(frozen=True, slots=True)
class DenseIndexWriter:
    """Lease-bound mutation access to one Qdrant collection."""

    name: str
    _client: Any = field(repr=False)
    _store: CorpusManifestStore = field(repr=False)
    _lease: CollectionWriteLease = field(repr=False)

    def __post_init__(self) -> None:
        with _write_operation(self._store, self._lease, self.name):
            pass

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("DenseIndexWriter cannot be subclassed")

    @classmethod
    def create(
        cls,
        url: str,
        name: str,
        store: CorpusManifestStore,
        lease: CollectionWriteLease,
    ) -> DenseIndexWriter:
        owner = _require_store(store)
        live_lease = _require_write_lease(lease)
        with _write_operation(owner, live_lease, name):
            client = _new_client(url)
            try:
                if _qdrant_call(client.collection_exists, name):
                    raise FileExistsError(name)
                _qdrant_call(
                    client.create_collection,
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=VECTOR_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                return cls(name, client, owner, live_lease)
            except BaseException:
                _close_after_failure(client)
                raise

    @classmethod
    def open(
        cls,
        url: str,
        name: str,
        store: CorpusManifestStore,
        lease: CollectionWriteLease,
    ) -> DenseIndexWriter:
        owner = _require_store(store)
        live_lease = _require_write_lease(lease)
        with _write_operation(owner, live_lease, name):
            client = _new_client(url)
            try:
                if not _qdrant_call(client.collection_exists, name):
                    raise FileNotFoundError(name)
                return cls(name, client, owner, live_lease)
            except BaseException:
                _close_after_failure(client)
                raise

    def upsert(self, points: Sequence[DensePoint]) -> None:
        with _write_operation(self._store, self._lease, self.name):
            if not points:
                return
            structs: list[PointStruct] = []
            for point in points:
                payload = _locator_payload(point.payload)
                if payload["unit_id"] != point.unit_id:
                    raise ValueError("dense point unit_id does not match its payload")
                structs.append(
                    PointStruct(
                        id=point_id_for_unit(point.unit_id),
                        vector=list(point.vector),
                        payload=payload,
                    )
                )
            _qdrant_call(
                self._client.upsert,
                collection_name=self.name,
                points=structs,
                wait=True,
            )

    def drop(self) -> None:
        with _write_operation(self._store, self._lease, self.name):
            _qdrant_call(self._client.delete_collection, self.name)

    def close(self) -> None:
        _qdrant_call(self._client.close)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        _close_on_exit(self._client, exc_type)


@final
@dataclass(frozen=True, slots=True)
class DenseSnapshotAdmin:
    """Exclusive-lease capability for collection snapshot creation."""

    name: str
    _client: Any = field(repr=False)
    _store: CorpusManifestStore = field(repr=False)
    _lease: CollectionFreezeLease = field(repr=False)

    def __post_init__(self) -> None:
        with _freeze_operation(self._store, self._lease, self.name):
            pass

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("DenseSnapshotAdmin cannot be subclassed")

    @classmethod
    def open(
        cls,
        url: str,
        name: str,
        store: CorpusManifestStore,
        lease: CollectionFreezeLease,
    ) -> DenseSnapshotAdmin:
        owner = _require_store(store)
        live_lease = _require_freeze_lease(lease)
        with _freeze_operation(owner, live_lease, name):
            client = _new_client(url)
            try:
                return cls(name, client, owner, live_lease)
            except BaseException:
                _close_after_failure(client)
                raise

    def create_snapshot(self) -> DenseSnapshot:
        with _freeze_operation(self._store, self._lease, self.name):
            value = _qdrant_call(
                self._client.create_snapshot,
                self.name,
                wait=True,
            )
        if value is None:
            raise ValueError("Qdrant returned incomplete snapshot metadata")
        return DenseSnapshot(
            name=value.name,
            checksum=value.checksum,
            size_bytes=value.size,
        )

    def close(self) -> None:
        _qdrant_call(self._client.close)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        _close_on_exit(self._client, exc_type)
