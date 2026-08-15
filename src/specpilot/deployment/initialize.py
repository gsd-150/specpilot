"""Explicit fixture and real corpus initialization; never imported by startup."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
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
from specpilot.contracts.manifests import (
    AuthorizationConclusion,
    ComplianceAssessment,
    EvidenceSnapshot,
    OutboundLimitAssessment,
    ProviderPolicyAssessment,
    ProviderRouteBinding,
    ProviderUse,
    RfcSourceManifest,
    RfcSourceManifestDraft,
    SourceTermsAssessment,
)
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
from specpilot.ingestion._secure_fs import (
    directory_open_flags,
    open_directory_path,
    revalidate_directory_path,
)
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
_MAX_MODEL_FILES = 100_000
_MAX_MODEL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_MODEL_DEPTH = 32
_QDRANT_OPERATION_TIMEOUT_SECONDS = 10
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_FIXTURE_ROUTE = ProviderRouteBinding(
    provider_id="fixture-provider",
    endpoint_purpose="fixture-smoke",
    use=ProviderUse.ONLINE_MAIN,
)


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
    parent_descriptor: int | None = None
    try:
        parent_descriptor = open_directory_path(path.parent, create=False)
        revalidate_directory_path(path.parent, parent_descriptor)
        data = _read_bounded_at(
            parent_descriptor,
            path.name,
            max_bytes=max_bytes,
            code=code,
            private=private,
        )
        revalidate_directory_path(path.parent, parent_descriptor)
        return data
    except InitializationRefusal:
        raise
    except (OSError, RuntimeError, ValueError):
        _refuse(code)
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _file_state(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_bounded_at(
    directory_descriptor: int,
    name: str,
    *,
    max_bytes: int,
    code: str,
    private: bool = False,
) -> bytes:
    descriptor: int | None = None
    try:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        nonblocking = getattr(os, "O_NONBLOCK", 0)
        if (
            not no_follow
            or not nonblocking
            or not name
            or name != Path(name).name
            or name in {".", ".."}
            or max_bytes < 0
        ):
            _refuse(code)
        descriptor = os.open(
            name,
            os.O_RDONLY | no_follow | nonblocking,
            dir_fd=directory_descriptor,
        )
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
        named = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            len(data) > max_bytes
            or len(data) != before.st_size
            or _file_state(before) != _file_state(after)
            or _file_state(after) != _file_state(named)
            or not stat.S_ISREG(named.st_mode)
        ):
            _refuse(code)
        return data
    except InitializationRefusal:
        raise
    except OSError:
        _refuse(code)
    finally:
        if descriptor is not None:
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
    source_manifest_id: str,
    schema: object,
    point_count: int,
    inventory_root: str,
) -> CorpusManifestIntent:
    from specpilot.contracts.corpus_manifest import QdrantCollectionSchema

    parameters = bundle.bm25.parameters
    return CorpusManifestIntent(
        source_manifest_ids=(source_manifest_id,),
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
                source_manifest_id=source_manifest_id,
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
    source_manifest_id: str,
    marker: ReadyMarker,
) -> ReadyMarker:
    if (
        marker.mode != "fixture"
        or marker.source_manifest_ids != (source_manifest_id,)
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


def _fixture_assessment(created_at: datetime) -> ComplianceAssessment:
    """Return deterministic synthetic evidence for the committed fake route only."""
    at = created_at.astimezone(UTC)
    premise = "Only committed synthetic fixture excerpts may reach the fake provider."
    snapshot_hash = hashlib.sha256(
        b"specpilot/fixture-only-compliance-evidence/v1"
    ).hexdigest()
    snapshot = EvidenceSnapshot.model_validate(
        {
            "snapshot_url": "https://example.test/specpilot/fixture-only-evidence",
            "snapshot_sha256": snapshot_hash,
            "captured_at": at,
        }
    )
    return ComplianceAssessment(
        source_terms=SourceTermsAssessment(
            terms_snapshot=snapshot,
            summary="Synthetic fixture terms for offline packaged-demo verification.",
            uncertainty=("This evidence must never authorize a live provider.",),
        ),
        provider_policy=ProviderPolicyAssessment(
            policy_snapshot=snapshot,
            retention_summary="The fake provider stores no fixture requests.",
            training_summary="The fake provider performs no training.",
            region_summary="The fake provider runs inside the local test boundary.",
            subprocessor_summary="The fake provider has no subprocessors.",
            uncertainty=("This evidence applies only to the fake fixture route.",),
        ),
        outbound_limit=OutboundLimitAssessment(
            premise=premise,
            premise_sha256=hashlib.sha256(premise.encode("utf-8")).hexdigest(),
        ),
        author_conclusion=AuthorizationConclusion(
            authorized=True,
            authorization_statement=(
                "This synthetic fixture authorizes only the exact fake provider route."
            ),
            author_id="fixture-only-synthetic-author",
            provider_id=_FIXTURE_ROUTE.provider_id,
            endpoint_purpose=_FIXTURE_ROUTE.endpoint_purpose,
            authored_at=at,
            expires_at=datetime(2036, 8, 15, tzinfo=UTC),
        ),
    )


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
    try:
        authorized_source = source_store.create_successor_v2(
            stored_source,
            assessment=_fixture_assessment(bundle.manifest.source.created_at),
            route_binding=_FIXTURE_ROUTE,
            created_at=bundle.manifest.source.created_at,
        )
    except (OSError, ValueError):
        _refuse("fixture_source_manifest_mismatch")

    ready_store = ReadyMarkerStore(request.ready_dir)
    try:
        existing_marker = ready_store.read()
    except FileNotFoundError:
        existing_marker = None
    except (OSError, ValueError):
        _refuse("ready_marker_mismatch")
    if existing_marker is not None:
        return _verify_fixture_marker(
            request, bundle, authorized_source.manifest_id, existing_marker
        )

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
        intent = _fixture_intent(
            bundle,
            authorized_source.manifest_id,
            schema,
            point_count,
            inventory_root,
        )
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
            _cleanup_unbound_collection(
                admin,
                corpus_store,
                bundle.manifest.collection_name,
            )
        _close_qdrant(admin)
        raise
    admin.close()
    return result


def _private_directory_status(value: os.stat_result) -> bool:
    return stat.S_ISDIR(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o700


@dataclass(frozen=True, slots=True)
class _PinnedRealDirectories:
    input_root: Path
    output_root: Path
    input_fd: int
    source_manifest_fd: int
    sources_fd: int
    model_fd: int
    output_fd: int
    input_states: tuple[tuple[str, tuple[int, ...]], ...]

    def revalidate(self) -> None:
        """Require every input name to retain its originally pinned inode."""
        try:
            revalidate_directory_path(self.input_root, self.input_fd)
            revalidate_directory_path(self.output_root, self.output_fd)
            input_status = os.fstat(self.input_fd)
            if (
                not _private_directory_status(input_status)
                or _file_state(input_status) != dict(self.input_states)["."]
            ):
                raise OSError("real input root changed")
            for name, descriptor in (
                ("source-manifests", self.source_manifest_fd),
                ("sources", self.sources_fd),
                ("model", self.model_fd),
            ):
                opened = os.fstat(descriptor)
                named = os.stat(
                    name,
                    dir_fd=self.input_fd,
                    follow_symlinks=False,
                )
                if (
                    not _private_directory_status(opened)
                    or _file_state(opened) != dict(self.input_states)[name]
                    or _file_state(named) != _file_state(opened)
                ):
                    raise OSError("real input child changed")
            if not _private_directory_status(os.fstat(self.output_fd)):
                raise OSError("real output root changed")
        except (OSError, RuntimeError, ValueError):
            _refuse("real_corpus_unavailable")


@dataclass(slots=True)
class _ModelCopyBudget:
    files: int = 0
    bytes: int = 0


def _open_private_child(parent_descriptor: int, name: str) -> int:
    descriptor = os.open(
        name,
        directory_open_flags(),
        dir_fd=parent_descriptor,
    )
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if (
        not _private_directory_status(opened)
        or _file_state(opened) != _file_state(named)
    ):
        os.close(descriptor)
        raise OSError("private child identity mismatch")
    return descriptor


def _descriptor_directory_alias(descriptor: int) -> Path | None:
    """Return a Linux descriptor path only when it names the pinned directory."""
    alias = Path("/proc/self/fd") / str(descriptor)
    try:
        observed = alias.stat()
        pinned = os.fstat(descriptor)
    except OSError:
        return None
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_dev != pinned.st_dev
        or observed.st_ino != pinned.st_ino
    ):
        return None
    return alias


def _copy_pinned_model_tree(
    source_descriptor: int,
    target_descriptor: int,
    budget: _ModelCopyBudget,
    *,
    depth: int = 0,
) -> None:
    """Copy a descriptor-pinned regular tree when no directory-fd alias exists."""
    if depth > _MAX_MODEL_DEPTH:
        raise ValueError("model directory is too deep")
    source_before = os.fstat(source_descriptor)
    if not stat.S_ISDIR(source_before.st_mode):
        raise ValueError("model root is not a directory")
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking:
        raise RuntimeError("required secure filesystem primitives unavailable")

    for name in sorted(os.listdir(source_descriptor)):
        if (
            not name
            or name in {".", ".."}
            or name != Path(name).name
            or len(os.fsencode(name)) > 255
        ):
            raise ValueError("model entry name is invalid")
        named_before = os.stat(
            name,
            dir_fd=source_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(named_before.st_mode):
            source_child = os.open(
                name,
                directory_open_flags(),
                dir_fd=source_descriptor,
            )
            target_child: int | None = None
            try:
                if _file_state(os.fstat(source_child)) != _file_state(named_before):
                    raise FileExistsError(name)
                os.mkdir(name, mode=0o700, dir_fd=target_descriptor)
                target_child = os.open(
                    name,
                    directory_open_flags(),
                    dir_fd=target_descriptor,
                )
                _copy_pinned_model_tree(
                    source_child,
                    target_child,
                    budget,
                    depth=depth + 1,
                )
                named_after = os.stat(
                    name,
                    dir_fd=source_descriptor,
                    follow_symlinks=False,
                )
                if _file_state(os.fstat(source_child)) != _file_state(named_after):
                    raise FileExistsError(name)
            finally:
                if target_child is not None:
                    os.close(target_child)
                os.close(source_child)
            continue
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError("model tree contains a non-regular entry")

        source_file = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | nonblocking,
            dir_fd=source_descriptor,
        )
        target_file: int | None = None
        try:
            opened = os.fstat(source_file)
            if _file_state(opened) != _file_state(named_before):
                raise FileExistsError(name)
            budget.files += 1
            budget.bytes += opened.st_size
            if budget.files > _MAX_MODEL_FILES or budget.bytes > _MAX_MODEL_BYTES:
                raise ValueError("model tree exceeds its capture bound")
            target_file = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target_descriptor,
            )
            copied = 0
            while True:
                chunk = os.read(source_file, 1024 * 1024)
                if not chunk:
                    break
                _write_all(target_file, chunk)
                copied += len(chunk)
            source_after = os.fstat(source_file)
            named_after = os.stat(
                name,
                dir_fd=source_descriptor,
                follow_symlinks=False,
            )
            if (
                copied != opened.st_size
                or _file_state(source_after) != _file_state(opened)
                or _file_state(named_after) != _file_state(opened)
            ):
                raise FileExistsError(name)
            target_status = os.fstat(target_file)
            if (
                not stat.S_ISREG(target_status.st_mode)
                or stat.S_IMODE(target_status.st_mode) != 0o600
                or target_status.st_size != copied
            ):
                raise FileExistsError(name)
        finally:
            if target_file is not None:
                os.close(target_file)
            os.close(source_file)

    if _file_state(os.fstat(source_descriptor)) != _file_state(source_before):
        raise FileExistsError("model directory changed during capture")


@contextmanager
def _validated_model_view(
    pinned: _PinnedRealDirectories,
    path: Path,
) -> Iterator[Path]:
    try:
        yield path
    except BaseException:
        with suppress(InitializationRefusal):
            pinned.revalidate()
        raise
    else:
        pinned.revalidate()


@contextmanager
def _pinned_model_view(pinned: _PinnedRealDirectories) -> Iterator[Path]:
    """Expose only the pinned model inode to legacy path-based model loaders."""
    pinned.revalidate()
    alias = _descriptor_directory_alias(pinned.model_fd)
    if alias is not None:
        with _validated_model_view(pinned, alias) as path:
            yield path
        return

    try:
        with TemporaryDirectory(prefix="specpilot-real-model-") as temporary:
            root = Path(temporary).resolve(strict=True)
            root.chmod(0o700)
            target_descriptor = open_directory_path(root, create=False)
            try:
                _copy_pinned_model_tree(
                    pinned.model_fd,
                    target_descriptor,
                    _ModelCopyBudget(),
                )
            finally:
                os.close(target_descriptor)
            pinned.revalidate()
            with _validated_model_view(pinned, root) as path:
                yield path
    except InitializationRefusal:
        raise
    except (OSError, RuntimeError, ValueError):
        _refuse("real_corpus_unavailable")


@contextmanager
def _pinned_real_directories(
    input_root: Path, output_root: Path
) -> Iterator[_PinnedRealDirectories]:
    descriptors: list[int] = []
    try:
        input_fd = open_directory_path(input_root, create=False)
        descriptors.append(input_fd)
        if not _private_directory_status(os.fstat(input_fd)):
            raise OSError("real input root is not private")
        children: dict[str, int] = {}
        for name in ("source-manifests", "sources", "model"):
            child = _open_private_child(input_fd, name)
            descriptors.append(child)
            children[name] = child
        output_fd = open_directory_path(output_root, create=False)
        descriptors.append(output_fd)
        if not _private_directory_status(os.fstat(output_fd)):
            raise OSError("real output root is not private")
        states = ((".", _file_state(os.fstat(input_fd))),) + tuple(
            (name, _file_state(os.fstat(children[name])))
            for name in ("source-manifests", "sources", "model")
        )
        pinned = _PinnedRealDirectories(
            input_root=input_root,
            output_root=output_root,
            input_fd=input_fd,
            source_manifest_fd=children["source-manifests"],
            sources_fd=children["sources"],
            model_fd=children["model"],
            output_fd=output_fd,
            input_states=states,
        )
        pinned.revalidate()
        yield pinned
    except InitializationRefusal:
        raise
    except (OSError, RuntimeError, ValueError):
        _refuse("real_corpus_unavailable")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _staged_source_manifest_store(
    pinned: _PinnedRealDirectories,
) -> Iterator[tuple[ManifestStore, tuple[str, ...]]]:
    """Capture descriptor-read manifests before any path-based consumer runs."""
    source_path = pinned.input_root / "source-manifests"
    try:
        with SecureRecordDirectory.from_fd(
            source_path,
            pinned.source_manifest_fd,
        ) as records:
            source_ids = records.content_ids()
            captured = tuple(
                (
                    source_id,
                    records.read(
                        f"{source_id}.json",
                        max_bytes=256 * 1024,
                    ),
                )
                for source_id in source_ids
            )
        pinned.revalidate()
    except (OSError, RuntimeError, ValueError):
        _refuse("real_corpus_unavailable")

    with TemporaryDirectory(prefix="specpilot-real-manifests-") as temporary:
        root = Path(temporary).resolve(strict=True)
        root.chmod(0o700)
        root_descriptor = open_directory_path(root, create=False)
        try:
            for source_id, data in captured:
                descriptor = os.open(
                    f"{source_id}.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=root_descriptor,
                )
                try:
                    _write_all(descriptor, data)
                finally:
                    os.close(descriptor)
        finally:
            os.close(root_descriptor)
        yield ManifestStore(root), source_ids


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
        root = Path(temporary).resolve(strict=True)
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


def _cleanup_unbound_collection(
    admin: QdrantClient,
    corpus_store: CorpusManifestStore,
    collection: str,
) -> None:
    """Delete only while an owned lease proves no durable freeze can bind it."""
    try:
        with corpus_store.acquire_freeze_lease(collection) as lease:
            if corpus_store.has_collection_binding(collection, lease=lease):
                return
            _cleanup_collection(admin, collection)
    except BaseException:
        # Any lease, decode, filesystem, or cleanup uncertainty preserves the
        # collection. A false preserve is recoverable; deleting a durable
        # manifest's collection is not.
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
    pinned: _PinnedRealDirectories,
    source_store: ManifestStore,
    stored_source_ids: tuple[str, ...],
    corpus_store: CorpusManifestStore,
    model_dir: Path,
) -> CorpusManifest:
    pinned.revalidate()
    if not stored_source_ids:
        _refuse("real_corpus_unavailable")
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
        source_bytes = _read_bounded_at(
            pinned.sources_fd,
            f"{source_id}.xml",
            max_bytes=RfcLimits().max_bytes,
            code="real_corpus_unavailable",
            private=True,
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
    dense_bytes = _read_bounded_at(
        pinned.input_fd,
        "dense-points.jsonl",
        max_bytes=_MAX_DENSE_POINTS_BYTES,
        code="real_dense_points_invalid",
        private=True,
    )
    pinned.revalidate()
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
            pinned.revalidate()
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
            pinned.revalidate()
        frozen = True
        manifest = result.manifest
    except BaseException:
        if created and not frozen:
            _cleanup_unbound_collection(
                admin,
                corpus_store,
                derived_collection,
            )
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
    try:
        # Preserve the stable overlap refusal before validating the input-tree
        # inventory. This check has no side effects; the same identities are
        # checked again after the root and output descriptors are pinned.
        initial_input_root = request.corpus_dir.resolve(strict=True)
        initial_output_root = request.corpus_manifest_dir.resolve(strict=True)
        initial_ready_root = request.ready_dir.resolve(strict=False)
        if initial_output_root.is_relative_to(
            initial_input_root
        ) or initial_ready_root.is_relative_to(initial_input_root):
            _refuse("real_corpus_output_overlaps_input")
        with _pinned_real_directories(
            request.corpus_dir,
            request.corpus_manifest_dir,
        ) as pinned:
            input_root = request.corpus_dir.resolve(strict=True)
            output_root = request.corpus_manifest_dir.resolve(strict=True)
            ready_root = request.ready_dir.resolve(strict=False)
            pinned.revalidate()
            if output_root.is_relative_to(
                input_root
            ) or ready_root.is_relative_to(input_root):
                _refuse("real_corpus_output_overlaps_input")
            corpus_dir = request.corpus_manifest_dir
            corpus_store = CorpusManifestStore.from_fd(
                corpus_dir,
                pinned.output_fd,
            )
            with (
                _pinned_model_view(pinned) as model_dir,
                _staged_source_manifest_store(pinned) as staged_source_store,
            ):
                source_store, stored_source_ids = staged_source_store
                with SecureRecordDirectory.from_fd(
                    corpus_dir,
                    pinned.output_fd,
                ) as records:
                    corpus_ids = records.content_ids(
                        allowed_non_records=frozenset({".locks"})
                    )
                pinned.revalidate()
                if not corpus_ids:
                    manifest = _initialize_new_real(
                        request,
                        pinned=pinned,
                        source_store=source_store,
                        stored_source_ids=stored_source_ids,
                        corpus_store=corpus_store,
                        model_dir=model_dir,
                    )
                else:
                    stored = tuple(
                        corpus_store.read(corpus_id) for corpus_id in corpus_ids
                    )
                    pinned.revalidate()
                    predecessor_ids = {
                        item.predecessor_manifest_id
                        for item in stored
                        if item.predecessor_manifest_id is not None
                    }
                    heads = tuple(
                        item
                        for item in stored
                        if item.manifest_id not in predecessor_ids
                    )
                    if len(heads) != 1:
                        _refuse("real_corpus_not_frozen")
                    manifest = heads[0]
                source_snapshots = tuple(
                    (
                        manifest_id,
                        _read_bounded_at(
                            pinned.sources_fd,
                            f"{manifest_id}.xml",
                            max_bytes=RfcLimits().max_bytes,
                            code="real_corpus_unavailable",
                            private=True,
                        ),
                    )
                    for manifest_id in manifest.source_manifest_ids
                )
                pinned.revalidate()
                with _staged_source_bindings(source_snapshots) as bindings:
                    verified = verify_corpus(
                        VerifyCorpusRequest(
                            manifest_id=manifest.manifest_id,
                            sources=bindings,
                            model_dir=model_dir,
                            qdrant_url=request.qdrant_url,
                        ),
                        source_store=source_store,
                        corpus_store=corpus_store,
                    )
                    verified.close()
                pinned.revalidate()
                marker = _marker_for(manifest, "real")
                try:
                    return ReadyMarkerStore(request.ready_dir).publish(marker)
                except (OSError, ValueError):
                    _refuse("ready_marker_mismatch")
    except InitializationRefusal:
        raise
    except CorpusManifestRefusal as error:
        _refuse(error.code)
    except (OSError, ValueError, RuntimeError):
        _refuse("real_corpus_unavailable")
    raise AssertionError("unreachable")
