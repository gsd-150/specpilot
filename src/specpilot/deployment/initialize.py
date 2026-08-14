"""Explicit fixture and real corpus initialization; never imported by startup."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints
from qdrant_client import QdrantClient

from specpilot.contracts.corpus_manifest import (
    Bm25Binding,
    CorpusManifest,
    CorpusManifestDraft,
    CorpusManifestIntent,
    ParseQaEvidence,
    QdrantSnapshotBinding,
)
from specpilot.contracts.manifests import RfcSourceManifest, RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import (
    build_dense_inventory,
    derived_corpus_sha256,
)
from specpilot.corpus.freezing import (
    CorpusManifestRefusal,
    CorpusSourceInput,
    FreezeCorpusRequest,
    VerifyCorpusRequest,
    _current_versions,
    _retrieval_binding,
    freeze_corpus,
    verify_corpus,
)
from specpilot.corpus.indexable import IndexTextPolicy
from specpilot.corpus.walk import document_identity
from specpilot.embedding.throughput import PIPELINE_VERSION
from specpilot.ingestion.rfc import RfcByteSnapshot, VerifiedRfc, verify_rfc_snapshot
from specpilot.manifests._secure_records import SecureRecordDirectory
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.bm25 import TOKENIZER_VERSION, Bm25Index
from specpilot.retrieval.dense import (
    DenseIndex,
    DenseIndexWriter,
    DensePoint,
    DenseRecord,
    DenseSnapshotAdmin,
    collection_name,
    point_id_for_unit,
)
from specpilot.retrieval.local import LocalCorpus

from .ready import ReadyMarker, ReadyMarkerStore

_MAX_FIXTURE_MANIFEST_BYTES = 64 * 1024
_MAX_DENSE_POINTS_BYTES = 32 * 1024 * 1024
_QDRANT_OPERATION_TIMEOUT_SECONDS = 10
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class InitializationRefusal(ValueError):
    """One sanitized refusal code suitable for the CLI boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _refuse(code: str) -> Never:
    raise InitializationRefusal(code)


@dataclass(frozen=True, slots=True)
class FixtureInitializationRequest:
    fixture_dir: Path
    source_manifest_dir: Path
    corpus_manifest_dir: Path
    ready_dir: Path
    qdrant_url: str


@dataclass(frozen=True, slots=True)
class RealInitializationRequest:
    corpus_dir: Path
    corpus_manifest_dir: Path
    ready_dir: Path
    qdrant_url: str


class _FixtureSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: Literal["source.xml"]
    sha256: Sha256
    document_id: Literal["ietf-rfc-9999"]
    document_version: Literal["2026-08"]
    text_url: HttpUrl
    xml_url: HttpUrl
    text_sha256: Sha256
    downloaded_at: datetime
    created_at: datetime
    manifest_id: Sha256


class _FixtureDensePoints(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: Literal["dense-points.jsonl"]
    sha256: Sha256
    vector_size: Literal[1024]
    point_count: Annotated[int, Field(strict=True, gt=0, le=4096)]


class _FixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["fixture-manifest/v1"]
    source: _FixtureSource
    dense_points: _FixtureDensePoints
    derived_corpus_sha256: Sha256
    collection_name: Annotated[
        str,
        StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,255}$"),
    ]
    inventory_root_sha256: Sha256
    embedding_weights_sha256: Sha256
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _FixtureBundle:
    manifest: _FixtureManifest
    source_manifest: RfcSourceManifest
    corpus: LocalCorpus
    bm25: Bm25Index
    points: tuple[DensePoint, ...]
    inventory_root_sha256: str


def _decode_dense_points(
    data: bytes,
    corpus: LocalCorpus,
    *,
    invalid_code: str,
) -> tuple[tuple[DensePoint, ...], str]:
    by_id = {unit.unit_id: unit for unit in corpus.units()}
    points: list[DensePoint] = []
    records: list[DenseRecord] = []
    try:
        decoded_lines = data.decode("utf-8").splitlines()
        for line in decoded_lines:
            raw = json.loads(line)
            if (
                not isinstance(raw, dict)
                or set(raw) != {"unit_id", "vector", "payload"}
                or not isinstance(raw.get("unit_id"), str)
                or raw["unit_id"] not in by_id
                or not isinstance(raw.get("vector"), list)
                or not isinstance(raw.get("payload"), dict)
                or _canonical_json_line(raw).rstrip(b"\n") != line.encode("utf-8")
            ):
                raise ValueError
            point = DensePoint(
                raw["unit_id"],
                tuple(raw["vector"]),
                raw["payload"],
            )
            points.append(point)
            records.append(
                DenseRecord(
                    point_id_for_unit(point.unit_id),
                    point.payload,
                    point.vector,
                )
            )
        inventory = build_dense_inventory(corpus.units(), records)
    except (UnicodeDecodeError, ValueError, TypeError):
        _refuse(invalid_code)
    return tuple(points), inventory.inventory_root_sha256


def _read_bounded(
    path: Path,
    *,
    max_bytes: int,
    code: str,
    private: bool = False,
) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        _refuse(code)
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    except OSError:
        _refuse(code)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > max_bytes
            or (
                private
                and (
                    stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_nlink != 1
                )
            )
        ):
            _refuse(code)
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(data) > max_bytes or len(data) != before.st_size or not stable:
            _refuse(code)
        return data
    except InitializationRefusal:
        raise
    except OSError:
        _refuse(code)
    finally:
        os.close(descriptor)


def _verified_rfc_bytes(data: bytes) -> VerifiedRfc:
    return verify_rfc_snapshot(
        RfcByteSnapshot(
            document_sha256=hashlib.sha256(data).hexdigest(),
            document_bytes=len(data),
            data=data,
        )
    )


def _canonical_json_line(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _load_fixture_bundle(directory: Path) -> _FixtureBundle:
    manifest_bytes = _read_bounded(
        directory / "fixture-manifest.json",
        max_bytes=_MAX_FIXTURE_MANIFEST_BYTES,
        code="fixture_manifest_invalid",
    )
    try:
        manifest = _FixtureManifest.model_validate_json(manifest_bytes)
    except ValueError:
        _refuse("fixture_manifest_invalid")
    if manifest_bytes != _canonical_json_line(manifest.model_dump(mode="json")):
        _refuse("fixture_manifest_invalid")

    source_path = directory / manifest.source.filename
    source_bytes = _read_bounded(
        source_path,
        max_bytes=RfcLimits().max_bytes,
        code="fixture_source_invalid",
    )
    if hashlib.sha256(source_bytes).hexdigest() != manifest.source.sha256:
        _refuse("fixture_source_hash_mismatch")

    points_path = directory / manifest.dense_points.filename
    points_bytes = _read_bounded(
        points_path,
        max_bytes=_MAX_DENSE_POINTS_BYTES,
        code="fixture_dense_points_invalid",
    )
    if hashlib.sha256(points_bytes).hexdigest() != manifest.dense_points.sha256:
        _refuse("fixture_dense_points_hash_mismatch")

    draft = RfcSourceManifestDraft.model_validate(
        {
            "document_id": manifest.source.document_id,
            "document_version": manifest.source.document_version,
            "text_url": str(manifest.source.text_url),
            "xml_url": str(manifest.source.xml_url),
            "text_sha256": manifest.source.text_sha256,
            "xml_sha256": manifest.source.sha256,
            "downloaded_at": manifest.source.downloaded_at,
            "created_at": manifest.source.created_at,
        }
    )
    source_manifest = RfcSourceManifest.from_draft(draft)
    if source_manifest.manifest_id != manifest.source.manifest_id:
        _refuse("fixture_source_manifest_mismatch")
    try:
        document = _verified_rfc_bytes(source_bytes)
        corpus = LocalCorpus.load(
            ((document, ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)),),
            RfcLimits(),
            IndexTextPolicy(),
        )
        bm25 = Bm25Index.build(corpus.indexable())
    except ValueError:
        _refuse("fixture_source_invalid")
    if derived_corpus_sha256(corpus.units()) != manifest.derived_corpus_sha256:
        _refuse("fixture_corpus_mismatch")
    if (
        collection_name(
            manifest.derived_corpus_sha256,
            PIPELINE_VERSION,
            IndexTextPolicy().version,
        )
        != manifest.collection_name
    ):
        _refuse("fixture_collection_name_mismatch")

    points, inventory_root = _decode_dense_points(
        points_bytes,
        corpus,
        invalid_code="fixture_dense_points_invalid",
    )
    if (
        len(points) != manifest.dense_points.point_count
        or inventory_root != manifest.inventory_root_sha256
    ):
        _refuse("fixture_dense_inventory_mismatch")
    return _FixtureBundle(
        manifest,
        source_manifest,
        corpus,
        bm25,
        points,
        inventory_root,
    )


def _observe_fixture(
    bundle: _FixtureBundle, qdrant_url: str
) -> tuple[object, int, str]:
    with DenseIndex.open(qdrant_url, bundle.manifest.collection_name) as reader:
        try:
            schema = reader.collection_schema()
        except ValueError:
            _refuse("dense_collection_schema_mismatch")
        if schema.dense_vector.size != 1024 or schema.sparse_vectors:
            _refuse("dense_collection_schema_mismatch")
        count = reader.point_count()
        if count != bundle.manifest.dense_points.point_count:
            _refuse("dense_point_count_mismatch")
        try:
            inventory = build_dense_inventory(
                bundle.corpus.units(), reader.iter_records()
            )
        except ValueError:
            _refuse("dense_point_inventory_mismatch")
        if inventory.inventory_root_sha256 != bundle.inventory_root_sha256:
            _refuse("dense_point_inventory_mismatch")
        return schema, count, inventory.inventory_root_sha256


def _fixture_intent(
    bundle: _FixtureBundle,
    schema: object,
    point_count: int,
    inventory_root: str,
) -> CorpusManifestIntent:
    from specpilot.contracts.corpus_manifest import QdrantCollectionSchema

    parameters = bundle.bm25.parameters
    return CorpusManifestIntent(
        source_manifest_ids=(bundle.manifest.source.manifest_id,),
        versions=_current_versions(),
        embedding_weights_sha256=bundle.manifest.embedding_weights_sha256,
        bm25=Bm25Binding(
            tokenizer_version=TOKENIZER_VERSION,
            k1=parameters.k1,
            b=parameters.b,
            index_fingerprint=bundle.bm25.fingerprint,
        ),
        retrieval=_retrieval_binding(),
        collection_name=bundle.manifest.collection_name,
        collection_schema=QdrantCollectionSchema.model_validate(schema),
        point_count=point_count,
        derived_corpus_sha256=bundle.manifest.derived_corpus_sha256,
        inventory_root_sha256=inventory_root,
        parse_qa=(
            ParseQaEvidence(
                source_manifest_id=bundle.manifest.source.manifest_id,
                evidence_sha256=hashlib.sha256(
                    b"specpilot/fixture-parse-qa/v1\x1f"
                    + bundle.manifest.source.sha256.encode("ascii")
                ).hexdigest(),
            ),
        ),
    )


def _marker_for(
    manifest: CorpusManifest, mode: Literal["fixture", "real"]
) -> ReadyMarker:
    return ReadyMarker.create(
        source_manifest_ids=manifest.source_manifest_ids,
        corpus_manifest_id=manifest.manifest_id,
        collection_name=manifest.collection_name,
        point_count=manifest.point_count,
        inventory_root_sha256=manifest.inventory_root_sha256,
        mode=mode,
    )


def _verify_fixture_marker(
    request: FixtureInitializationRequest,
    bundle: _FixtureBundle,
    marker: ReadyMarker,
) -> ReadyMarker:
    if (
        marker.mode != "fixture"
        or marker.source_manifest_ids != (bundle.manifest.source.manifest_id,)
        or marker.collection_name != bundle.manifest.collection_name
        or marker.point_count != bundle.manifest.dense_points.point_count
        or marker.inventory_root_sha256 != bundle.inventory_root_sha256
    ):
        _refuse("ready_marker_mismatch")
    corpus_store = CorpusManifestStore(request.corpus_manifest_dir)
    try:
        manifest = corpus_store.read(marker.corpus_manifest_id)
    except (OSError, ValueError):
        _refuse("ready_marker_mismatch")
    if _marker_for(manifest, "fixture") != marker:
        _refuse("ready_marker_mismatch")
    schema, count, inventory = _observe_fixture(bundle, request.qdrant_url)
    if (
        manifest.collection_schema != schema
        or manifest.point_count != count
        or manifest.inventory_root_sha256 != inventory
    ):
        _refuse("ready_marker_mismatch")
    with DenseIndex.open(request.qdrant_url, marker.collection_name) as reader:
        snapshots = reader.snapshots()
    if not any(
        item.name == manifest.snapshot.name
        and item.checksum == manifest.snapshot.checksum
        and item.size_bytes == manifest.snapshot.size_bytes
        for item in snapshots
    ):
        _refuse("corpus_snapshot_mismatch")
    return marker


def initialize_fixture(request: FixtureInitializationRequest) -> ReadyMarker:
    """Validate committed fixture bytes before touching Qdrant, then seal once."""
    bundle = _load_fixture_bundle(request.fixture_dir)
    source_store = ManifestStore(request.source_manifest_dir)
    try:
        stored_source = source_store.create_source_v2(
            RfcSourceManifestDraft.model_validate(
                bundle.source_manifest.model_dump(exclude={"manifest_id"})
            )
        )
    except (OSError, ValueError):
        _refuse("fixture_source_manifest_mismatch")
    if stored_source.manifest_id != bundle.manifest.source.manifest_id:
        _refuse("fixture_source_manifest_mismatch")

    ready_store = ReadyMarkerStore(request.ready_dir)
    try:
        existing_marker = ready_store.read()
    except FileNotFoundError:
        existing_marker = None
    except (OSError, ValueError):
        _refuse("ready_marker_mismatch")
    if existing_marker is not None:
        return _verify_fixture_marker(request, bundle, existing_marker)

    corpus_store = CorpusManifestStore(request.corpus_manifest_dir)
    admin = QdrantClient(
        url=request.qdrant_url,
        timeout=_QDRANT_OPERATION_TIMEOUT_SECONDS,
        trust_env=False,
    )
    created = False
    frozen = False
    try:
        exists = bool(admin.collection_exists(bundle.manifest.collection_name))
        if not exists:
            with (
                corpus_store.acquire_write_lease(
                    bundle.manifest.collection_name
                ) as write_lease,
                DenseIndexWriter.create(
                    request.qdrant_url,
                    bundle.manifest.collection_name,
                    corpus_store,
                    write_lease,
                ) as writer,
            ):
                created = True
                writer.upsert(bundle.points)
        schema, point_count, inventory_root = _observe_fixture(
            bundle, request.qdrant_url
        )
        intent = _fixture_intent(bundle, schema, point_count, inventory_root)
        with corpus_store.acquire_freeze_lease(
            bundle.manifest.collection_name
        ) as freeze_lease:
            existing = corpus_store.find_by_intent(intent, lease=freeze_lease)
            if existing is None:
                corpus_store.require_publishable_intent(intent, lease=freeze_lease)
                with DenseSnapshotAdmin.open(
                    request.qdrant_url,
                    bundle.manifest.collection_name,
                    corpus_store,
                    freeze_lease,
                ) as snapshot_admin:
                    snapshot = snapshot_admin.create_snapshot()
                schema_after, count_after, inventory_after = _observe_fixture(
                    bundle, request.qdrant_url
                )
                if (schema_after, count_after, inventory_after) != (
                    schema,
                    point_count,
                    inventory_root,
                ):
                    _refuse("collection_changed_during_freeze")
                manifest = corpus_store.create(
                    CorpusManifestDraft(
                        **intent.model_dump(),
                        snapshot=QdrantSnapshotBinding(
                            name=snapshot.name,
                            checksum=snapshot.checksum,
                            size_bytes=snapshot.size_bytes,
                        ),
                        created_at=bundle.manifest.created_at,
                    ),
                    lease=freeze_lease,
                )
            else:
                manifest = existing
        frozen = True
        marker = _marker_for(manifest, "fixture")
        result = ready_store.publish(marker)
    except BaseException:
        if created and not frozen:
            _cleanup_collection(admin, bundle.manifest.collection_name)
        _close_qdrant(admin)
        raise
    admin.close()
    return result


def _private_directory(path: Path, *, code: str) -> None:
    try:
        status = path.stat(follow_symlinks=False)
    except OSError:
        _refuse(code)
    if not stat.S_ISDIR(status.st_mode) or stat.S_IMODE(status.st_mode) != 0o700:
        _refuse(code)


def _read_private_file(path: Path, *, max_bytes: int, code: str) -> bytes:
    return _read_bounded(
        path,
        max_bytes=max_bytes,
        code=code,
        private=True,
    )


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write")
        written += count


@contextmanager
def _staged_source_bindings(
    sources: tuple[tuple[str, bytes], ...],
) -> Iterator[tuple[CorpusSourceInput, ...]]:
    """Expose captured source bytes to freeze/verify without reopening input."""
    with TemporaryDirectory(prefix="specpilot-real-sources-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        bindings: list[CorpusSourceInput] = []
        for manifest_id, data in sources:
            path = root / f"{manifest_id}.xml"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                _write_all(descriptor, data)
            finally:
                os.close(descriptor)
            bindings.append(CorpusSourceInput(manifest_id, path))
        yield tuple(bindings)


def _cleanup_collection(admin: QdrantClient, collection: str) -> None:
    """Best-effort bounded cleanup that never replaces the primary failure."""
    try:
        admin.delete_collection(
            collection,
            timeout=_QDRANT_OPERATION_TIMEOUT_SECONDS,
        )
    except BaseException:
        return


def _close_qdrant(admin: QdrantClient) -> None:
    try:
        admin.close()
    except BaseException:
        return


def _observe_real_collection(
    *,
    corpus: LocalCorpus,
    collection: str,
    qdrant_url: str,
    point_count: int,
    inventory_root: str,
) -> None:
    with DenseIndex.open(qdrant_url, collection) as reader:
        schema = reader.collection_schema()
        if schema.dense_vector.size != 1024 or schema.sparse_vectors:
            _refuse("dense_collection_schema_mismatch")
        if reader.point_count() != point_count:
            _refuse("dense_point_count_mismatch")
        try:
            observed = build_dense_inventory(corpus.units(), reader.iter_records())
        except ValueError:
            _refuse("dense_point_inventory_mismatch")
        if observed.inventory_root_sha256 != inventory_root:
            _refuse("dense_point_inventory_mismatch")


def _initialize_new_real(
    request: RealInitializationRequest,
    *,
    source_dir: Path,
    corpus_dir: Path,
    sources_dir: Path,
    model_dir: Path,
) -> CorpusManifest:
    with SecureRecordDirectory.open(source_dir, create=False) as records:
        stored_source_ids = records.content_ids()
    if not stored_source_ids:
        _refuse("real_corpus_unavailable")
    source_store = ManifestStore(source_dir)
    stored_sources = tuple(
        source_store.read_source(source_id) for source_id in stored_source_ids
    )
    predecessor_ids = {
        source.predecessor_manifest_id
        for source in stored_sources
        if source.predecessor_manifest_id is not None
    }
    source_manifests = tuple(
        source
        for source in stored_sources
        if source.manifest_id not in predecessor_ids
    )
    if (
        not source_manifests
        or len({source.document_id for source in source_manifests})
        != len(source_manifests)
    ):
        _refuse("real_corpus_unavailable")
    source_snapshots: list[tuple[str, bytes]] = []
    documents = []
    created_at: datetime | None = None
    for source_manifest in source_manifests:
        if not isinstance(source_manifest, RfcSourceManifest):
            _refuse("real_corpus_unavailable")
        source_id = source_manifest.manifest_id
        source_path = sources_dir / f"{source_id}.xml"
        source_bytes = _read_private_file(
            source_path,
            max_bytes=RfcLimits().max_bytes,
            code="real_corpus_unavailable",
        )
        if hashlib.sha256(source_bytes).hexdigest() != source_manifest.xml_sha256:
            _refuse("corpus_source_mismatch")
        document = _verified_rfc_bytes(source_bytes)
        if document_identity(document.root) != (
            source_manifest.document_id,
            source_manifest.document_version,
        ):
            _refuse("corpus_source_mismatch")
        source_snapshots.append((source_id, source_bytes))
        documents.append((document, ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)))
        created_at = (
            source_manifest.created_at
            if created_at is None
            else max(created_at, source_manifest.created_at)
        )
    corpus = LocalCorpus.load(
        tuple(documents),
        RfcLimits(),
        IndexTextPolicy(),
    )
    dense_bytes = _read_private_file(
        request.corpus_dir / "dense-points.jsonl",
        max_bytes=_MAX_DENSE_POINTS_BYTES,
        code="real_dense_points_invalid",
    )
    points, inventory_root = _decode_dense_points(
        dense_bytes,
        corpus,
        invalid_code="real_dense_points_invalid",
    )
    derived = derived_corpus_sha256(corpus.units())
    derived_collection = collection_name(
        derived,
        PIPELINE_VERSION,
        IndexTextPolicy().version,
    )
    corpus_store = CorpusManifestStore(corpus_dir)
    admin = QdrantClient(
        url=request.qdrant_url,
        timeout=_QDRANT_OPERATION_TIMEOUT_SECONDS,
        trust_env=False,
    )
    created = False
    frozen = False
    try:
        if not admin.collection_exists(derived_collection):
            with (
                corpus_store.acquire_write_lease(derived_collection) as write_lease,
                DenseIndexWriter.create(
                    request.qdrant_url,
                    derived_collection,
                    corpus_store,
                    write_lease,
                ) as writer,
            ):
                created = True
                writer.upsert(points)
        _observe_real_collection(
            corpus=corpus,
            collection=derived_collection,
            qdrant_url=request.qdrant_url,
            point_count=len(points),
            inventory_root=inventory_root,
        )
        assert created_at is not None
        with _staged_source_bindings(tuple(source_snapshots)) as sources:
            result = freeze_corpus(
                FreezeCorpusRequest(
                    sources=sources,
                    model_dir=model_dir,
                    qdrant_url=request.qdrant_url,
                    collection_name=derived_collection,
                    predecessor_manifest_id=None,
                    created_at=created_at,
                ),
                source_store=source_store,
                corpus_store=corpus_store,
            )
        frozen = True
        manifest = result.manifest
    except BaseException:
        if created and not frozen:
            _cleanup_collection(admin, derived_collection)
        _close_qdrant(admin)
        raise
    admin.close()
    return manifest


def initialize_real(request: RealInitializationRequest) -> ReadyMarker:
    """Build-and-freeze once or verify an exact already-frozen real corpus.

    The read-only secure input tree contains source manifests, source XML files
    named by manifest ID, vectors, and the local model. Corpus manifests are
    published only in the separate controlled output store. A write lease is
    used only for a missing derived collection; an existing collection is
    verified byte-for-byte and never updated in place.
    """
    if not request.corpus_dir.is_absolute():
        _refuse("real_corpus_dir_not_absolute")
    if not request.corpus_manifest_dir.is_absolute():
        _refuse("real_corpus_manifest_dir_not_absolute")
    _private_directory(request.corpus_dir, code="real_corpus_unavailable")
    _private_directory(
        request.corpus_manifest_dir,
        code="real_corpus_unavailable",
    )
    input_root = request.corpus_dir.resolve(strict=True)
    output_root = request.corpus_manifest_dir.resolve(strict=True)
    ready_root = request.ready_dir.resolve(strict=False)
    if output_root.is_relative_to(input_root) or ready_root.is_relative_to(input_root):
        _refuse("real_corpus_output_overlaps_input")
    source_dir = request.corpus_dir / "source-manifests"
    corpus_dir = request.corpus_manifest_dir
    sources_dir = request.corpus_dir / "sources"
    model_dir = request.corpus_dir / "model"
    for directory in (source_dir, sources_dir, model_dir):
        _private_directory(directory, code="real_corpus_unavailable")
    try:
        with SecureRecordDirectory.open(corpus_dir, create=False) as records:
            corpus_ids = records.content_ids(allowed_non_records=frozenset({".locks"}))
        if not corpus_ids:
            manifest = _initialize_new_real(
                request,
                source_dir=source_dir,
                corpus_dir=corpus_dir,
                sources_dir=sources_dir,
                model_dir=model_dir,
            )
        else:
            corpus_store = CorpusManifestStore(corpus_dir)
            stored = tuple(corpus_store.read(corpus_id) for corpus_id in corpus_ids)
            predecessor_ids = {
                item.predecessor_manifest_id
                for item in stored
                if item.predecessor_manifest_id is not None
            }
            heads = tuple(
                item for item in stored if item.manifest_id not in predecessor_ids
            )
            if len(heads) != 1:
                _refuse("real_corpus_not_frozen")
            manifest = heads[0]
        source_snapshots = tuple(
            (
                manifest_id,
                _read_private_file(
                    sources_dir / f"{manifest_id}.xml",
                    max_bytes=RfcLimits().max_bytes,
                    code="real_corpus_unavailable",
                ),
            )
            for manifest_id in manifest.source_manifest_ids
        )
        with _staged_source_bindings(source_snapshots) as bindings:
            verified = verify_corpus(
                VerifyCorpusRequest(
                    manifest_id=manifest.manifest_id,
                    sources=bindings,
                    model_dir=model_dir,
                    qdrant_url=request.qdrant_url,
                ),
                source_store=ManifestStore(source_dir),
                corpus_store=CorpusManifestStore(corpus_dir),
            )
            verified.close()
    except InitializationRefusal:
        raise
    except CorpusManifestRefusal as error:
        _refuse(error.code)
    except (OSError, ValueError, RuntimeError):
        _refuse("real_corpus_unavailable")
    marker = _marker_for(manifest, "real")
    try:
        return ReadyMarkerStore(request.ready_dir).publish(marker)
    except (OSError, ValueError):
        _refuse("ready_marker_mismatch")
