# Immutable Corpus Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seal and verify the existing RFC 9110/9112 corpus with a content-addressed corpus manifest, a real Qdrant snapshot, a complete payload/vector inventory, and an application-level write-revocation boundary.

**Architecture:** A strict Pydantic contract records a non-persistent freeze intent and a snapshot-bearing immutable manifest. A secure create-only store doubles as the durable frozen-collection registry and issues shared writer or exclusive freeze leases. Freeze and verify rebuild the local corpus and BM25, rerun parse QA, inspect every Qdrant payload and vector, and fail closed on any mismatch.

**Tech Stack:** Python >=3.12,<3.15, Pydantic 2, qdrant-client 1.12.x/Qdrant 1.12.4, `fcntl.flock`, pytest, Ruff, mypy.

## Global Constraints

- Preserve the current real corpus: RFC 9110 then RFC 9112, 1,922 points (1,907 clauses and 15 tables), vector width 1,024, cosine distance.
- Preserve BM25 fingerprint `8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3`.
- Preserve derived corpus SHA-256 `46616bd050308f6f77782afe8706b8e2d8f577de9b9b698e228e1c52b40596eb` and collection `specpilot_ff4841e2d846388014efa06870fbbdb7`.
- Canonicalize source order by `(document_id, document_version, manifest_id)`; do not sort sources by manifest ID and do not sort input inside `Bm25Index.build()`.
- Sort units by `unit_id` only for derived-corpus and inventory hashes.
- Keep Qdrant point payloads at the existing six locator fields; never add text, ordinal, numeric path, child span, or vectors to payloads or manifests.
- Keep `TOKENIZER_VERSION="bm25-rfc/v1"`, `PIPELINE_VERSION="clause/v1"`, and `IndexTextPolicy.version="index-text/v1"`; parser/chunker versions enter the manifest but not the existing collection-name preimage.
- Vector inventory hashes pack exactly 1,024 finite values as little-endian IEEE-754 float32 bytes.
- Every SpecPilot collection mutation requires a live shared writer lease; freeze owns an exclusive lease until the manifest hard-link and directory fsync complete.
- The manifest file set is the frozen registry. Do not add a second registry JSON file.
- Manifest directories/files are 0700/0600, create-only, canonical JSON, single-link regular files, and read with `O_NOFOLLOW`/`O_NONBLOCK` through pinned directory descriptors.
- CLI success emits identifiers, hashes, counts, and snapshot metadata only. CLI failure emits one stable code to stderr and no source or exception text.
- Restricted source, unit inventories, payloads, vectors, and individual content hashes remain ignored and uncommitted.
- Keep every existing source-manifest v1/v2 golden byte count and ID unchanged.
- Do not rebuild or re-embed the current collection, change gold, rerun pooling, or restore snapshots automatically.

## File Map

### New production files

- `src/specpilot/contracts/corpus_manifest.py` — frozen manifest, intent, retrieval, schema, QA, and snapshot contracts.
- `src/specpilot/manifests/_secure_records.py` — reusable descriptor-pinned create-only JSON record primitives.
- `src/specpilot/manifests/corpus_store.py` — corpus manifest persistence, intent uniqueness, collection leases, and frozen registry.
- `src/specpilot/retrieval/protocol.py` — whole-unit retrieval locators, §8.5.1 deduplication identity, and stable numeric tie keys.
- `src/specpilot/corpus/dense_inventory.py` — derived corpus hash, vector hash, QA evidence hash, and full point/content/vector inventory root.
- `src/specpilot/corpus/freezing.py` — source canonicalization plus freeze and load-time verification orchestration.

### Modified production files

- `src/specpilot/manifests/store.py` — use `_secure_records.py` without changing source-manifest behavior.
- `src/specpilot/corpus/walk.py` — publish the RFCXML parser contract version.
- `src/specpilot/corpus/indexable.py` — publish the chunker version and carry local unit ordinal.
- `src/specpilot/corpus/qa.py` — publish and hash deterministic QA evidence.
- `src/specpilot/retrieval/local.py` — reject duplicate unit IDs and expose a read-only unit tuple.
- `src/specpilot/retrieval/hybrid.py` — fuse by full retrieval identity and exact stable tie key.
- `src/specpilot/retrieval/dense.py` — split read, writer, and snapshot-admin capabilities; expose normalized schema and complete records.
- `src/specpilot/cli.py` — add `corpus freeze` and `corpus verify`, and use the read-only dense API for pooling.

### New and modified tests

- `tests/helpers/corpus_manifest_factory.py`
- `tests/unit/contracts/test_corpus_manifest.py`
- `tests/unit/manifests/test_secure_records.py`
- `tests/unit/manifests/test_corpus_manifest_store.py`
- `tests/unit/manifests/test_corpus_collection_leases.py`
- `tests/unit/corpus/test_indexable.py`
- `tests/unit/corpus/test_dense_inventory.py`
- `tests/unit/corpus/test_corpus_freezing.py`
- `tests/unit/retrieval/test_hybrid.py`
- `tests/unit/retrieval/test_dense.py`
- `tests/cli/test_corpus_manifest.py`
- `tests/integration/qdrant/test_collection.py`
- `tests/integration/qdrant/test_corpus_freeze.py`

### Completion documentation

- `docs/superpowers/plans/2026-08-08-w2-corpus-and-retrieval.md`
- `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`
- `docs/reports/2026-08-09-corpus-manifest-freeze.md`

---

### Task 1: Define the immutable corpus manifest contract

**Files:**
- Create: `src/specpilot/contracts/corpus_manifest.py`
- Create: `tests/helpers/corpus_manifest_factory.py`
- Create: `tests/unit/contracts/test_corpus_manifest.py`
- Verify unchanged: `tests/unit/manifests/test_source_manifest_v2.py`

**Interfaces:**
- Consumes: `Identifier`, `Sha256`, `canonical_sha256()` and `canonical_json()`.
- Produces: `CorpusManifestIntent`, `CorpusManifestDraft`, `CorpusManifest`, `CorpusComponentVersions`, `Bm25Binding`, `RetrievalProtocolBinding`, `QdrantCollectionSchema`, `ParseQaEvidence`, and `QdrantSnapshotBinding`.

- [ ] **Step 1: Add a complete manifest fixture factory**

Create `tests/helpers/corpus_manifest_factory.py` with concrete, reusable values:

```python
from datetime import UTC, datetime

from specpilot.contracts.corpus_manifest import (
    Bm25Binding,
    CorpusComponentVersions,
    CorpusManifestDraft,
    CorpusManifestIntent,
    DenseQueryParameters,
    DenseVectorSchema,
    HnswSchema,
    LocatorFieldSchema,
    ParseQaEvidence,
    QdrantCollectionSchema,
    QdrantSnapshotBinding,
    RetrievalProtocolBinding,
)

SOURCE_IDS = ("a" * 64, "b" * 64)


def corpus_intent(**changes: object) -> CorpusManifestIntent:
    values: dict[str, object] = {
        "predecessor_manifest_id": None,
        "source_manifest_ids": SOURCE_IDS,
        "versions": CorpusComponentVersions(
            parser="rfcxml-v3/v1",
            chunker="rfc-clause-table/v1",
            index_text="index-text/v1",
            embedding_pipeline="clause/v1",
        ),
        "embedding_weights_sha256": "c" * 64,
        "bm25": Bm25Binding(
            tokenizer_version="bm25-rfc/v1",
            k1=1.2,
            b=0.75,
            index_fingerprint="d" * 64,
        ),
        "retrieval": RetrievalProtocolBinding(
            dense_top_k=20,
            bm25_top_k=20,
            rrf_k=60,
            final_top_k=5,
            deduplication_key=(
                "corpus_manifest_id", "document_id", "clause_id", "child_span"
            ),
            stable_tie_key=(
                "document_id", "numeric_clause_path", "child_start"
            ),
            dense_query=DenseQueryParameters(
                hnsw_ef=None,
                exact=False,
                indexed_only=False,
            ),
        ),
        "collection_name": "specpilot_0123456789abcdef0123456789abcdef",
        "collection_schema": QdrantCollectionSchema(
            dense_vector=DenseVectorSchema(
                name=None,
                size=1024,
                distance="cosine",
                datatype="float32",
                on_disk=False,
                vector_quantization_sha256=None,
            ),
            hnsw=HnswSchema(
                m=16,
                ef_construct=100,
                full_scan_threshold=10000,
                max_indexing_threads=0,
                on_disk=False,
                payload_m=None,
            ),
            collection_quantization_sha256=None,
            sparse_vectors=(),
            payload_indexes=(),
            locator_payload=(
                LocatorFieldSchema(name="unit_id", value_type="keyword", nullable=False, payload_indexed=False),
                LocatorFieldSchema(name="kind", value_type="keyword", nullable=False, payload_indexed=False),
                LocatorFieldSchema(name="document_id", value_type="keyword", nullable=False, payload_indexed=False),
                LocatorFieldSchema(name="document_version", value_type="keyword", nullable=False, payload_indexed=False),
                LocatorFieldSchema(name="section_number", value_type="keyword", nullable=True, payload_indexed=False),
                LocatorFieldSchema(name="section_path", value_type="keyword", nullable=False, payload_indexed=False),
            ),
        ),
        "point_count": 1922,
        "derived_corpus_sha256": "e" * 64,
        "inventory_root_sha256": "f" * 64,
        "parse_qa": (
            ParseQaEvidence(source_manifest_id=SOURCE_IDS[0], evidence_sha256="1" * 64),
            ParseQaEvidence(source_manifest_id=SOURCE_IDS[1], evidence_sha256="2" * 64),
        ),
    }
    values.update(changes)
    return CorpusManifestIntent(**values)


def corpus_draft(**changes: object) -> CorpusManifestDraft:
    values = corpus_intent().model_dump()
    values.update(
        snapshot=QdrantSnapshotBinding(
            name="specpilot.snapshot", checksum="3" * 64, size_bytes=4096
        ),
        created_at=datetime(2026, 8, 9, 11, tzinfo=UTC),
    )
    values.update(changes)
    return CorpusManifestDraft(**values)
```

- [ ] **Step 2: Write strict contract and content-addressing tests**

Tests must assert frozen models, `extra="forbid"`, UTC normalization, finite BM25 values, positive integer fields, source uniqueness, QA/source equality, locator schema uniqueness/order, vector-size agreement, canonical golden byte count/ID, and field sensitivity:

```python
def test_manifest_is_content_addressed_and_round_trips() -> None:
    draft = corpus_draft()
    manifest = CorpusManifest.from_draft(draft)

    assert manifest.manifest_id == canonical_sha256(draft)
    assert CorpusManifest.model_validate_json(
        canonical_json(manifest, include_manifest_id=True)
    ) == manifest
    with pytest.raises(ValidationError):
        manifest.point_count = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("embedding_weights_sha256", "9" * 64),
        ("point_count", 1923),
        ("inventory_root_sha256", "8" * 64),
        ("created_at", datetime(2026, 8, 9, 12, tzinfo=UTC)),
        ("snapshot", QdrantSnapshotBinding(name="other.snapshot", checksum="7" * 64, size_bytes=4096)),
    ],
)
def test_every_bound_change_produces_a_new_manifest_id(field: str, value: object) -> None:
    first = CorpusManifest.from_draft(corpus_draft())
    second = CorpusManifest.from_draft(corpus_draft(**{field: value}))
    assert second.manifest_id != first.manifest_id
```

The finalized fixture above has canonical draft byte count `2585` and exact
ID `d477eed26ce3a56d41286f18fbba711926abe9b38f0430af8c451c9a48a277bf`.
Hard-code both constants and assert
`len(canonical_json(corpus_draft())) == 2585` plus the exact digest; do not
regenerate either expected value inside the assertion. Together they are the
v1 compatibility tripwire.

- [ ] **Step 3: Run the contract test and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/contracts/test_corpus_manifest.py -q`

Expected: FAIL during collection because `specpilot.contracts.corpus_manifest` does not exist.

- [ ] **Step 4: Implement the strict nested contract models**

Use frozen `BaseModel` subclasses and a non-persistent intent base:

```python
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        if _RFC3339_TIMESTAMP.fullmatch(value) is None:
            raise ValueError("created_at must be an RFC3339 timestamp")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("created_at must be an RFC3339 timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return value.astimezone(UTC)


CollectionName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
]
SnapshotName = CollectionName


class CorpusComponentVersions(_FrozenModel):
    parser: Identifier
    chunker: Identifier
    index_text: Identifier
    embedding_pipeline: Identifier


class Bm25Binding(_FrozenModel):
    tokenizer_version: Identifier
    k1: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    b: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    index_fingerprint: Sha256


class DenseQueryParameters(_FrozenModel):
    hnsw_ef: Annotated[int, Field(strict=True, gt=0)] | None
    exact: StrictBool
    indexed_only: StrictBool


class RetrievalProtocolBinding(_FrozenModel):
    dense_top_k: Annotated[int, Field(strict=True, gt=0)]
    bm25_top_k: Annotated[int, Field(strict=True, gt=0)]
    rrf_k: Annotated[int, Field(strict=True, gt=0)]
    final_top_k: Annotated[int, Field(strict=True, gt=0)]
    deduplication_key: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    stable_tie_key: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    dense_query: DenseQueryParameters


class HnswSchema(_FrozenModel):
    m: Annotated[int, Field(strict=True, gt=0)]
    ef_construct: Annotated[int, Field(strict=True, gt=0)]
    full_scan_threshold: Annotated[int, Field(strict=True, ge=0)]
    max_indexing_threads: Annotated[int, Field(strict=True, ge=0)] | None
    on_disk: StrictBool
    payload_m: Annotated[int, Field(strict=True, gt=0)] | None


class DenseVectorSchema(_FrozenModel):
    name: None = None
    size: Annotated[int, Field(strict=True, gt=0)]
    distance: Literal["cosine"]
    datatype: Literal["float32"]
    on_disk: StrictBool
    vector_quantization_sha256: Sha256 | None


class SparseVectorSchema(_FrozenModel):
    name: Identifier
    config_sha256: Sha256


class PayloadIndexSchema(_FrozenModel):
    field_name: Identifier
    data_type: Identifier
    params_sha256: Sha256 | None


class LocatorFieldSchema(_FrozenModel):
    name: Identifier
    value_type: Literal["keyword", "integer"]
    nullable: StrictBool
    payload_indexed: StrictBool


class QdrantCollectionSchema(_FrozenModel):
    dense_vector: DenseVectorSchema
    hnsw: HnswSchema
    collection_quantization_sha256: Sha256 | None
    sparse_vectors: tuple[SparseVectorSchema, ...]
    payload_indexes: tuple[PayloadIndexSchema, ...]
    locator_payload: tuple[LocatorFieldSchema, ...]

    @model_validator(mode="after")
    def _canonical_schema_order(self) -> Self:
        if tuple(item.name for item in self.sparse_vectors) != tuple(
            sorted(item.name for item in self.sparse_vectors)
        ):
            raise ValueError("sparse vector schema is not canonically ordered")
        if tuple(item.field_name for item in self.payload_indexes) != tuple(
            sorted(item.field_name for item in self.payload_indexes)
        ):
            raise ValueError("payload index schema is not canonically ordered")
        expected = (
            "unit_id", "kind", "document_id", "document_version",
            "section_number", "section_path",
        )
        if tuple(item.name for item in self.locator_payload) != expected:
            raise ValueError("locator payload schema is not locator-payload/v1")
        return self


class ParseQaEvidence(_FrozenModel):
    source_manifest_id: Sha256
    evidence_sha256: Sha256


class QdrantSnapshotBinding(_FrozenModel):
    name: SnapshotName
    checksum: Sha256
    size_bytes: Annotated[int, Field(strict=True, gt=0)]


class CorpusManifestIntent(_FrozenModel):
    schema_version: Literal["corpus-manifest/v1"] = "corpus-manifest/v1"
    predecessor_manifest_id: Sha256 | None = None
    source_manifest_ids: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    versions: CorpusComponentVersions
    embedding_weights_sha256: Sha256
    bm25: Bm25Binding
    retrieval: RetrievalProtocolBinding
    collection_name: CollectionName
    collection_schema: QdrantCollectionSchema
    point_count: Annotated[int, Field(strict=True, gt=0)]
    derived_corpus_sha256: Sha256
    inventory_root_sha256: Sha256
    parse_qa: Annotated[tuple[ParseQaEvidence, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _cross_validate(self) -> Self:
        if len(set(self.source_manifest_ids)) != len(self.source_manifest_ids):
            raise ValueError("a source manifest is listed twice")
        if tuple(item.source_manifest_id for item in self.parse_qa) != self.source_manifest_ids:
            raise ValueError("parse QA must cover sources in canonical order")
        if self.collection_schema.dense_vector.size != 1024:
            raise ValueError("the corpus dense vector must be 1024 wide")
        if self.collection_schema.sparse_vectors:
            raise ValueError("corpus-manifest/v1 does not support sparse vectors")
        return self


class CorpusManifestDraft(CorpusManifestIntent):
    snapshot: QdrantSnapshotBinding
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def _normalize_created_at(cls, value: object) -> datetime:
        return _utc_timestamp(value)

    @property
    def intent(self) -> CorpusManifestIntent:
        return CorpusManifestIntent.model_validate(
            self.model_dump(exclude={"snapshot", "created_at", "manifest_id"})
        )


class CorpusManifest(CorpusManifestDraft):
    manifest_id: Sha256

    @model_validator(mode="after")
    def _verify_manifest_id(self) -> Self:
        if self.manifest_id != canonical_sha256(self):
            raise ValueError("manifest_id does not match canonical content")
        return self

    @classmethod
    def from_draft(cls, draft: CorpusManifestDraft) -> CorpusManifest:
        return cls(manifest_id=canonical_sha256(draft), **draft.model_dump())
```

Normalize timestamps to UTC with the same strict RFC3339 rules as source manifests. Hash normalized optional Qdrant sub-configs rather than storing version-specific untyped response objects.

- [ ] **Step 5: Run contract and source-manifest compatibility tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/contracts/test_corpus_manifest.py tests/unit/manifests/test_source_manifest.py tests/unit/manifests/test_source_manifest_v2.py -q
```

Expected: PASS, including unchanged source-manifest golden values.

- [ ] **Step 6: Run static checks for the new contract**

Run:

```bash
.venv/bin/python -m ruff check src/specpilot/contracts/corpus_manifest.py tests/helpers/corpus_manifest_factory.py tests/unit/contracts/test_corpus_manifest.py
.venv/bin/python -m mypy src/specpilot/contracts/corpus_manifest.py
```

Expected: both commands exit 0.

- [ ] **Step 7: Commit the contract**

```bash
git add src/specpilot/contracts/corpus_manifest.py tests/helpers/corpus_manifest_factory.py tests/unit/contracts/test_corpus_manifest.py
git commit -m "feat: define immutable corpus manifest"
```

---

### Task 2: Extract descriptor-pinned secure record primitives

**Files:**
- Create: `src/specpilot/manifests/_secure_records.py`
- Create: `tests/unit/manifests/test_secure_records.py`
- Modify: `src/specpilot/manifests/store.py`
- Verify: `tests/unit/manifests/test_manifest_store.py`

**Interfaces:**
- Consumes: `open_directory_path()`, `revalidate_directory_path()`, and `create_private_file()` from `ingestion._secure_fs`.
- Produces: `SecureRecordDirectory.open()`, `.from_fd()`, `.read(name, max_bytes)`, `.publish(name, data, max_bytes)`, and `.content_ids()` for both manifest stores.

- [ ] **Step 1: Write attacks against enumeration and create-only publication**

Add byte-store tests that create a valid-looking SHA-256 filename as a symlink,
FIFO, directory, hard link, bad-permission file, and oversized file. Assert
`content_ids()` or `read()` rejects every case and never blocks on the FIFO.
Canonical JSON, schema dispatch, and filename/content-ID agreement remain the
responsibility of each manifest store's decoder and are exercised in Task 3;
the reusable byte store must not guess a record schema:

```python
def test_enumeration_rejects_a_fifo_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "records"
    root.mkdir(mode=0o700)
    fifo = root / f"{'a' * 64}.json"
    os.mkfifo(fifo, mode=0o600)

    completed = subprocess.run(
        [sys.executable, "-c", ENUMERATE_PROBE, str(root)],
        check=False,
        timeout=1,
    )

    assert completed.returncode == 73
```

Define `ENUMERATE_PROBE` in that test module as a short `python -c` program that
constructs `SecureRecordDirectory.open(Path(sys.argv[1]), create=False)`, calls
`content_ids()`, exits 0 on success, and exits 73 on any exception. Also retain
tests for atomic no-replace hard-link publication, fsync, byte-identical replay,
root-directory swap, and closed descriptors on error. A `.manifest-*` crash
temporary may be ignored only after it is verified as a single-link 0600
regular file within the same byte limit; an attacker-shaped FIFO or symlink
under that prefix fails closed.

- [ ] **Step 2: Run focused secure-store tests and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/manifests/test_secure_records.py -q`

Expected: FAIL because `SecureRecordDirectory` is not defined.

- [ ] **Step 3: Implement reusable secure record operations**

Implement the pinned descriptor boundary without `Path.glob()`:

```python
_CONTENT_FILE = re.compile(r"^([0-9a-f]{64})\.json$")


class SecureRecordDirectory:
    def __init__(
        self,
        path: Path,
        fd: int,
        *,
        close_fd: bool,
    ) -> None:
        self.path = path
        self.fd = fd
        self._close_fd = close_fd

    @classmethod
    def open(cls, path: Path, *, create: bool) -> SecureRecordDirectory:
        fd = open_directory_path(path, create=create)
        try:
            if create:
                os.fchmod(fd, 0o700)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise PermissionError(path)
            return cls(path, fd, close_fd=True)
        except BaseException:
            os.close(fd)
            raise

    @classmethod
    def from_fd(
        cls,
        path: Path,
        fd: int,
        *,
        close_fd: bool = False,
    ) -> SecureRecordDirectory:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise PermissionError(path)
        return cls(path, fd, close_fd=close_fd)

    def content_ids(
        self,
        *,
        allowed_non_records: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        revalidate_directory_path(self.path, self.fd)
        identifiers: list[str] = []
        for name in os.listdir(self.fd):
            match = _CONTENT_FILE.fullmatch(name)
            if match is not None:
                self.read(name, max_bytes=256 * 1024)
                identifiers.append(match.group(1))
            elif name.startswith(".manifest-"):
                self.read(name, max_bytes=256 * 1024)
            elif name not in allowed_non_records:
                raise FileExistsError(self.path / name)
        revalidate_directory_path(self.path, self.fd)
        return tuple(sorted(identifiers))

    def read(self, name: str, *, max_bytes: int) -> bytes:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self.fd,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.EISDIR}:
                raise FileExistsError(self.path / name) from error
            raise
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_size > max_bytes
            ):
                raise FileExistsError(self.path / name)
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                data = source.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise FileExistsError(self.path / name)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(named.st_mode)
                or named.st_dev != opened.st_dev
                or named.st_ino != opened.st_ino
            ):
                raise FileExistsError(self.path / name)
            return data
        finally:
            os.close(descriptor)

    def publish(self, name: str, data: bytes, *, max_bytes: int) -> bytes:
        if len(data) > max_bytes or _CONTENT_FILE.fullmatch(name) is None:
            raise ValueError("invalid secure record publication")
        temporary_name: str | None = None
        published = False
        try:
            descriptor, temporary_name = create_private_file(
                self.fd, prefix=".manifest-"
            )
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            revalidate_directory_path(self.path, self.fd)
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=self.fd,
                    dst_dir_fd=self.fd,
                    follow_symlinks=False,
                )
                published = True
            except FileExistsError:
                pass
            os.unlink(temporary_name, dir_fd=self.fd)
            temporary_name = None
            os.fsync(self.fd)
            revalidate_directory_path(self.path, self.fd)
            stored = self.read(name, max_bytes=max_bytes)
            if stored != data:
                raise FileExistsError(self.path / name)
            return stored
        except BaseException:
            if published:
                with suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=self.fd)
                    os.fsync(self.fd)
            raise
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=self.fd)
                    os.fsync(self.fd)
```

Add `__enter__()`/`__exit__()` and close the pinned descriptor exactly once
only when `_close_fd` is true. Revalidate the root before publication, after
linking, after unlinking the temporary name, and before returning the reread
bytes. The temporary name must be removed before `read()` enforces
`st_nlink == 1`; otherwise a successful first publication still has two names
and rejects itself. `from_fd(..., close_fd=False)`
is the only path used while a collection lease owns the descriptor. The
default `content_ids()` allows no unrelated entry. Corpus-store code may pass
`allowed_non_records=frozenset({".locks"})` only after opening `.locks` with
`O_DIRECTORY | O_NOFOLLOW` relative to the pinned root and verifying it is the
same 0700 directory used by the active lease.

- [ ] **Step 4: Refactor `ManifestStore` to use the shared primitive**

Keep `ManifestStore`'s public methods and decode dispatch unchanged. Its create/read path becomes:

```python
with SecureRecordDirectory.open(self._directory, create=True) as records:
    stored = records.publish(
        f"{manifest.manifest_id}.json",
        canonical_json(manifest, include_manifest_id=True),
        max_bytes=_MAX_MANIFEST_BYTES,
    )
return self._decode_canonical(stored, manifest.manifest_id)
```

Do not change canonical bytes, schema routing, exception classes, or permission requirements.

- [ ] **Step 5: Run secure-record and all source-manifest store tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/manifests/test_secure_records.py tests/unit/manifests/test_manifest_store.py tests/unit/manifests/test_source_manifest.py tests/unit/manifests/test_source_manifest_v2.py -q
```

Expected: PASS with source v1/v2 golden IDs unchanged.

- [ ] **Step 6: Run static checks and commit**

Run:

```bash
.venv/bin/python -m ruff check src/specpilot/manifests/_secure_records.py src/specpilot/manifests/store.py tests/unit/manifests/test_secure_records.py
.venv/bin/python -m mypy src/specpilot/manifests/_secure_records.py src/specpilot/manifests/store.py
git add src/specpilot/manifests/_secure_records.py src/specpilot/manifests/store.py tests/unit/manifests/test_secure_records.py
git commit -m "refactor: share secure manifest storage"
```

Expected: checks exit 0 and the commit contains no corpus behavior yet.

---

### Task 3: Build the corpus manifest store and persistent collection leases

**Files:**
- Create: `src/specpilot/manifests/corpus_store.py`
- Create: `tests/unit/manifests/test_corpus_manifest_store.py`
- Create: `tests/unit/manifests/test_corpus_collection_leases.py`

**Interfaces:**
- Consumes: `CorpusManifestIntent`, `CorpusManifestDraft`, `CorpusManifest`, and `SecureRecordDirectory`.
- Produces: `CorpusManifestStore.read()`, `.read_all()`, `.find_by_intent()`, `.require_publishable_intent()`, `.create()`, `.acquire_write_lease()`, `.acquire_freeze_lease()`, `CollectionWriteLease`, and `CollectionFreezeLease`.

- [ ] **Step 1: Write create/read/intent/predecessor tests**

Cover canonical round trip, 0700/0600 permissions, byte-identical replay, tampering, unknown schema, content-ID mismatch, source-order preservation, predecessor existence, predecessor collection equality, and same-intent conflicts:

```python
def test_create_requires_an_active_matching_freeze_lease(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    draft = corpus_draft()

    with store.acquire_freeze_lease(draft.collection_name) as lease:
        stored = store.create(draft, lease=lease)

    assert store.read(stored.manifest_id) == stored
    with pytest.raises(CollectionLeaseError):
        store.create(draft, lease=lease)


def test_same_intent_cannot_bind_two_snapshots(tmp_path: Path) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    first = corpus_draft()
    second = corpus_draft(
        snapshot=QdrantSnapshotBinding(
            name="other.snapshot", checksum="9" * 64, size_bytes=8192
        )
    )
    with store.acquire_freeze_lease(first.collection_name) as lease:
        store.create(first, lease=lease)
        with pytest.raises(CorpusManifestIntentConflictError):
            store.create(second, lease=lease)
```

- [ ] **Step 2: Write real `flock` exclusion and frozen-registry tests**

Use `blocking=False` for deterministic same-process assertions and `subprocess.Popen` for kernel-level cross-process behavior:

```python
def test_waiting_writer_rechecks_registry_after_freeze_publishes(
    tmp_path: Path,
) -> None:
    store = CorpusManifestStore(tmp_path / "corpus")
    collection = corpus_intent().collection_name
    with store.acquire_freeze_lease(collection) as freeze_lease:
        waiting = subprocess.Popen(
            [sys.executable, "-c", WRITER_PROBE, str(tmp_path / "corpus"), collection]
        )
        store.create(corpus_draft(), lease=freeze_lease)

    assert waiting.wait(timeout=2) == FROZEN_EXIT
```

Define `FROZEN_EXIT = 74` and `WRITER_PROBE` in the test module. The probe
constructs `CorpusManifestStore(Path(sys.argv[1]))`, blocks in
`acquire_write_lease(sys.argv[2])`, exits 0 only if the context is entered, and
exits `FROZEN_EXIT` on `CollectionFrozenError`. Also test: shared writers
coexist; an exclusive lease cannot enter while a writer lives; a writer object
is invalid after context exit; wrong-store, wrong-collection, wrong-mode, and
forged/closed leases fail; corrupt canonical, unsupported-version, and
filename/content-ID-mismatched records make writer acquisition fail closed
globally.

- [ ] **Step 3: Run the store and lease tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/manifests/test_corpus_manifest_store.py tests/unit/manifests/test_corpus_collection_leases.py -q
```

Expected: FAIL because `specpilot.manifests.corpus_store` does not exist.

- [ ] **Step 4: Implement lease types with owner and lifetime validation**

Use lock files that are never removed:

```python
class UnsupportedCorpusManifestVersionError(ValueError):
    pass


class CollectionLeaseError(RuntimeError):
    pass


class CollectionLeaseUnavailableError(CollectionLeaseError):
    pass


class CollectionFrozenError(CollectionLeaseError):
    pass


class CorpusManifestIntentConflictError(ValueError):
    pass


class CorpusPredecessorError(ValueError):
    pass


@dataclass(slots=True)
class _CollectionLease:
    collection_name: str
    _issue_token: object = field(repr=False)
    _owner_token: object = field(repr=False)
    _root_fd: int = field(repr=False)
    _lock_fd: int = field(repr=False)
    _exclusive: bool
    _closed: bool = False

    @property
    def root_fd(self) -> int:
        self.require_active_for(self.collection_name)
        return self._root_fd

    def require_active_for(self, collection_name: str) -> None:
        if self._issue_token is not _LEASE_ISSUER or self._closed:
            raise CollectionLeaseError("collection lease is closed")
        if self.collection_name != collection_name:
            raise CollectionLeaseError("collection lease names another collection")

    def require_owned(
        self,
        *,
        owner_token: object,
        collection_name: str,
        exclusive: bool | None = None,
    ) -> None:
        self.require_active_for(collection_name)
        if self._owner_token is not owner_token:
            raise CollectionLeaseError("collection lease is not active for this store")
        if exclusive is not None and self._exclusive is not exclusive:
            raise CollectionLeaseError("collection lease has the wrong mode")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
        finally:
            os.close(self._root_fd)
```

Create `.locks` as 0700 beneath the pinned root without following links, keep
its descriptor long enough to open and validate the lock file, then close only
that child-directory descriptor. Open `<sha256(collection)>.lock` with `O_RDWR
| O_CREAT | O_NOFOLLOW | O_NONBLOCK`, require regular/single-link/0600, and use
`LOCK_SH` for writers or `LOCK_EX` for freeze. Add `LOCK_NB` only when
`blocking=False`; translate `EWOULDBLOCK`/`EAGAIN` into
`CollectionLeaseUnavailableError` and close every acquired descriptor on every
failure path.
Make concrete lease constructors module-private (`init=False`) and issue them only through the store with `_LEASE_ISSUER`; `CollectionWriteLease.require_active_for()` also requires shared mode and `CollectionFreezeLease.require_active_for()` requires exclusive mode.

- [ ] **Step 5: Implement store scanning and create-only publication**

Use the manifest files themselves as authority:

```python
class CorpusManifestStore:
    @staticmethod
    def _validate_predecessor_reference(
        intent: CorpusManifestIntent,
        manifests: tuple[CorpusManifest, ...],
    ) -> None:
        predecessor_id = intent.predecessor_manifest_id
        if predecessor_id is None:
            return
        by_id = {item.manifest_id: item for item in manifests}
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            raise CorpusPredecessorError("corpus predecessor does not exist")
        if predecessor.collection_name != intent.collection_name:
            raise CorpusPredecessorError(
                "corpus predecessor names another collection"
            )

    @classmethod
    def _validate_predecessor_graph(
        cls,
        manifests: tuple[CorpusManifest, ...],
    ) -> None:
        for manifest in manifests:
            cls._validate_predecessor_reference(manifest.intent, manifests)

    def read_all(self) -> tuple[CorpusManifest, ...]:
        with SecureRecordDirectory.open(self._directory, create=False) as records:
            return self._decode_all(records)

    def _read_all_under(
        self,
        lease: CollectionWriteLease | CollectionFreezeLease,
    ) -> tuple[CorpusManifest, ...]:
        lease.require_owned(
            owner_token=self._owner_token,
            collection_name=lease.collection_name,
        )
        with SecureRecordDirectory.from_fd(
            self._directory,
            lease.root_fd,
            close_fd=False,
        ) as records:
            return self._decode_all(records)

    def acquire_write_lease(
        self, collection_name: str, *, blocking: bool = True
    ) -> CollectionWriteLease:
        lease = self._acquire(collection_name, exclusive=False, blocking=blocking)
        try:
            if any(
                item.collection_name == collection_name
                for item in self._read_all_under(lease)
            ):
                raise CollectionFrozenError(collection_name)
            return lease
        except BaseException:
            lease.close()
            raise

    def require_publishable_intent(
        self,
        intent: CorpusManifestIntent,
        *,
        lease: CollectionFreezeLease,
    ) -> None:
        lease.require_owned(
            owner_token=self._owner_token,
            collection_name=intent.collection_name,
            exclusive=True,
        )
        manifests = self._read_all_under(lease)
        self._validate_predecessor_reference(intent, manifests)
        predecessor_id = intent.predecessor_manifest_id
        bound = tuple(
            item for item in manifests
            if item.collection_name == intent.collection_name
        )
        if bound and predecessor_id not in {item.manifest_id for item in bound}:
            raise CorpusPredecessorError(
                "a frozen collection requires an explicit predecessor"
            )

    def create(
        self,
        draft: CorpusManifestDraft,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest:
        lease.require_owned(
            owner_token=self._owner_token,
            collection_name=draft.collection_name,
            exclusive=True,
        )
        existing = self.find_by_intent(draft.intent, lease=lease)
        manifest = CorpusManifest.from_draft(draft)
        if existing is not None:
            if existing != manifest:
                raise CorpusManifestIntentConflictError("intent already has a manifest")
            return existing
        self.require_publishable_intent(draft.intent, lease=lease)
        return self._publish_under(manifest, lease=lease)
```

`root_fd` is a read-only property on the lease; callers cannot replace it, and
only `CorpusManifestStore` accepts it because owner-token validation happens
first. `_decode_all()` first securely validates the `.locks` directory, passes
it as the sole allowed non-record entry, decodes and canonical-validates every
content file, and then validates the entire predecessor graph: every non-null
predecessor must exist in that decoded set and bind the same collection.
`read(manifest_id)` selects from `read_all()` rather than bypassing that graph
validation. `_validate_predecessor_reference(intent, manifests)` performs the
same existence/same-collection check for the proposed intent.

`find_by_intent()` scans with `_read_all_under()`, calls
`_validate_predecessor_reference()` before returning an exact replay, requires
the same active exclusive lease, and refuses multiple matches.
`require_publishable_intent()` calls the predecessor validator and separately
enforces the new-publication rule that an already-bound collection must name
one of its existing manifests. `_publish_under()` uses
`SecureRecordDirectory.from_fd(..., close_fd=False)` so registry validation,
predecessor validation, hard-link publication, reread, and directory fsync all
refer to the lease-pinned root. `require_publishable_intent()` is deliberately
called both by freeze before snapshot creation and by `create()` immediately
before publication. Thus an exact successor replay cannot succeed after its
predecessor file disappears.

- [ ] **Step 6: Run all corpus-store security and concurrency tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/manifests/test_secure_records.py tests/unit/manifests/test_corpus_manifest_store.py tests/unit/manifests/test_corpus_collection_leases.py -q
```

Expected: PASS, including the cross-process waiting-writer case.

- [ ] **Step 7: Run source-store regression and static checks**

Run:

```bash
.venv/bin/python -m pytest tests/unit/manifests -q
.venv/bin/python -m ruff check src/specpilot/manifests tests/unit/manifests
.venv/bin/python -m mypy src/specpilot/manifests
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit the corpus store and leases**

```bash
git add src/specpilot/manifests/corpus_store.py tests/unit/manifests/test_corpus_manifest_store.py tests/unit/manifests/test_corpus_collection_leases.py
git commit -m "feat: revoke writes to frozen collections"
```

---

### Task 4: Align retrieval identity and stable RRF ordering

**Files:**
- Create: `src/specpilot/retrieval/protocol.py`
- Create: `tests/unit/corpus/test_indexable.py`
- Modify: `src/specpilot/corpus/walk.py`
- Modify: `src/specpilot/corpus/indexable.py`
- Modify: `src/specpilot/retrieval/local.py`
- Modify: `src/specpilot/retrieval/hybrid.py`
- Modify: `tests/unit/retrieval/test_hybrid.py`
- Verify: `tests/unit/retrieval/test_bm25.py`
- Verify: `tests/unit/retrieval/test_pooling.py`

**Interfaces:**
- Consumes: current `IndexUnit`, `RouteRanking`, `RrfParameters`, and whole-unit chunking.
- Produces: `RFCXML_PARSER_VERSION`, `CHUNKER_VERSION`, `RetrievalLocator`, `numeric_clause_path()`, `locator_for_unit()`, and locator-required `reciprocal_rank_fusion()`.

- [ ] **Step 1: Write local-unit version, ordinal, and path tests**

Define this concrete helper in `test_indexable.py`, then assert
parser/chunker constants, clause/table ordinal propagation, body numeric
ordering, appendix ordering, and unchanged point payload:

```python
def _index_unit(**changes: object) -> IndexUnit:
    values: dict[str, object] = {
        "unit_id": "ietf-rfc-9110:section-2-1",
        "kind": "clause",
        "document_id": "ietf-rfc-9110",
        "document_version": "2022-06",
        "section_number": "2",
        "section_path": "Syntax",
        "ordinal": 1,
        "text": "source",
        "indexed": "2 Syntax\nsource",
    }
    values.update(changes)
    return IndexUnit(**values)


def test_numeric_paths_sort_sections_numerically() -> None:
    section_two = replace(_index_unit(), section_number="2", ordinal=1)
    section_ten = replace(_index_unit(), section_number="10", ordinal=1)
    assert numeric_clause_path(section_two) < numeric_clause_path(section_ten)


def test_clause_and_table_at_one_ordinal_have_distinct_paths() -> None:
    clause = replace(_index_unit(), kind="clause", ordinal=1)
    table = replace(_index_unit(), kind="table", ordinal=1)
    assert numeric_clause_path(clause) != numeric_clause_path(table)
    assert set(point_payload(clause)) == {
        "unit_id", "kind", "document_id", "document_version",
        "section_number", "section_path",
    }
```

Use `(0, *numeric_section, -1, ordinal, kind_rank)` for body sections and `(1, appendix_base26, *numeric_subsections, -1, ordinal, kind_rank)` for appendices; `kind_rank` is clause 0 and table 1.

- [ ] **Step 2: Replace RRF tie tests with exact §8.5.1 tests**

Keep `RouteRanking` unchanged for pooling. Define `_locator()` in
`test_hybrid.py`, then assert full-key deduplication, route-order invariance,
numeric ties, missing locators, cross-manifest inputs, conflicting locators,
tie-key collisions, and a nonempty set of empty routes producing an empty
fused ranking without requiring locators. Parameterize malformed locator
values (bad manifest digest, blank document/clause, empty or non-integer path,
negative child start, invalid span, and span/start disagreement) and assert
construction itself fails. A direct `locator_for_unit("a" * 64, _index_unit())`
test must assert `clause_id == unit_id`, `child_span is None`,
`child_start == 0`, and the expected numeric path:

```python
def _locator(
    *,
    clause_id: str,
    numeric_clause_path: tuple[int, ...],
) -> RetrievalLocator:
    return RetrievalLocator(
        corpus_manifest_id="a" * 64,
        document_id="ietf-rfc-9110",
        clause_id=clause_id,
        child_span=None,
        numeric_clause_path=numeric_clause_path,
        child_start=0,
    )


def test_rrf_breaks_ties_by_numeric_clause_path_not_unit_id() -> None:
    rankings = (
        RouteRanking("bm25", ("z", "a")),
        RouteRanking("dense", ("a", "z")),
    )
    locators = {
        "z": _locator(clause_id="z", numeric_clause_path=(0, 2, -1, 1, 0)),
        "a": _locator(clause_id="a", numeric_clause_path=(0, 10, -1, 1, 0)),
    }
    fused = reciprocal_rank_fusion(rankings, locators=locators)
    assert [hit.unit_id for hit in fused.hits] == ["z", "a"]
```

- [ ] **Step 3: Run protocol tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/unit/corpus/test_indexable.py tests/unit/retrieval/test_hybrid.py -q
```

Expected: FAIL because `ordinal`, `RetrievalLocator`, and locator-required fusion do not exist.

- [ ] **Step 4: Add version authorities and local ordinal without changing indexed content**

Add:

```python
# corpus/walk.py
RFCXML_PARSER_VERSION: Final = "rfcxml-v3/v1"

# corpus/indexable.py
CHUNKER_VERSION: Final = "rfc-clause-table/v1"

@dataclass(frozen=True, slots=True)
class IndexUnit:
    unit_id: str
    kind: str
    document_id: str
    document_version: str
    section_number: str | None
    section_path: str
    ordinal: int
    text: str
    indexed: str
```

Populate `ordinal` from `Clause.ordinal` and `Table.ordinal`. Keep `point_payload()` byte-for-byte equivalent. Change `LocalCorpus.load()` to raise on duplicate unit IDs before insertion and add `units() -> tuple[IndexUnit, ...]` that preserves canonical document/unit order.

Implement the numeric path in `retrieval/protocol.py`:

```python
def _appendix_number(label: str) -> int:
    value = 0
    for character in label.upper():
        if not "A" <= character <= "Z":
            raise ValueError("appendix label is not alphabetic")
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def numeric_clause_path(unit: IndexUnit) -> tuple[int, ...]:
    if unit.section_number is None:
        raise ValueError("retrieval unit has no numbered section")
    parts = unit.section_number.split(".")
    if parts[0].isdigit() and all(part.isdigit() for part in parts):
        section = (0, *(int(part) for part in parts))
    elif parts[0].isalpha() and all(part.isdigit() for part in parts[1:]):
        section = (1, _appendix_number(parts[0]), *(int(part) for part in parts[1:]))
    else:
        raise ValueError("retrieval unit has a nonnumeric clause path")
    kind_rank = {"clause": 0, "table": 1}.get(unit.kind)
    if kind_rank is None:
        raise ValueError("retrieval unit has an unsupported kind")
    return (*section, -1, unit.ordinal, kind_rank)
```

- [ ] **Step 5: Implement exact retrieval locators and fusion**

Use immutable locator properties:

```python
@dataclass(frozen=True, slots=True)
class RetrievalLocator:
    corpus_manifest_id: str
    document_id: str
    clause_id: str
    child_span: tuple[int, int] | None
    numeric_clause_path: tuple[int, ...]
    child_start: int

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.corpus_manifest_id) is None:
            raise ValueError("retrieval locator has an invalid corpus manifest ID")
        if not self.document_id or self.document_id != self.document_id.strip():
            raise ValueError("retrieval locator has an invalid document ID")
        if not self.clause_id or self.clause_id != self.clause_id.strip():
            raise ValueError("retrieval locator has an invalid clause ID")
        if (
            not isinstance(self.numeric_clause_path, tuple)
            or not self.numeric_clause_path
            or any(type(part) is not int for part in self.numeric_clause_path)
        ):
            raise ValueError("retrieval locator has no numeric clause path")
        if type(self.child_start) is not int or self.child_start < 0:
            raise ValueError("retrieval locator has an invalid child start")
        if self.child_span is None:
            if self.child_start != 0:
                raise ValueError("whole-unit locator must start at zero")
            return
        if (
            not isinstance(self.child_span, tuple)
            or len(self.child_span) != 2
            or any(type(part) is not int for part in self.child_span)
        ):
            raise ValueError("retrieval locator has an invalid child span")
        start, end = self.child_span
        if start < 0 or start >= end or self.child_start != start:
            raise ValueError("retrieval locator child span and start disagree")

    @property
    def dedupe_key(self) -> tuple[object, ...]:
        return (
            self.corpus_manifest_id,
            self.document_id,
            self.clause_id,
            self.child_span,
        )

    @property
    def stable_tie_key(self) -> tuple[object, ...]:
        return self.document_id, self.numeric_clause_path, self.child_start


def locator_for_unit(
    corpus_manifest_id: str,
    unit: IndexUnit,
) -> RetrievalLocator:
    return RetrievalLocator(
        corpus_manifest_id=corpus_manifest_id,
        document_id=unit.document_id,
        clause_id=unit.unit_id,
        child_span=None,
        numeric_clause_path=numeric_clause_path(unit),
        child_start=0,
    )


def reciprocal_rank_fusion(
    rankings: Sequence[RouteRanking],
    *,
    locators: Mapping[str, RetrievalLocator],
    parameters: RrfParameters | None = None,
) -> FusedRanking:
    if not rankings:
        raise ValueError("fusion needs at least one ranking")
    settings = parameters or RrfParameters()
    if len({ranking.route for ranking in rankings}) != len(rankings):
        raise ValueError("fusion routes must be unique")
    wanted = {unit_id for ranking in rankings for unit_id in ranking.unit_ids}
    if wanted - set(locators):
        raise ValueError("fusion candidate has no retrieval locator")
    if wanted and len(
        {locators[unit_id].corpus_manifest_id for unit_id in wanted}
    ) != 1:
        raise ValueError("fusion candidates cross corpus manifests")

    grouped: dict[tuple[object, ...], dict[str, tuple[int, str]]] = {}
    identity_locator: dict[tuple[object, ...], RetrievalLocator] = {}
    tie_owner: dict[tuple[object, ...], tuple[object, ...]] = {}
    for ranking in rankings:
        for rank, unit_id in enumerate(ranking.unit_ids, start=1):
            locator = locators[unit_id]
            identity = locator.dedupe_key
            previous = identity_locator.setdefault(identity, locator)
            if previous != locator:
                raise ValueError("one identity has conflicting locators")
            owner = tie_owner.setdefault(locator.stable_tie_key, identity)
            if owner != identity:
                raise ValueError("two identities share one stable tie key")
            grouped.setdefault(identity, {}).setdefault(
                ranking.route, (rank, unit_id)
            )

    hits: list[FusedHit] = []
    for identity, by_route in grouped.items():
        score = math.fsum(
            1.0 / (settings.k + by_route[route][0])
            for route in sorted(by_route)
        )
        hits.append(
            FusedHit(
                unit_id=min(value[1] for value in by_route.values()),
                score=score,
                ranks={route: value[0] for route, value in sorted(by_route.items())},
                locator=identity_locator[identity],
            )
        )
    hits.sort(key=lambda hit: (-hit.score, *hit.locator.stable_tie_key))
    return FusedRanking(hits=tuple(hits), parameters=settings)
```

`FusedHit` carries its chosen `RetrievalLocator`. Do not add a locator field to `RouteRanking` and do not change pooling.

- [ ] **Step 6: Run retrieval, corpus, and pooling regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/corpus/test_indexable.py tests/unit/retrieval/test_hybrid.py tests/unit/retrieval/test_bm25.py tests/unit/retrieval/test_pooling.py tests/cli/test_annotation_pooling.py -q
```

Expected: PASS; pooling still consumes independent `RouteRanking` values and never fusion.

- [ ] **Step 7: Run static checks and commit**

Run:

```bash
.venv/bin/python -m ruff check src/specpilot/corpus/walk.py src/specpilot/corpus/indexable.py src/specpilot/retrieval/local.py src/specpilot/retrieval/protocol.py src/specpilot/retrieval/hybrid.py tests/unit/corpus/test_indexable.py tests/unit/retrieval/test_hybrid.py
.venv/bin/python -m mypy src/specpilot/corpus src/specpilot/retrieval
git add src/specpilot/corpus/walk.py src/specpilot/corpus/indexable.py src/specpilot/retrieval/local.py src/specpilot/retrieval/protocol.py src/specpilot/retrieval/hybrid.py tests/unit/corpus/test_indexable.py tests/unit/retrieval/test_hybrid.py
git commit -m "fix: align the frozen retrieval protocol"
```

Expected: checks exit 0 and the commit does not change corpus text, unit IDs, or Qdrant payloads.

---

### Task 5: Split dense read/write/admin capabilities and inspect Qdrant exactly

**Files:**
- Modify: `src/specpilot/retrieval/dense.py`
- Modify: `src/specpilot/cli.py` (`_annotation_pool_register` only)
- Modify: `tests/unit/retrieval/test_dense.py`
- Modify: `tests/integration/qdrant/test_collection.py`

**Interfaces:**
- Consumes: `QdrantCollectionSchema`, `CollectionWriteLease`, and `CollectionFreezeLease`.
- Produces: `BASELINE_DENSE_QUERY`, `DenseBackendUnavailable`, read-only `DenseIndex`, lease-bound `DenseIndexWriter.create()`/`.open()`, lease-bound `DenseSnapshotAdmin`, `DenseRecord`, `DenseSnapshot`, `point_id_for_unit()`, schema normalization, full record iteration, and snapshot listing.

- [ ] **Step 1: Write unit tests for capability separation and schema normalization**

Define `_collection_info()` with `types.SimpleNamespace` for the outer response,
a real `VectorParams(size=1024, distance=Distance.COSINE)`, `params.vectors`
set to that value, `params.sparse_vectors=None`, an HNSW namespace containing
`m=16`, `ef_construct=100`, `full_scan_threshold=10000`,
`max_indexing_threads=0`, `on_disk=None`, `payload_m=None`, and an empty
`payload_schema`. Assert the reader has no `create`, `upsert`, `drop`, or
`freeze`; writer creation needs a live matching write lease; an existing
collection is never deleted; closed/foreign leases reject every mutation;
default Qdrant `None` values normalize to float32/no-on-disk/no-sparse:

```python
def test_read_only_index_has_no_mutation_surface() -> None:
    for name in ("create", "upsert", "drop", "freeze"):
        assert not hasattr(DenseIndex, name)


def test_default_vector_schema_is_normalized() -> None:
    schema = normalize_collection_schema(_collection_info())
    assert schema.dense_vector.datatype == "float32"
    assert schema.dense_vector.on_disk is False
    assert schema.sparse_vectors == ()
```

Add record validation for absent/named/multivector, wrong width, NaN, infinity, duplicate point IDs, and duplicate payload unit IDs.

- [ ] **Step 2: Add Qdrant integration tests for records, snapshots, and no-delete create**

Use the existing `qdrant_url` fixture and a test-only raw `QdrantClient` for
cleanup and mutation. Construct `index` with `DenseIndex.open(qdrant_url,
collection_name)` and obtain `freeze_lease` from a temporary
`CorpusManifestStore`; assert:

```python
def test_real_snapshot_has_a_checksum(
    qdrant_url: str,
    index: DenseIndex,
    freeze_lease: CollectionFreezeLease,
) -> None:
    with DenseSnapshotAdmin.open(qdrant_url, index.name, freeze_lease) as admin:
        created = admin.create_snapshot()
    assert created in index.snapshots()
    assert len(created.checksum) == 64
    assert created.size_bytes > 0
```

Also assert `iter_records()` calls scroll with payloads and vectors, returns the deterministic point IDs, and a second `DenseIndexWriter.create()` raises without deleting the first collection.
The integration fixture must release its shared write lease before yielding a
read-only reader or acquiring a freeze lease:

```python
with store.acquire_write_lease(collection_name) as write_lease:
    with DenseIndexWriter.create(qdrant_url, collection_name, write_lease) as writer:
        writer.upsert(points)

with DenseIndex.open(qdrant_url, collection_name) as reader:
    yield reader
```

This prevents a same-process snapshot test from waiting forever for an EX
lease while its fixture still holds SH. Cleanup uses a separate raw
`QdrantClient(url=qdrant_url, trust_env=False)` after the reader closes.

- [ ] **Step 3: Run dense unit tests and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/retrieval/test_dense.py -q`

Expected: FAIL because the read/write/admin split is absent.

- [ ] **Step 4: Implement exact dense data types and read-only methods**

Use these signatures:

```python
BASELINE_DENSE_QUERY = DenseQueryParameters(
    hnsw_ef=None,
    exact=False,
    indexed_only=False,
)


class DenseBackendUnavailable(RuntimeError):
    pass


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


@dataclass(slots=True)
class DenseIndex:
    name: str
    _client: Any = field(repr=False)

    @classmethod
    def open(cls, url: str, name: str) -> DenseIndex:
        return cls(name, QdrantClient(url=url, trust_env=False))

    def collection_schema(self) -> QdrantCollectionSchema:
        return normalize_collection_schema(self._client.get_collection(self.name))

    def point_count(self) -> int:
        return int(self._client.count(self.name, exact=True).count)

    def iter_records(self, *, batch_size: int = 256) -> Iterator[DenseRecord]:
        if batch_size <= 0:
            raise ValueError("record batch size must be positive")
        point_ids: set[int | str] = set()
        unit_ids: set[str] = set()
        offset: Any = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for point in points:
                payload = dict(point.payload or {})
                unit_id = payload.get("unit_id")
                vector = point.vector
                if not isinstance(unit_id, str) or not unit_id:
                    raise ValueError("dense point has no unit_id payload")
                if point.id in point_ids or unit_id in unit_ids:
                    raise ValueError("dense collection has a duplicate identity")
                if not isinstance(vector, list) or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    for value in vector
                ):
                    raise ValueError("dense point does not hold one unnamed vector")
                numeric = tuple(float(value) for value in vector)
                if len(numeric) != VECTOR_SIZE or any(
                    not math.isfinite(value) for value in numeric
                ):
                    raise ValueError("dense point vector is invalid")
                point_ids.add(point.id)
                unit_ids.add(unit_id)
                yield DenseRecord(point.id, payload, numeric)
            if offset is None:
                return

    def snapshots(self) -> tuple[DenseSnapshot, ...]:
        values = self._client.list_snapshots(self.name)
        result: list[DenseSnapshot] = []
        for item in sorted(values, key=lambda value: value.name):
            checksum = item.checksum
            if not item.name or not checksum or item.size <= 0:
                raise ValueError("Qdrant returned incomplete snapshot metadata")
            result.append(
                DenseSnapshot(
                    name=item.name,
                    checksum=checksum,
                    size_bytes=item.size,
                )
            )
        return tuple(result)

    def search(self, vector: Sequence[float], k: int) -> list[DenseHit]:
        _validate_query(vector, k)
        points = self._client.query_points(
            collection_name=self.name,
            query=list(vector),
            limit=k,
            with_payload=True,
            search_params=SearchParams(
                hnsw_ef=BASELINE_DENSE_QUERY.hnsw_ef,
                exact=BASELINE_DENSE_QUERY.exact,
                indexed_only=BASELINE_DENSE_QUERY.indexed_only,
            ),
        ).points
        return [
            DenseHit(
                unit_id=str((point.payload or {}).get("unit_id", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in points
        ]

    def close(self) -> None:
        self._client.close()
```

Wrap every Qdrant transport/API call in a small `_qdrant_call()` that catches
`qdrant_client.http.exceptions.ApiException` and `OSError`, then raises
`DenseBackendUnavailable` without copying the backend message. Contract,
schema, payload, vector, and inventory `ValueError`s remain unwrapped so the
service can classify mismatches precisely.

`normalize_collection_schema()` accepts only one unnamed `VectorParams` at
`info.config.params.vectors`. It rejects `multivector_config`, nonempty
`sparse_vectors`, and datatypes other than `None`/`Datatype.FLOAT32` for
corpus-manifest/v1. Normalize enum values through `.value` (distance to
lowercase `"cosine"`, datatype to `"float32"`), vector `on_disk=None` to
false, and HNSW `on_disk=None` to false. Start from
`info.config.hnsw_config`, overlay every non-`None` field in vector-level
`HnswConfigDiff`, and record the resulting effective HNSW values. Hash
normalized vector-level and collection-level quantization configs separately.
Sort payload-index descriptors and exclude `PayloadIndexInfo.points`; the six
locator-field flags are derived from those normalized descriptors.
`DenseIndex` is also a context manager whose `__exit__()` calls `close()`.

- [ ] **Step 5: Implement lease-bound writer and snapshot admin**

```python
@dataclass(slots=True)
class DenseIndexWriter:
    name: str
    _client: Any = field(repr=False)
    _lease: CollectionWriteLease = field(repr=False)

    @classmethod
    def create(cls, url: str, name: str, lease: CollectionWriteLease) -> DenseIndexWriter:
        lease.require_active_for(name)
        client = QdrantClient(url=url, trust_env=False)
        try:
            if client.collection_exists(name):
                raise FileExistsError(name)
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=VECTOR_SIZE,
                    distance=Distance.COSINE,
                ),
            )
            return cls(name, client, lease)
        except BaseException:
            client.close()
            raise

    @classmethod
    def open(
        cls,
        url: str,
        name: str,
        lease: CollectionWriteLease,
    ) -> DenseIndexWriter:
        lease.require_active_for(name)
        client = QdrantClient(url=url, trust_env=False)
        try:
            if not client.collection_exists(name):
                raise FileNotFoundError(name)
            return cls(name, client, lease)
        except BaseException:
            client.close()
            raise

    def upsert(self, points: Sequence[DensePoint]) -> None:
        self._lease.require_active_for(self.name)
        if not points:
            return
        self._client.upsert(
            collection_name=self.name,
            points=[
                PointStruct(
                    id=point_id_for_unit(point.unit_id),
                    vector=list(point.vector),
                    payload=point.payload,
                )
                for point in points
            ],
            wait=True,
        )

    def drop(self) -> None:
        self._lease.require_active_for(self.name)
        self._client.delete_collection(self.name)

    def close(self) -> None:
        self._client.close()


@dataclass(slots=True)
class DenseSnapshotAdmin:
    name: str
    _client: Any = field(repr=False)
    _lease: CollectionFreezeLease = field(repr=False)

    @classmethod
    def open(
        cls,
        url: str,
        name: str,
        lease: CollectionFreezeLease,
    ) -> DenseSnapshotAdmin:
        lease.require_active_for(name)
        return cls(name, QdrantClient(url=url, trust_env=False), lease)

    def create_snapshot(self) -> DenseSnapshot:
        self._lease.require_active_for(self.name)
        value = self._client.create_snapshot(self.name, wait=True)
        checksum = None if value is None else value.checksum
        if value is None or not value.name or not checksum or value.size <= 0:
            raise ValueError("Qdrant returned incomplete snapshot metadata")
        return DenseSnapshot(
            name=value.name,
            checksum=checksum,
            size_bytes=value.size,
        )

    def close(self) -> None:
        self._client.close()
```

Reader, writer, and admin are context managers. Each mutating/admin operation
rechecks the lease, so an escaped capability stops working after lease close.
Promote `_point_id()` to `point_id_for_unit()` without changing its output.

- [ ] **Step 6: Update pooling to use the read-only API**

Open the completed offline pooling reader without a mutable flag:

```python
dense = DenseIndex.open(arguments.qdrant_url, arguments.collection)
```

Remove the `frozen=True` flag. Preserve `vector_size()` and `unit_ids()` as
read-only compatibility methods implemented from the normalized schema and
payload-only scrolling, and close the reader in a `finally` block after the
pooling run is emitted or refused. Keep the exact vector-size, point-count, and
unit-ID checks on this already-completed offline audit path; it receives no
writer capability and is not the online startup loader.

- [ ] **Step 7: Run unit and live Qdrant tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/retrieval/test_dense.py tests/cli/test_annotation_pooling.py -q
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6333 .venv/bin/python -m pytest tests/integration/qdrant/test_collection.py -q
```

Expected: PASS with a running Qdrant 1.12.4; the integration command must not skip.

- [ ] **Step 8: Run static checks and commit**

Run:

```bash
.venv/bin/python -m ruff check src/specpilot/retrieval/dense.py src/specpilot/cli.py tests/unit/retrieval/test_dense.py tests/integration/qdrant/test_collection.py
.venv/bin/python -m mypy src/specpilot/retrieval/dense.py
git add src/specpilot/retrieval/dense.py src/specpilot/cli.py tests/unit/retrieval/test_dense.py tests/integration/qdrant/test_collection.py
git commit -m "feat: separate dense collection capabilities"
```

---

### Task 6: Derive deterministic corpus, QA, and dense inventory attestations

**Files:**
- Create: `src/specpilot/corpus/dense_inventory.py`
- Create: `tests/unit/corpus/test_dense_inventory.py`
- Modify: `src/specpilot/corpus/qa.py`
- Modify: `src/specpilot/retrieval/local.py`
- Verify: `src/specpilot/retrieval/dense.py`

**Interfaces:**
- Consumes: `IndexUnit`, `DenseRecord`, `QaReport`, `point_id_for_unit()`, and `point_payload()`.
- Produces: `derived_corpus_sha256()`, `vector_sha256()`, `qa_evidence_sha256()`, `DenseInventoryEvidence`, and `build_dense_inventory()`.

- [ ] **Step 1: Write golden hash, refusal, and sensitivity tests**

Define `_unit()` with one concrete `IndexUnit(unit_id="u1", kind="clause",
document_id="ietf-rfc-1", document_version="1", section_number="1",
section_path="One", ordinal=1, text="source", indexed="1 One\nsource")` and
`_record(unit)` with `point_id_for_unit(unit.unit_id)`,
`point_payload(unit)`, and `(0.0,) * 1023 + (1.0,)`. Use those small concrete
values and fixed expected digests. Assert input-order independence for
derived/inventory hashes. Live-only point-ID or payload drift is a refusal;
coordinated local+dense identity/locator changes and source/indexed/vector
content changes produce a different root:

```python
def test_vector_hash_uses_little_endian_float32() -> None:
    vector = (0.0,) * 1023 + (1.0,)
    expected = hashlib.sha256(struct.pack("<1024f", *vector)).hexdigest()
    assert vector_sha256(vector) == expected


def test_live_point_or_payload_drift_is_refused() -> None:
    unit = _unit()
    record = _record(unit)
    with pytest.raises(ValueError):
        build_dense_inventory((unit,), (replace(record, point_id="wrong"),))
    with pytest.raises(ValueError):
        build_dense_inventory(
            (unit,),
            (replace(record, payload={**record.payload, "section_path": "Other"}),),
        )


@pytest.mark.parametrize("mutation", ["identity", "locator", "source", "indexed", "vector"])
def test_inventory_changes_for_every_bound_fact(mutation: str) -> None:
    unit = _unit()
    record = _record(unit)
    original = build_dense_inventory((unit,), (record,))
    if mutation == "identity":
        changed_unit = replace(unit, unit_id="u2")
        changed_record = _record(changed_unit)
    elif mutation == "locator":
        changed_unit = replace(unit, section_path="Other")
        changed_record = _record(changed_unit)
    elif mutation == "source":
        changed_unit = replace(unit, text="changed source")
        changed_record = record
    elif mutation == "indexed":
        changed_unit = replace(unit, indexed="changed indexed")
        changed_record = record
    else:
        changed_unit = unit
        changed = list(record.vector)
        changed[0] = 0.5
        changed_record = replace(record, vector=tuple(changed))
    observed = build_dense_inventory((changed_unit,), (changed_record,))
    assert observed.inventory_root_sha256 != original.inventory_root_sha256
```

Reject duplicate local IDs, duplicate point IDs, duplicate payload unit IDs, missing/extra points, wrong deterministic point ID, nonexact payload, missing/named/multivector values, wrong vector width, and nonfinite/float32-overflowing values.

- [ ] **Step 2: Write QA evidence and real compatibility tests**

The evidence preimage includes a domain/version, source manifest ID, document ID, report verdict, and each line's name/counts/`float.hex()` values/verdict:

```python
def test_qa_evidence_binds_counts_thresholds_and_source() -> None:
    original = qa_evidence_sha256("a" * 64, qa_report())
    assert qa_evidence_sha256("b" * 64, qa_report()) != original
    assert qa_evidence_sha256("a" * 64, qa_report(numerator_delta=1)) != original
    assert qa_evidence_sha256("a" * 64, qa_report(threshold_delta=0.01)) != original
```

Add a restricted-corpus acceptance test guarded by local file existence that reconstructs RFC 9110 then RFC 9112 and asserts exactly:

```python
clause_limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
corpus = LocalCorpus.load(
    (
        (verified_rfc_9110, clause_limits),
        (verified_rfc_9112, clause_limits),
    ),
    RfcLimits(),
)
assert corpus.unit_count() == 1922
assert Bm25Index.build(corpus.indexable()).fingerprint == (
    "8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3"
)
assert derived_corpus_sha256(corpus.units()) == (
    "46616bd050308f6f77782afe8706b8e2d8f577de9b9b698e228e1c52b40596eb"
)
assert collection_name(
    derived_corpus_sha256(corpus.units()), "clause/v1", "index-text/v1"
) == "specpilot_ff4841e2d846388014efa06870fbbdb7"
```

- [ ] **Step 3: Run inventory tests and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/corpus/test_dense_inventory.py -q`

Expected: FAIL because `specpilot.corpus.dense_inventory` does not exist.

- [ ] **Step 4: Implement deterministic hashes and exact inventory matching**

Use these exact algorithms:

```python
def derived_corpus_sha256(units: Iterable[IndexUnit]) -> str:
    by_id = _unique_units(units)
    lines = [
        f"{unit_id}\x1f{hashlib.sha256(by_id[unit_id].indexed.encode('utf-8')).hexdigest()}"
        for unit_id in sorted(by_id)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def vector_sha256(vector: Sequence[float]) -> str:
    if len(vector) != VECTOR_SIZE or any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError("dense vector is not finite and 1024-dimensional")
    try:
        encoded = struct.pack(f"<{VECTOR_SIZE}f", *vector)
    except (OverflowError, struct.error) as error:
        raise ValueError("dense vector cannot be represented as float32") from error
    return hashlib.sha256(encoded).hexdigest()
```

`build_dense_inventory()` maps local units and records by unique payload `unit_id`, requires exact set equality, verifies `record.point_id == point_id_for_unit(unit_id)` and `record.payload == point_payload(unit)`, then hashes canonical JSON entries ordered by unit ID:

```python
@dataclass(frozen=True, slots=True)
class DenseInventoryEvidence:
    point_count: int
    inventory_root_sha256: str


def canonical_mapping_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_mapping_sha256(value: object) -> str:
    return hashlib.sha256(canonical_mapping_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


entry = {
    "point_id": str(record.point_id),
    "unit_id": unit_id,
    "locator_payload_sha256": canonical_mapping_sha256(record.payload),
    "source_text_sha256": sha256_text(unit.text),
    "indexed_text_sha256": sha256_text(unit.indexed),
    "dense_vector_sha256": vector_sha256(record.vector),
}
```

Return `DenseInventoryEvidence(point_count=len(entries), inventory_root_sha256=canonical_mapping_sha256(entries))`. No individual entry is persisted.

- [ ] **Step 5: Implement deterministic QA evidence**

Add `PARSE_QA_EVIDENCE_VERSION = "parse-qa-evidence/v1"` and:

```python
def qa_evidence_sha256(source_manifest_id: str, report: QaReport) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", source_manifest_id) is None:
        raise ValueError("parse QA source manifest ID is invalid")
    expected_names = (
        "section_numbering",
        "cross_references",
        "table_fidelity",
        "coverage",
        "orphan_normatives",
        "excerpt_fit",
    )
    if not report.passed or tuple(line.name for line in report.lines) != expected_names:
        raise ValueError("parse QA is incomplete or failed")
    if any(not line.passed for line in report.lines):
        raise ValueError("parse QA contains an unmeasured or failed line")
    if report.lines[-1].denominator == 0:
        raise ValueError("parse QA excerpt_fit line is unmeasured")
    value = {
        "version": PARSE_QA_EVIDENCE_VERSION,
        "source_manifest_id": source_manifest_id,
        "document_id": report.document_id,
        "passed": report.passed,
        "lines": [
            {
                "name": line.name,
                "numerator": line.numerator,
                "denominator": line.denominator,
                "measured": line.measured.hex(),
                "threshold": line.threshold.hex(),
                "passed": line.passed,
            }
            for line in report.lines
        ],
    }
    return hashlib.sha256(canonical_mapping_bytes(value)).hexdigest()
```

Reject a report unless `report.passed` is true, the six blocking lines occur
exactly once in the canonical order above, every line passes, and
`excerpt_fit` has a positive denominator; its `0/0` form is explicitly
unmeasured, while the existing QA semantics legitimately measure some other
empty populations as 1.0 or 0.0. Also require
`source_manifest_id` to be a lowercase SHA-256 and bind the report's document
identity as shown.

- [ ] **Step 6: Run inventory, corpus, QA, and compatibility tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/corpus/test_dense_inventory.py tests/unit/corpus/test_qa.py tests/unit/retrieval/test_bm25.py tests/unit/retrieval/test_dense.py -q
```

Expected: PASS, including the three real-corpus constants when restricted files are present.

- [ ] **Step 7: Run static checks and commit**

Run:

```bash
.venv/bin/python -m ruff check src/specpilot/corpus/dense_inventory.py src/specpilot/corpus/qa.py tests/unit/corpus/test_dense_inventory.py
.venv/bin/python -m mypy src/specpilot/corpus/dense_inventory.py src/specpilot/corpus/qa.py
git add src/specpilot/corpus/dense_inventory.py src/specpilot/corpus/qa.py src/specpilot/retrieval/local.py tests/unit/corpus/test_dense_inventory.py tests/unit/corpus/test_qa.py
git commit -m "feat: attest the complete dense inventory"
```

---

### Task 7: Implement freeze and load-time verification services

**Files:**
- Create: `src/specpilot/corpus/freezing.py`
- Create: `tests/unit/corpus/test_corpus_freezing.py`
- Modify: `tests/helpers/corpus_manifest_factory.py`

**Interfaces:**
- Consumes: both manifest stores, source RFC snapshots, parse QA/tokenizer, model weight hashing, LocalCorpus/BM25, dense reader/admin, inventory functions, and collection leases.
- Produces: `CorpusSourceInput`, `FreezeCorpusRequest`, `VerifyCorpusRequest`, `FreezeResult`, `VerifiedCorpus`, `CorpusRefusalCode`, `CorpusManifestRefusal`, `freeze_corpus()`, and `verify_corpus()`.

- [ ] **Step 1: Build deterministic fake source and Qdrant fixtures**

The fake dense backend records call order and exposes mutable schema/count/records/snapshots. It must support a hook that mutates only after snapshot creation:

```python
class FakeDense:
    def __init__(
        self,
        schema: QdrantCollectionSchema,
        records: tuple[DenseRecord, ...],
    ) -> None:
        self.schema = schema
        self.records = records
        self.snapshot_values: tuple[DenseSnapshot, ...] = ()
        self.snapshot_calls = 0
        self.after_snapshot: Callable[[], None] | None = None

    def collection_schema(self) -> QdrantCollectionSchema:
        return self.schema

    def point_count(self) -> int:
        return len(self.records)

    def iter_records(self, *, batch_size: int = 256) -> Iterator[DenseRecord]:
        del batch_size
        yield from self.records

    def snapshots(self) -> tuple[DenseSnapshot, ...]:
        return self.snapshot_values

    def create_snapshot(self) -> DenseSnapshot:
        self.snapshot_calls += 1
        snapshot = DenseSnapshot("fake.snapshot", "a" * 64, 4096)
        self.snapshot_values = (*self.snapshot_values, snapshot)
        if self.after_snapshot is not None:
            self.after_snapshot()
        return snapshot

    def __enter__(self) -> FakeDense:
        return self

    def __exit__(self, *ignored: object) -> None:
        return None
```

Use monkeypatches for `load_token_counter()` and `weights_sha256()` so unit tests never load the 2 GB model.
Monkeypatch both `DenseIndex.open()` and `DenseSnapshotAdmin.open()` to return the same `FakeDense`, so snapshot-call counts and post-snapshot drift are observed through the production orchestration path.
Define a local `FreezeFixture` dataclass with fields
`request: FreezeCorpusRequest`, `source_store: ManifestStore`,
`corpus_store: CorpusManifestStore`, and `dense: FakeDense`; the
`freeze_fixture` pytest fixture
creates two minimal valid RFCXML v3 files with distinct identities (replace
`number="9999"` in the second fixture with `number="9998"`) and creates each
stored v2 source manifest from its actual bytes and parsed identity. It then
creates matching `IndexUnit`/`DenseRecord` values with the helper factories
from Tasks 1 and 6. Reusing one document identity twice remains a separate
refusal test, not the success fixture.

- [ ] **Step 2: Write success, idempotence, and successor tests**

Assert canonical source order ignores CLI pair order; first freeze calls snapshot once; successful replay with a different `created_at` returns the same manifest/snapshot without another call; explicit successor is required after a prior snapshot disappears:

```python
def test_successful_replay_does_not_create_a_second_snapshot(
    freeze_fixture: FreezeFixture,
) -> None:
    first = freeze_corpus(freeze_fixture.request, source_store=freeze_fixture.source_store, corpus_store=freeze_fixture.corpus_store)
    replay_request = replace(
        freeze_fixture.request,
        created_at=freeze_fixture.request.created_at + timedelta(hours=1),
    )
    second = freeze_corpus(replay_request, source_store=freeze_fixture.source_store, corpus_store=freeze_fixture.corpus_store)
    assert second.replayed is True
    assert second.manifest == first.manifest
    assert freeze_fixture.dense.snapshot_calls == 1
```

- [ ] **Step 3: Write every fail-closed mismatch test**

Separate first-freeze and post-seal expectations. First freeze directly refuses
invalid source bytes/identity, failed or unmeasured QA, invalid v1 dense schema,
collection-name mismatch, count/inventory mismatch, wrong predecessor, and
pre/post-snapshot drift. Parser/chunker/index/embedding constants, model hash,
QA evidence, and BM25 fingerprint have no earlier manifest on first freeze, so
test their mismatch only after one successful seal: monkeypatch the consuming
symbols in `specpilot.corpus.freezing`, then call `verify_corpus()` or exact
intent replay and assert the narrow refusal code. Parameterize live schema,
exact point count, payload, vector, inventory root, missing snapshot, and
changed checksum/size after seal. Assert snapshot-boundary drift never calls
store publication. Add a crash-injected publication failure and assert the
orphan snapshot is never guessed as authoritative on retry.

Patch symbols where they are consumed—`specpilot.corpus.freezing.load_token_counter`,
`.weights_sha256`, `.DenseIndex.open`, and `.DenseSnapshotAdmin.open`—rather
than patching only their definition modules.

- [ ] **Step 4: Run freezing tests and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/corpus/test_corpus_freezing.py -q`

Expected: FAIL because `specpilot.corpus.freezing` does not exist.

- [ ] **Step 5: Implement requests, results, refusal type, and source canonicalization**

Use exact request/result boundaries:

```python
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
```

Resolve each source through `ManifestStore.read_source()`, one bounded RFC byte snapshot, hash comparison before XML interpretation, and document identity comparison. Reject duplicate document IDs. Sort resolved sources exactly by `(manifest.document_id, manifest.document_version, manifest.manifest_id)` before QA, LocalCorpus, BM25, and manifest fields.

- [ ] **Step 6: Implement shared local preparation and dense observation**

One private `_prepare()` function used by freeze and verify accepts exact
`sources`, `model_dir`, `expected_collection_name`, and `source_store`
arguments and must:

1. Load the real token counter and hash the real model directory.
2. Construct exactly one code-owned `ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)` and use it for every source in both QA and `LocalCorpus`; never import CLI-private `_clause_limits()` and never use bare `ClauseLimits()` for this corpus.
3. Run every QA line for each canonical source and build ordered evidence.
4. Build `LocalCorpus` and `Bm25Index` without reordering within documents.
5. Derive corpus hash with the imported alias `derive_collection_name(corpus_hash, PIPELINE_VERSION, IndexTextPolicy().version)` and require it to equal `expected_collection_name`.
6. Return the local corpus, BM25 index, source IDs, QA hashes, weight hash,
   code-owned versions, and derived hash in one immutable `PreparedCorpus`.

The separate `_observe(prepared, reader)` function normalizes Qdrant schema,
uses the exact count, scrolls every payload/vector, and builds the inventory.
`_intent(prepared, observation, predecessor)` constructs
`CorpusManifestIntent` from only code-owned versions and the exact baseline
protocol constants, importing `BASELINE_DENSE_QUERY` from the same dense module
that passes it explicitly to every online `query_points()` call.

Represent local preparation and the live observation as immutable values so
pre/post-snapshot equality is one exact dense comparison:

```python
@dataclass(frozen=True, slots=True)
class PreparedCorpus:
    source_manifest_ids: tuple[str, ...]
    parse_qa: tuple[ParseQaEvidence, ...]
    embedding_weights_sha256: str
    corpus: LocalCorpus
    bm25: Bm25Index
    derived_corpus_sha256: str


@dataclass(frozen=True, slots=True)
class DenseObservation:
    collection_schema: QdrantCollectionSchema
    point_count: int
    inventory_root_sha256: str
```

- [ ] **Step 7: Implement freeze under one exclusive lease**

```python
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
        with DenseIndex.open(
            request.qdrant_url,
            request.collection_name,
        ) as reader:
            before = _observe(prepared, reader)
            intent = _intent(prepared, before, request.predecessor_manifest_id)
            existing = corpus_store.find_by_intent(intent, lease=lease)
            if existing is not None:
                _require_snapshot(existing.snapshot, reader.snapshots())
                return FreezeResult(existing, replayed=True)
            corpus_store.require_publishable_intent(intent, lease=lease)
            with DenseSnapshotAdmin.open(
                request.qdrant_url, request.collection_name, lease
            ) as admin:
                snapshot = admin.create_snapshot()
            after = _observe(prepared, reader)
            if after != before:
                raise CorpusManifestRefusal("collection_changed_during_freeze")
            manifest = corpus_store.create(
                CorpusManifestDraft(
                    **intent.model_dump(),
                    snapshot=_snapshot_binding(snapshot),
                    created_at=request.created_at,
                ),
                lease=lease,
            )
            return FreezeResult(manifest, replayed=False)
```

Context managers close reader/admin in all paths. If a matching manifest's
snapshot is missing or changed, raise `corpus_snapshot_mismatch`; do not create
another snapshot. `require_publishable_intent()` validates a nonmatching
intent's explicit predecessor before Qdrant creates a snapshot, and
`create()` revalidates it before publication. Translate only
`CorpusPredecessorError` (existence, same-collection, or new-successor failure) into
`corpus_predecessor_mismatch`; do not blanket-catch `ValueError`, because
canonical-record corruption and unrelated programming errors must remain
distinct failures.

- [ ] **Step 8: Implement verify as the only startup loader**

Read and canonical-validate the manifest, require the request's source ID set
to equal the bound set, canonicalize the pairs by referenced source metadata,
prepare from the manifest-bound collection name, verify snapshot metadata
before the full live observation, and compare the reconstructed
`CorpusManifestIntent` exactly with `manifest.intent`:

```python
@dataclass(slots=True)
class VerifiedCorpus:
    manifest: CorpusManifest
    corpus: LocalCorpus
    bm25: Bm25Index
    dense: DenseIndex

    def close(self) -> None:
        self.dense.close()
```

Return no writer/admin capability. Map each comparison to the narrowest code in
`CorpusRefusalCode`: source bytes/IDs/order, QA evidence, model hash,
code-owned versions/BM25/retrieval settings, collection name, schema, exact
count, full inventory, snapshot metadata, and predecessor binding never share
one generic mismatch. Unexpected Qdrant or filesystem failures remain I/O
failures for the CLI boundary. `UnsupportedCorpusManifestVersionError` maps to
`unsupported_corpus_manifest_version`, while malformed canonical storage is an
I/O/unavailable failure rather than a version mismatch.

- [ ] **Step 9: Run all freeze service tests and static checks**

Run:

```bash
.venv/bin/python -m pytest tests/unit/corpus/test_corpus_freezing.py tests/unit/corpus/test_dense_inventory.py tests/unit/manifests/test_corpus_manifest_store.py tests/unit/manifests/test_corpus_collection_leases.py -q
.venv/bin/python -m ruff check src/specpilot/corpus/freezing.py tests/unit/corpus/test_corpus_freezing.py
.venv/bin/python -m mypy src/specpilot/corpus/freezing.py
```

Expected: all commands exit 0.

- [ ] **Step 10: Commit freeze and verification services**

```bash
git add src/specpilot/corpus/freezing.py tests/unit/corpus/test_corpus_freezing.py tests/helpers/corpus_manifest_factory.py
git commit -m "feat: freeze and verify corpus manifests"
```

---

### Task 8: Expose fail-closed `corpus freeze` and `corpus verify` commands

**Files:**
- Modify: `src/specpilot/cli.py`
- Create: `tests/cli/test_corpus_manifest.py`
- Modify: `tests/cli/test_annotation_pooling.py`

**Interfaces:**
- Consumes: `FreezeCorpusRequest`, `VerifyCorpusRequest`, `freeze_corpus()`, `verify_corpus()`, both manifest stores, and existing `_refuse()`/`_emit()` conventions.
- Produces: `specpilot corpus freeze` and `specpilot corpus verify`.

- [ ] **Step 1: Write parser and paired-source validation tests**

Assert both commands require the documented arguments, repeated `--manifest`
and `--xml` counts match, verify takes no collection override, timestamps match
the same strict RFC3339 syntax as manifest storage (including an explicit
offset), and duplicate source pairs are rejected:

```python
def test_freeze_refuses_unpaired_sources(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([
        "corpus", "freeze",
        "--source-manifest-dir", "source",
        "--corpus-manifest-dir", "corpus",
        "--manifest", "a" * 64,
        "--manifest", "b" * 64,
        "--xml", "source.xml",
        "--model-dir", "model",
        "--qdrant-url", "http://127.0.0.1:6333",
        "--collection", "specpilot_test",
        "--created-at", "2026-08-09T11:00:00Z",
    ])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "source_pair_count_mismatch\n"
```

- [ ] **Step 2: Write success, replay, verify, refusal, and privacy tests**

Monkeypatch service calls with concrete `FreezeResult`/`VerifiedCorpus` fixtures. Assert exact JSON keys and stable code mapping. Include source markers and exception messages that must not appear:

```python
assert set(payload) == {
    "status", "corpus_manifest_id", "source_manifest_ids", "collection",
    "point_count", "derived_corpus_sha256", "inventory_root_sha256",
    "snapshot_name", "snapshot_checksum", "snapshot_size_bytes",
}
assert "secret clause prose" not in captured.out + captured.err
```

Map `CorpusManifestRefusal.code` to exit 2, malformed arguments to exit 4, and
`OSError`, `DenseBackendUnavailable`, or `EmbeddingRuntimeUnavailable` to
`corpus_manifest_unavailable` with exit 3. Unsupported corpus schema has its
own stable refusal code. None of these handlers interpolate the exception, and
tests make the fake backend exception text contain source markers to prove it
never reaches stdout/stderr.

- [ ] **Step 3: Run CLI tests and verify RED**

Run: `.venv/bin/python -m pytest tests/cli/test_corpus_manifest.py -q`

Expected: FAIL because the two commands are not registered.

- [ ] **Step 4: Add handlers with no domain logic**

Construct repeated pairs and delegate:

```python
def _source_inputs(arguments: argparse.Namespace) -> tuple[CorpusSourceInput, ...] | str:
    if len(arguments.manifest) != len(arguments.xml):
        return "source_pair_count_mismatch"
    pairs = tuple(
        CorpusSourceInput(manifest_id, xml_path)
        for manifest_id, xml_path in zip(arguments.manifest, arguments.xml, strict=True)
    )
    if len({item.manifest_id for item in pairs}) != len(pairs):
        return "duplicate_source_manifest"
    return pairs
```

`_corpus_freeze()` and `_corpus_verify()` instantiate `ManifestStore` and `CorpusManifestStore`, call the service once, close verified handles in `finally`, and emit only manifest metadata. Do not duplicate QA, hashing, Qdrant, or snapshot logic in CLI.

- [ ] **Step 5: Register exact command arguments**

First make `_aware_timestamp()` require the existing manifest RFC3339 pattern
before calling `datetime.fromisoformat()`; inputs with a space separator,
missing offset, or more than six fractional digits raise
`argparse.ArgumentTypeError` without echoing domain data. Then add under the
existing `corpus` group:

```python
freeze = corpus.add_parser("freeze")
freeze.add_argument("--source-manifest-dir", type=Path, required=True)
freeze.add_argument("--corpus-manifest-dir", type=Path, required=True)
freeze.add_argument("--manifest", action="append", required=True)
freeze.add_argument("--xml", action="append", type=Path, required=True)
freeze.add_argument("--model-dir", type=Path, required=True)
freeze.add_argument("--qdrant-url", required=True)
freeze.add_argument("--collection", required=True)
freeze.add_argument("--predecessor", default=None)
freeze.add_argument("--created-at", type=_aware_timestamp, required=True)
freeze.set_defaults(handler=_corpus_freeze)

verify = corpus.add_parser("verify")
verify.add_argument("--source-manifest-dir", type=Path, required=True)
verify.add_argument("--corpus-manifest-dir", type=Path, required=True)
verify.add_argument("--corpus-manifest", required=True)
verify.add_argument("--manifest", action="append", required=True)
verify.add_argument("--xml", action="append", type=Path, required=True)
verify.add_argument("--model-dir", type=Path, required=True)
verify.add_argument("--qdrant-url", required=True)
verify.set_defaults(handler=_corpus_verify)
```

- [ ] **Step 6: Run all CLI and focused domain tests**

Run:

```bash
.venv/bin/python -m pytest tests/cli/test_corpus_manifest.py tests/cli/test_corpus_parse.py tests/cli/test_annotation_pooling.py tests/unit/corpus/test_corpus_freezing.py -q
```

Expected: PASS with no source content in captured output.

- [ ] **Step 7: Run static checks and commit**

Run:

```bash
.venv/bin/python -m ruff check src/specpilot/cli.py tests/cli/test_corpus_manifest.py
.venv/bin/python -m mypy src/specpilot/cli.py
git add src/specpilot/cli.py tests/cli/test_corpus_manifest.py tests/cli/test_annotation_pooling.py
git commit -m "feat: expose corpus freeze verification"
```

---

### Task 9: Prove the real Qdrant freeze, seal the manifest, and close W2 Task 6

**Files:**
- Create: `tests/integration/qdrant/test_corpus_freeze.py`
- Modify: `tests/integration/qdrant/test_collection.py`
- Modify: `docs/superpowers/plans/2026-08-08-w2-corpus-and-retrieval.md`
- Modify: `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`
- Create: `docs/reports/2026-08-09-corpus-manifest-freeze.md`
- Create locally, do not commit: `manifests/local/r0/corpus/<manifest-id>.json`
- Create in Qdrant storage, do not commit: collection snapshot bound by that manifest.

**Interfaces:**
- Consumes: the complete implementation, current 1,922-point collection, source manifests/XML, BGE-M3 model cache, and Qdrant 1.12.4.
- Produces: live integration evidence, one real immutable corpus manifest, one real Qdrant snapshot, a successful replay/verify record, and W2 Task 6 completion documentation.

- [ ] **Step 1: Write end-to-end Qdrant mutation tests**

Define the integration fixture type explicitly:

```python
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
```

The function-scoped `real_freeze` fixture replaces `number="9999"` in
`rfc_factory.QA_RFC_XML` with
`str(900_000 + uuid.uuid4().int % 100_000_000)`,
writes those bytes, creates its v2 source manifest, reconstructs its
`LocalCorpus`, and derives the collection name normally from that corpus. Do
not append a random suffix to the versioned collection name. Changing the RFC
identity changes unit IDs and therefore yields an independently valid unique
name for reruns and parallel workers. The fixture then
acquires a write lease from a temporary `CorpusManifestStore`, creates the
collection through `DenseIndexWriter`, and upserts one deterministic 1,024-wide
vector per local unit. Monkeypatch only `load_token_counter()` to return a
deterministic word counter and `weights_sha256()` to return `"a" * 64`; Qdrant
itself remains real. Release the shared lease before freezing. Freeze once,
return the manifest, its snapshot name, the first `DenseRecord`, and a raw
test-only `QdrantClient(url=qdrant_url, trust_env=False)`. In fixture teardown,
delete snapshots/collection only through that raw client and always call
`admin.close()`.

Against a fresh unique test collection per test, freeze and verify
successfully, then use the raw test-only client as disaster-recovery admin to
mutate one payload or one vector without changing point count. Each mutation
must make verify refuse
`dense_point_inventory_mismatch`. Delete the snapshot and assert
`corpus_snapshot_mismatch`:

```python
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
    with pytest.raises(CorpusManifestRefusal) as raised:
        verify_corpus(
            real_freeze.verify_request,
            source_store=real_freeze.source_store,
            corpus_store=real_freeze.corpus_store,
        )
    assert raised.value.code == "dense_point_inventory_mismatch"
```

The snapshot-missing case calls the exact 1.12 API:

```python
real_freeze.admin.delete_snapshot(
    collection_name=real_freeze.collection,
    snapshot_name=real_freeze.snapshot_name,
    wait=True,
)
```

Cleanup uses only the raw test admin because application writers must be permanently unavailable after seal.

- [ ] **Step 2: Run the complete unit/CLI suite before starting services**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest tests/unit tests/cli -q
```

Expected: all checks pass. Record exact test totals for the report.

- [ ] **Step 3: Record initial container-runtime state and start only Qdrant**

Run `docker context show`, `docker compose -f compose.yaml -f compose.index.yaml --profile real ps qdrant`, and the local runtime status command in use on the machine. Record whether Qdrant/runtime were already running. Then run:

```bash
docker compose -f compose.yaml -f compose.index.yaml --profile real up --wait qdrant
```

Expected: Qdrant reports healthy on `127.0.0.1:6333`. Do not start PostgreSQL, API, MCP, or providers.

- [ ] **Step 4: Run Qdrant integration tests without skips**

Run:

```bash
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6333 .venv/bin/python -m pytest tests/integration/qdrant -q
```

Expected: PASS and zero skips. Record the exact total.

- [ ] **Step 5: Capture one explicit UTC timestamp for the real seal**

Open one persistent PTY shell session for Steps 5–8 and send every subsequent
command to that same session (for example, `exec_command(..., tty=True)` once,
then `write_stdin`). Do not run these exports in independent shells. Run:

```bash
export SPECPILOT_CORPUS_CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\n' "$SPECPILOT_CORPUS_CREATED_AT"
```

Expected: one RFC3339 value retained unchanged for both freeze calls.

- [ ] **Step 6: Freeze the existing real collection**

Define and invoke one exact shell function, then extract the returned manifest ID from a private temporary output file:

```bash
specpilot_freeze_corpus() {
  .venv/bin/python -m specpilot.cli corpus freeze \
    --source-manifest-dir manifests/local/r0/source \
    --corpus-manifest-dir manifests/local/r0/corpus \
    --manifest af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691 \
    --xml artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml \
    --manifest 3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2 \
    --xml artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml \
    --model-dir data/cache/models/bge-m3 \
    --qdrant-url http://127.0.0.1:6333 \
    --collection specpilot_ff4841e2d846388014efa06870fbbdb7 \
    --created-at "$SPECPILOT_CORPUS_CREATED_AT"
}
export SPECPILOT_FREEZE_OUTPUT="$(mktemp /private/tmp/specpilot-corpus-freeze.XXXXXX)"
specpilot_freeze_corpus | tee "$SPECPILOT_FREEZE_OUTPUT"
export SPECPILOT_CORPUS_MANIFEST_ID="$(
  .venv/bin/python -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["corpus_manifest_id"])' \
    "$SPECPILOT_FREEZE_OUTPUT"
)"
```

Expected JSON: `status="frozen"`, `point_count=1922`, the expected corpus hash and collection, a 64-character inventory root and snapshot checksum, positive snapshot size, and one corpus manifest ID. Record all nonrestricted metadata.

- [ ] **Step 7: Prove successful replay creates no second snapshot**

Run:

```bash
export SPECPILOT_SNAPSHOT_COUNT_BEFORE="$(
  .venv/bin/python -c \
    'from specpilot.retrieval.dense import DenseIndex; i=DenseIndex.open("http://127.0.0.1:6333", "specpilot_ff4841e2d846388014efa06870fbbdb7"); print(len(i.snapshots())); i.close()'
)"
specpilot_freeze_corpus
export SPECPILOT_SNAPSHOT_COUNT_AFTER="$(
  .venv/bin/python -c \
    'from specpilot.retrieval.dense import DenseIndex; i=DenseIndex.open("http://127.0.0.1:6333", "specpilot_ff4841e2d846388014efa06870fbbdb7"); print(len(i.snapshots())); i.close()'
)"
test "$SPECPILOT_SNAPSHOT_COUNT_BEFORE" = "$SPECPILOT_SNAPSHOT_COUNT_AFTER"
```

Expected: CLI reports `status="replayed"`, the manifest/snapshot IDs are unchanged, and the snapshot count did not increase.

- [ ] **Step 8: Verify the sealed corpus through the startup gate**

Run in the same shell:

```bash
.venv/bin/python -m specpilot.cli corpus verify \
  --source-manifest-dir manifests/local/r0/source \
  --corpus-manifest-dir manifests/local/r0/corpus \
  --corpus-manifest "$SPECPILOT_CORPUS_MANIFEST_ID" \
  --manifest af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691 \
  --xml artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml \
  --manifest 3a752dd99f78398815252baa322e1ad0e9963ade5eb66e2861d8c2bede2 \
  --xml artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml \
  --model-dir data/cache/models/bge-m3 \
  --qdrant-url http://127.0.0.1:6333
```

Expected: `status="verified"` and the same manifest, collection, point count, inventory root, and snapshot metadata.

- [ ] **Step 9: Verify privacy, permissions, and ignore rules**

Run:

```bash
git check-ignore manifests/local/r0/corpus artifacts/restricted data/cache/models/bge-m3
stat -f '%Sp %N' manifests/local/r0/corpus manifests/local/r0/corpus/.locks
find manifests/local/r0/corpus -maxdepth 1 -type f -exec stat -f '%Sp %N' {} \;
git status --short
```

Expected: restricted/local paths are ignored; corpus manifest files are `-rw-------`; the store and `.locks` directories are 0700; Git shows only intended source/test/doc changes and no source, vector, inventory, or local manifest artifacts.

- [ ] **Step 10: Run final full verification**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6333 .venv/bin/python -m pytest tests/integration/qdrant -q
```

Expected: all commands pass; PostgreSQL-only tests may skip only when their documented DSN is absent, while Qdrant tests must not skip.

- [ ] **Step 11: Restore the initial service/runtime state**

If Step 3 found Qdrant stopped, run:

```bash
docker compose -f compose.yaml -f compose.index.yaml --profile real stop qdrant
```

If the container runtime itself was initially stopped and this task started it, stop that runtime too. Do not delete the Qdrant volume, current collection, new snapshot, manifest, or any pre-existing collection.

- [ ] **Step 12: Write the completion report and update W2 status**

The report records: implementation commits; source IDs; pooling run/seal IDs; corpus manifest ID; collection/snapshot/checksum/size; expected BM25/corpus/inventory hashes; 1,922 count; QA pass; replay evidence; writer revocation; focused/full/integration totals; skipped PG-only tests; service-state restoration; and restricted artifact locations without prose or individual inventory entries.

Mark W2 Task 6 Steps 1–3 complete in `2026-08-08-w2-corpus-and-retrieval.md` and update the master roadmap from “freeze pending” to the exact manifest/snapshot evidence.

- [ ] **Step 13: Commit tests and completion documentation**

Run:

```bash
git add tests/integration/qdrant/test_collection.py tests/integration/qdrant/test_corpus_freeze.py docs/superpowers/plans/2026-08-08-w2-corpus-and-retrieval.md docs/roadmaps/2026-08-06-specpilot-master-roadmap.md docs/reports/2026-08-09-corpus-manifest-freeze.md
git commit -m "docs: record the frozen corpus manifest"
```

Expected: commit contains no local manifest, source text, vector, full inventory, model file, or snapshot body.

---

## Final Acceptance Checklist

- [ ] Contract IDs are canonical, strict, immutable, and field-sensitive.
- [ ] Source-manifest golden IDs are unchanged.
- [ ] Secure enumeration rejects symlinks, FIFOs, hard links, bad modes, oversized/noncanonical/unknown records, and directory swaps.
- [ ] A live writer lease blocks freeze; a published manifest permanently blocks later writers.
- [ ] Read-only dense handles expose no mutations; writer/admin operations require active matching leases.
- [ ] Full live inventory binds deterministic point ID, exact payload, source/indexed hashes, and each float32 vector hash.
- [ ] Freeze reruns all QA, binds real model weights, creates one real snapshot, double-checks collection state, and publishes under the same exclusive lease.
- [ ] Successful replay reuses the same manifest/snapshot; missing or changed snapshot fails closed; recovery requires an explicit successor.
- [ ] Verify rejects every source/config/schema/count/payload/vector/inventory/snapshot mismatch.
- [ ] RRF uses full §8.5.1 deduplication identity and numeric stable tie ordering while pooling remains independent-route-only.
- [ ] The real corpus remains 1,922 points with BM25 `8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3`, corpus `46616bd050308f6f77782afe8706b8e2d8f577de9b9b698e228e1c52b40596eb`, and collection `specpilot_ff4841e2d846388014efa06870fbbdb7`.
- [ ] Full tests and mandatory Qdrant integration tests pass; only documented PostgreSQL-only tests may skip.
- [ ] Restricted/local artifacts remain ignored and private; initial service/runtime state is restored.
