# W0 Safety and Egress Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build SpecPilot’s independent, testable W0 foundation so unsafe documents and unauthorized provider payloads fail closed before any real 3GPP excerpt can leave the machine.

**Architecture:** A Python 3.12+ package separates pure policy/domain logic from filesystem, PostgreSQL, and network adapters. Archive/OOXML inputs are preflighted and quarantined before parsing; immutable manifests bind every source and compliance decision; provider calls are reachable only through a policy enforcer backed by an atomic PostgreSQL reservation ledger. The W0 demo and smoke paths use synthetic fixtures and deterministic transports only.

**Tech Stack:** Python 3.12+, Pydantic 2, psycopg 3, FastAPI health skeleton, defusedxml, pytest, Hypothesis, Ruff, mypy, Docker Compose, PostgreSQL 17, Qdrant, MCP Python SDK (transport skeleton only in W0).

## Global Constraints

- Project lives independently at `/Users/chunxue/Documents/resume_project/specpilot`; `github-ona` is not imported, modified, or used as a runtime dependency.
- Real source text, quotations, extracted DOCX contents, full indexes, caches, and provider credentials stay out of Git.
- W0 performs no gold annotation and exposes no real-corpus cloud route until a successor source manifest records the self-assessed authorization decision.
- Every production function or behavior change follows red-green-refactor; configuration/generated files are verified by explicit smoke checks.
- Any unreadable ledger, ambiguous reservation state, policy mismatch, token-counter failure, or manifest mismatch fails closed before network I/O.
- Default online caps are copied from product-plan v5: L1 unique 5 excerpts/2,560 tokens and L2 run unique 12 excerpts/6,144 tokens; every excerpt is at most 512 model tokens and 8 KiB UTF-8; L2 is additionally limited to 4 excerpts/2,048 tokens per atomic claim.
- Online transmitted caps reserve four stage fan-outs; judge transmitted caps reserve two. Evaluation-root transmitted caps are L1 15,360 tokens/240 KiB and L2 29,696 tokens/464 KiB.
- One scope sits above `evaluation_root_id`: unique disclosures per `corpus_manifest_id`, spanning every case run against that frozen corpus. This is the scope product-plan §3.2 means by "全局唯一内容", and it is the number the outbound-limit premise actually rests on — per-case caps alone permit roughly `40 × 10 + 36 × 17 ≈ 1,012` distinct excerpts, more than one whole specification. The shipped value of 1,024 excerpts/524,288 tokens/8 MiB sits just above that all-distinct worst case, so it is a **tripwire, not a squeeze**: it guarantees the aggregate can never silently exceed one specification's worth, and it does not block the designed evaluation. W5's dev dry-run produces the first real distinct-disclosure count; W0 go/no-go records whether to lower it to something that binds.
- No provider route is authorized by a `SourceManifest` object supplied on the request. Content addressing proves a manifest is internally consistent, not that a compliance decision was ever recorded, so the enforcer resolves every manifest through a `SourceManifestResolver` it owns — the same way it owns the policy, the token counter contract, and the authorization clock.
- CI and fixture smoke output contain no recall, accuracy, F1, or other quality-looking metric.

---

## File map locked for W0

### Project and quality configuration

- `pyproject.toml` — package metadata, Python floor, dependencies, Ruff/mypy/pytest configuration.
- `Makefile` — deterministic setup, unit, integration, lint, typecheck, and fixture-smoke commands.
- `.gitignore` / `.env.example` — prevent real corpora, secrets, caches, manifests with restricted content, and local volumes from entering Git.
- `compose.yaml` — internal PostgreSQL/Qdrant/MCP networks plus demo/real profile skeletons; only API/web may later publish ports.
- `.github/workflows/ci.yml` — lint, typecheck, unit tests, PostgreSQL integration tests, and fixture smoke without model downloads.

### Python package

- `src/specpilot/contracts/archive.py` — archive limits, safe extraction result, rejection codes.
- `src/specpilot/contracts/manifests.py` — immutable source/compliance/provider-route records.
- `src/specpilot/contracts/egress.py` — task, stage, payload, disclosure, usage, and reservation contracts.
- `src/specpilot/ingestion/archive.py` — whole-archive preflight and atomic DOCX extraction.
- `src/specpilot/ingestion/quarantine.py` — content-addressed quarantine records without leaking file contents to logs.
- `src/specpilot/ingestion/ooxml.py` — OOXML package inspection for macro, active content, embedded object, and external relationship risks.
- `src/specpilot/ingestion/sandbox_worker.py` — narrow subprocess entry point for read-only/no-network container execution.
- `src/specpilot/manifests/canonical.py` — canonical JSON and SHA-256 IDs.
- `src/specpilot/manifests/store.py` — create-only filesystem manifest store and successor validation.
- `src/specpilot/egress/policy.py` — versioned policy profiles, field allowlists, and cap selection.
- `src/specpilot/egress/enforcer.py` — pure payload validation and disclosure accounting request.
- `src/specpilot/egress/ledger.py` — ledger protocol and fail-closed errors.
- `src/specpilot/egress/postgres.py` — transactional check-and-reserve implementation.
- `src/specpilot/providers/base.py` — private provider adapter protocol.
- `src/specpilot/providers/transport.py` — the sole public, policy-bound send path.
- `src/specpilot/providers/fake.py` — deterministic fixture provider with call capture.
- `src/specpilot/api/app.py` — non-secret `/health` skeleton only.
- `src/specpilot/mcp_server/app.py` — W0 internal health skeleton; the five tools arrive in W3.
- `src/specpilot/cli.py` — archive inspection, manifest, migration, envelope smoke, and route smoke commands.

### SQL, policy, fixture, and documentation assets

- `migrations/001_egress_ledger.sql` — policy-run, disclosure, reservation, attempt, and route-disclosure tables.
- `src/specpilot/egress/policies/default-v1.json` — exact W0 policy caps and allowed fields. It lives inside the package rather than a repo-root `config/`, so a wheel or Docker install still resolves it; the extension is `.json` because the loader parses JSON and no YAML parser is a dependency.
- `data/fixtures/specs/synthetic-mini-spec.docx` — generated synthetic safe OOXML fixture.
- `tests/fixtures/` — generated malicious archives/OOXML packages; no 3GPP content.
- `docs/compliance/assessment-template.md` — the four required self-assessment sections and explicit uncertainty field.
- `docs/adr/0001-fail-closed-boundaries.md` — why quarantine and provider transport are hard boundaries.
- `docs/runbooks/w0-go-no-go.md` — evidence checklist for routes A/B/C without claiming approval prematurely.

---

### Task 1: Independent package and quality baseline

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `README.md`
- Create: `src/specpilot/__init__.py`
- Create: `src/specpilot/api/app.py`
- Test: `tests/unit/test_package_baseline.py`

**Interfaces:**
- Produces: importable `specpilot.__version__: str` and `create_app() -> FastAPI` with a sanitized `/health` response.
- Consumes: no project code.

- [ ] **Step 1: Write the failing package/health test**

```python
from fastapi.testclient import TestClient

from specpilot import __version__
from specpilot.api.app import create_app


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"


def test_health_exposes_no_runtime_details() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/unit/test_package_baseline.py -q`

Expected: collection fails because `specpilot` does not exist.

- [ ] **Step 3: Add the minimal package and health implementation**

```python
# src/specpilot/__init__.py
__version__ = "0.1.0"
```

```python
# src/specpilot/api/app.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="SpecPilot", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Configure tooling and safe ignore rules**

Use a `src/` package layout, Python `>=3.12,<3.15`, Pydantic/FastAPI/psycopg/defusedxml production dependencies, pytest/Hypothesis/Ruff/mypy test dependencies, strict mypy, and Ruff `E,F,I,B,UP,SIM` rules. Ignore `.env`, `.venv`, `data/real/`, `data/quarantine/`, `data/cache/`, `artifacts/restricted/`, local manifests, provider snapshots containing account metadata, and Compose volumes.

- [ ] **Step 5: Verify GREEN and static baseline**

Run: `python -m pytest tests/unit/test_package_baseline.py -q`

Run: `python -m ruff check .`

Run: `python -m mypy src`

Expected: all commands exit 0 without warnings.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example pyproject.toml Makefile README.md src/specpilot tests/unit/test_package_baseline.py
git commit -m "chore: initialize independent specpilot package"
```

### Task 2: Safe outer ZIP preflight and atomic extraction

**Files:**
- Create: `src/specpilot/contracts/archive.py`
- Create: `src/specpilot/ingestion/archive.py`
- Create: `src/specpilot/ingestion/quarantine.py`
- Test: `tests/unit/ingestion/test_archive_preflight.py`
- Test: `tests/unit/ingestion/test_archive_limits.py`
- Test: `tests/unit/ingestion/test_quarantine.py`

**Interfaces:**
- Produces: `extract_expected_docx(archive_path: Path, destination: Path, quarantine_dir: Path, policy: ArchivePolicy) -> ExtractionResult`.
- Produces: `ArchivePolicy(expected_docx_name: str, max_members: int, max_member_bytes: int, max_total_bytes: int, max_compression_ratio: float = 100.0)` and stable `ArchiveRejectionCode` values.
- Consumes: local filesystem only; performs no network access.

- [ ] **Step 1: Write failing traversal/symlink/unexpected-member tests**

```python
@pytest.mark.parametrize(
    ("member_name", "external_attr", "expected_code"),
    [
        ("../escape.docx", 0, ArchiveRejectionCode.PATH_TRAVERSAL),
        ("/absolute.docx", 0, ArchiveRejectionCode.ABSOLUTE_PATH),
        ("expected.docx", 0o120777 << 16, ArchiveRejectionCode.SYMLINK),
        ("payload.exe", 0, ArchiveRejectionCode.UNEXPECTED_MEMBER),
        ("nested.zip", 0, ArchiveRejectionCode.NESTED_ARCHIVE),
    ],
)
def test_unsafe_member_quarantines_whole_archive(...):
    archive = build_zip(tmp_path, member_name, external_attr=external_attr)
    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(archive, corpus_dir, quarantine_dir, policy())
    assert raised.value.code is expected_code
    assert not corpus_dir.exists() or not any(corpus_dir.iterdir())
    assert one_quarantine_record_exists(quarantine_dir)
```

- [ ] **Step 2: Run traversal tests and verify RED**

Run: `python -m pytest tests/unit/ingestion/test_archive_preflight.py -q`

Expected: import failure because archive contracts and extractor do not exist.

- [ ] **Step 3: Implement normalized whole-archive preflight**

The preflight must inspect every `ZipInfo` before opening any member, reject absolute/drive-qualified paths, reject any normalized path containing `..`, reject Unix symlink/device modes from `external_attr`, accept exactly one regular member whose basename equals `expected_docx_name`, and reject nested archive extensions case-insensitively. It must not call `ZipFile.extract()`.

- [ ] **Step 4: Write failing encrypted/count/size/ratio tests**

```python
def test_member_count_limit_is_checked_before_writes(...): ...
def test_declared_member_size_limit_is_checked_before_writes(...): ...
def test_streamed_size_limit_catches_misreported_member(...): ...
def test_total_uncompressed_limit_is_checked_before_writes(...): ...
def test_encrypted_flag_is_rejected(...): ...
def test_high_compression_ratio_is_rejected(...): ...
```

- [ ] **Step 5: Run limit tests and verify RED**

Run: `python -m pytest tests/unit/ingestion/test_archive_limits.py -q`

Expected: each new test fails at the missing limit behavior, not fixture construction.

- [ ] **Step 6: Implement streamed extraction into a sibling temporary directory**

Read in bounded chunks while rechecking actual byte counts and SHA-256. `fsync` the file, rename the completed temporary directory atomically to the destination, and remove only the task-created temporary path on failure. Never follow destination symlinks. The result records archive hash, DOCX hash, byte count, and member name but never file contents.

- [ ] **Step 7: Write and implement content-addressed quarantine tests**

Assert that quarantine creates `<sha256>/record.json` plus the original archive with mode `0600`, records rejection code and safe metadata only, and is idempotent for the same archive hash. Logs and exceptions must not include extracted bytes or DOCX XML.

- [ ] **Step 8: Run archive unit suite**

Run: `python -m pytest tests/unit/ingestion -q`

Expected: all archive and quarantine tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/specpilot/contracts/archive.py src/specpilot/ingestion tests/unit/ingestion
git commit -m "feat: quarantine unsafe source archives"
```

### Task 3: OOXML active-content inspection boundary

**Files:**
- Create: `src/specpilot/ingestion/ooxml.py`
- Create: `src/specpilot/ingestion/sandbox_worker.py`
- Create: `tests/helpers/ooxml_factory.py`
- Test: `tests/unit/ingestion/test_ooxml_inspection.py`
- Test: `tests/integration/ingestion/test_sandbox_worker.py`

**Interfaces:**
- Produces: `inspect_docx(path: Path, limits: OoxmlLimits) -> OoxmlInspection`.
- Produces: CLI `python -m specpilot.ingestion.sandbox_worker inspect --input /input/source.docx --output /output/inspection.json`.
- Consumes: an already safely extracted DOCX; emits metadata/findings only.

- [ ] **Step 1: Write failing tests for macros, executable embeddings, nested packages, and external relationships**

```python
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("macro_content_type", OoxmlRejectionCode.MACRO),
        ("vba_project", OoxmlRejectionCode.MACRO),
        ("embedded_executable", OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT),
        ("ole_object", OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT),
        ("external_relationship", OoxmlRejectionCode.EXTERNAL_RELATIONSHIP),
    ],
)
def test_active_content_never_becomes_parseable_fixture(...): ...
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/ingestion/test_ooxml_inspection.py -q`

Expected: import failure for `inspect_docx`.

- [ ] **Step 3: Implement package inspection without XML entity/network resolution**

Use `defusedxml` for `[Content_Types].xml` and `.rels` files, disable entity expansion, validate OOXML member paths with the same path policy, cap member count and total uncompressed bytes, reject macro-enabled content types and active embeddings, and collect external relationship target hashes without resolving targets. W0’s fail-closed policy quarantines an external-relationship input; a later explicitly reviewed sanitizer may create a separate derivative, never silently promote the original.

- [ ] **Step 4: Write failing subprocess boundary test**

Start the worker with an input directory that is read-only and a separate writable output directory. Assert a valid fixture yields one JSON inspection file, unsafe input exits non-zero with a stable code, and stdout/stderr contain no XML or relationship target text.

- [ ] **Step 5: Implement the narrow worker and verify GREEN**

Run: `python -m pytest tests/unit/ingestion/test_ooxml_inspection.py tests/integration/ingestion/test_sandbox_worker.py -q`

Expected: safe DOCX accepted; every malicious fixture rejected before parsing content.

- [ ] **Step 6: Commit**

```bash
git add src/specpilot/ingestion/ooxml.py src/specpilot/ingestion/sandbox_worker.py tests/helpers tests/unit/ingestion tests/integration/ingestion
git commit -m "feat: inspect docx packages in a fail-closed boundary"
```

### Task 4: Canonical immutable source manifests

**Files:**
- Create: `src/specpilot/contracts/manifests.py`
- Create: `src/specpilot/manifests/canonical.py`
- Create: `src/specpilot/manifests/store.py`
- Test: `tests/unit/manifests/test_source_manifest.py`
- Test: `tests/unit/manifests/test_manifest_store.py`

**Interfaces:**
- Produces: `SourceManifestDraft`, `SourceManifest`, `ComplianceAssessment`, `ProviderRouteBinding`, and `ManifestStore.create_source(...)` / `create_successor(...)`.
- Produces: `canonical_sha256(model: BaseModel) -> str` with stable UTF-8 JSON ordering.
- Consumes: archive/DOCX hashes from Task 2 and explicit compliance evidence metadata; never derives authorization implicitly.

- [ ] **Step 1: Write failing canonicalization and default-deny tests**

```python
def test_initial_source_manifest_is_default_deny() -> None:
    manifest = build_initial_source_manifest()
    assert manifest.cloud_egress_authorized is False
    assert manifest.predecessor_manifest_id is None


def test_manifest_id_is_content_addressed_and_order_independent() -> None:
    first = canonical_sha256(SourceManifestDraft(**fields_in_order_a))
    second = canonical_sha256(SourceManifestDraft(**fields_in_order_b))
    assert first == second
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/manifests/test_source_manifest.py -q`

Expected: missing manifest contracts.

- [ ] **Step 3: Implement strict immutable Pydantic contracts and canonical hashing**

Use `ConfigDict(frozen=True, extra="forbid")`, timezone-aware timestamps, `https` download URLs, lowercase 64-hex hashes, explicit document/version fields, and manifest IDs derived from canonical content excluding the ID field itself.

- [ ] **Step 4: Write failing successor invariants**

Assert an authorized successor must preserve document/version/source hashes, reference the predecessor, bind one provider route plus use (`online_main` or `offline_judge`), include all four compliance-review sections and snapshot hashes, record uncertainty, and use a non-expired decision. Assert old/default-deny manifests remain unchanged and cannot satisfy an authorized route.

- [ ] **Step 5: Implement create-only store and successor validation**

Write and fsync a mode-`0600` sibling temporary file, install it with an atomic no-replace hard link, fsync the directory, then remove the temporary name. A same-ID replay may return the existing byte-identical object; differing bytes or attempted overwrite fail closed.

- [ ] **Step 6: Verify manifest suite**

Run: `python -m pytest tests/unit/manifests -q`

Expected: canonical, immutability, expiry, purpose/provider mismatch, and successor tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/specpilot/contracts/manifests.py src/specpilot/manifests tests/unit/manifests
git commit -m "feat: add immutable source manifest chain"
```

### Task 5: Pure egress policy and disclosure accounting

**Files:**
- Create: `src/specpilot/contracts/egress.py`
- Create: `src/specpilot/egress/policy.py`
- Create: `src/specpilot/egress/enforcer.py`
- Create: `src/specpilot/egress/policies/default-v1.json`
- Test: `tests/unit/egress/test_policy_projection.py`
- Test: `tests/unit/egress/test_disclosure_caps.py`
- Test: `tests/unit/egress/test_maximum_legal_envelope.py`

**Interfaces:**
- Produces: `EgressPolicyEnforcer(policy, *, manifests: SourceManifestResolver, clock)` with `prepare(request, counter) -> ReservationRequest` and `apply_reservation(usage, corpus_usage, request, counter) -> ReservationOutcome`.
- Produces: `disclosure_id(corpus_manifest_id, content_hash, quote_hash, normalized_excerpt_span) -> str`, which deliberately excludes run and root so one excerpt has one identity corpus-wide.
- Consumes: a **stored** source manifest resolved by ID through Task 4's store, and a mandatory model-compatible token counter. The manifest carried on the request is compared against the stored one and is never itself the basis of the decision.

- [ ] **Step 1: Write failing field-allowlist and local-object tests**

Assert full clauses, raw retrieval candidates, full TOCs, stack traces, source paths, secrets, and arbitrary extra fields cannot be encoded as an egress payload. Assert only projected query/claim, necessary version metadata, bounded TOC nodes, and `EvidenceExcerpt` values are accepted.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/egress/test_policy_projection.py -q`

Expected: missing egress contracts and enforcer.

- [ ] **Step 3: Implement typed whitelist projection and mandatory token counting**

No public method accepts `dict[str, Any]`. Pydantic request variants enumerate allowed fields per stage. A missing/failing token counter raises `TokenAccountingUnavailable` before reservation.

- [ ] **Step 4: Write failing unique/transmitted/per-claim tests**

Cover the exact L1/L2/judge/root caps, 512-token and 8-KiB excerpt caps, different spans from one clause as different disclosures, duplicate disclosures as one unique item but repeated transmitted use, cross-provider resend, TOC 12-per-call/24-per-run limits, and L2 atomic-claim 4/2,048 limits.

- [ ] **Step 5: Implement cap selection and stable disclosure identity**

Normalize spans as explicit token/paragraph coordinates, hash the canonical tuple, and return the deltas the ledger must reserve: global unique, per-route unique, stage transmitted, claim unique, and evaluation-root totals.

- [ ] **Step 6: Write the maximum-legal-envelope test before code changes**

Build three L2 claims, 12 distinct 512-token/8-KiB-safe excerpts, Evidence→Compliance→Verifier calls, one retry, and optional judge. Assert it is accepted exactly at 29,696 root transmitted tokens/464 KiB; adding one token, byte, excerpt, TOC node, or field is rejected with a stable public code.

- [ ] **Step 7: Run the pure policy suite**

Run: `python -m pytest tests/unit/egress -q`

Expected: normal, boundary, malicious, and maximum-envelope tests pass without database/network access.

- [ ] **Step 8: Commit**

```bash
git add src/specpilot/contracts/egress.py src/specpilot/egress config/egress tests/unit/egress
git commit -m "feat: enforce egress payload and disclosure caps"
```

### Task 6: Atomic PostgreSQL reservation ledger

**Files:**
- Create: `migrations/001_egress_ledger.sql`
- Create: `src/specpilot/egress/ledger.py`
- Create: `src/specpilot/egress/postgres.py`
- Test: `tests/integration/egress/test_postgres_reservation.py`
- Test: `tests/integration/egress/test_postgres_concurrency.py`
- Test: `tests/integration/egress/test_postgres_recovery.py`

**Interfaces:**
- Produces: async `EgressLedger.check_and_reserve(request: ReservationRequest, *, idempotency_key: str) -> Reservation` and `record_attempt(reservation_id, route, transmitted_usage, outcome) -> Attempt`.
- Consumes: exact deltas and policy snapshot hash produced by Task 5.

**Two constraints inherited from Task 5, both easy to get wrong:**

1. `ReservationOutcome` carries two rows with different keys — usage by `evaluation_root_id`, corpus usage by `corpus_manifest_id`. Both must be read, capped, and written inside **one** transaction. A corpus-usage row read outside the lock, or written on only some paths, silently restores the per-case-only ceiling that scope exists to remove.
2. `apply_reservation` has no idempotency concept and charges transmitted usage on **every** call. That is correct for a genuine resend and wrong for a replayed reservation, so an idempotency-key hit must return the stored `Reservation` directly and **must not** call `apply_reservation` again.

- [x] **Step 1: Write failing migration/first-reservation integration test**

Create a run bound to `(run_id, resolved_egress_policy_id, policy_hash, corpus_manifest_id)`, reserve one disclosure, and assert persisted per-root, per-route, and per-corpus unique totals plus zero plaintext columns. Add a test that two different `evaluation_root_id` values sharing one excerpt produce two usage rows but a single corpus disclosure.

- [x] **Step 2: Run and verify RED against an ephemeral PostgreSQL service**

Run: `make test-integration TEST=tests/integration/egress/test_postgres_reservation.py`

Expected: fails because the migration/repository does not exist.

- [x] **Step 3: Implement append-oriented schema and serializable transaction**

Use row locking on the run budget row, unique constraints on `(run_id, policy_id, idempotency_key)` and disclosure route tuples, check constraints for non-negative counters, and a transaction that revalidates every cap before inserting the reservation. Store hashes/coordinates/counts only; never query, claim, or excerpt text.

- [x] **Step 4: Write failing idempotency/retry tests**

Assert the same stable idempotency key reuses the unique reservation, each actual send attempt increments transmitted usage, a different provider creates a route disclosure and transmitted charge without resetting global unique usage, and an ambiguous/non-final reservation blocks transport.

- [x] **Step 5: Implement attempt accounting and fail-closed states**

Represent `reserved`, `sending`, `succeeded`, and `failed_known` states; absence, connection loss, or unknown commit outcome must raise `LedgerUnavailable`/`ReservationAmbiguous`, which the transport treats as no-send.

- [x] **Step 6: Write failing concurrency and restart tests**

Run at least 20 concurrent tasks racing for the last allowed excerpt and assert exactly one succeeds. Close/recreate the repository and assert the restored run keeps all unique/transmitted totals and rejects over-budget continuation.

- [x] **Step 7: Implement/re-run until concurrency and recovery are GREEN**

Run: `make test-integration TEST='tests/integration/egress/test_postgres_*.py'`

Expected: all integration tests pass repeatedly with no leaked connections.

- [x] **Step 8: Commit**

```bash
git add migrations src/specpilot/egress/ledger.py src/specpilot/egress/postgres.py tests/integration/egress
git commit -m "feat: reserve egress budgets atomically in postgres"
```

### Task 7: Sole policy-bound provider transport and deterministic fake

**Files:**
- Create: `src/specpilot/providers/base.py`
- Create: `src/specpilot/providers/transport.py`
- Create: `src/specpilot/providers/fake.py`
- Test: `tests/unit/providers/test_transport_fail_closed.py`
- Test: `tests/integration/providers/test_transport_ledger_flow.py`

**Interfaces:**
- Produces: `PolicyBoundTransport.send(request: EgressRequest, *, idempotency_key: str) -> ProviderResponse` as the sole importable send API.
- Consumes: Task 5 enforcer, Task 6 ledger, and a private `_ProviderAdapter.send(projected_payload)` protocol.

- [x] **Step 1: Write failing no-send tests**

Use a call-capturing fake adapter and assert its call count remains zero for unauthorized manifests/routes, expired policies, field violations, token/byte overages, ledger read/write failures, ambiguous reservations, and policy-hash mismatch.

- [x] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/providers/test_transport_fail_closed.py -q`

Expected: missing transport.

- [x] **Step 3: Implement prepare → reserve → send → attempt-record flow**

Keep adapter classes in a private module namespace, expose no raw client from dependency injection, and record only provider, endpoint identifier hash, model slug, response metadata allowlist, usage, hashes, duration, and public error code. A provider response body may enter the local response cache later but never ordinary logs.

- [x] **Step 4: Write retry/cross-route integration tests**

Assert one retry reuses the reservation but charges transmitted usage again; a fallback provider records a new route disclosure; failed attempt recording causes the run to be sealed against further sends until reconciled.

- [x] **Step 5: Verify provider suite**

Run: `python -m pytest tests/unit/providers -q`

Run: `make test-integration TEST=tests/integration/providers/test_transport_ledger_flow.py`

Expected: every violation is no-send and all valid attempt counts match policy math.

- [x] **Step 6: Commit**

```bash
git add src/specpilot/providers tests/unit/providers tests/integration/providers
git commit -m "feat: route providers through the egress ledger"
```

### Task 8: W0 CLI, compliance artifacts, and go/no-go validation

**Files:**
- Create: `src/specpilot/cli.py`
- Create: `docs/compliance/assessment-template.md`
- Create: `docs/adr/0001-fail-closed-boundaries.md`
- Create: `docs/runbooks/w0-go-no-go.md`
- Test: `tests/cli/test_ingest_inspect.py`
- Test: `tests/cli/test_manifest_commands.py`
- Test: `tests/cli/test_egress_envelope_smoke.py`

**Interfaces:**
- Produces CLI commands `archive inspect`, `source-manifest create`, `source-manifest authorize-successor`, `egress envelope-smoke`, and `provider route-smoke --fixture-only`.
- Consumes Tasks 2–7; route smoke may send only synthetic fixture text.

- [x] **Step 1: Write failing CLI tests**

Assert JSON stdout contains stable IDs/status only, diagnostics go to stderr without source/payload text, invalid authorization evidence exits non-zero, and envelope smoke checks both the maximum valid case and one-over-limit failures.

- [x] **Step 2: Run and verify RED**

Run: `python -m pytest tests/cli -q`

Expected: CLI entry point missing.

- [x] **Step 3: Implement the narrow commands and document the four-part assessment**

The template requires: source-side terms snapshot/hash and interpretation uncertainty; provider-side retention/training/region/subprocessor snapshot/hash; exact outbound-cap factual premise; explicit author conclusion and bound route/use/expiry. The runbook declares routes A/B/C and forbids calling self-assessment external approval.

- [ ] **Step 4: Perform official-source research and save only permissible snapshots/metadata** — OWNER: the author. Deliberately left unchecked. The conclusion in `author_conclusion` is a self-assessment attributed to a named `author_id`; tooling may validate its completeness and route binding but must never write, infer, or upgrade it. Until it exists, the source manifest stays default-deny and no cloud route is reachable, which is the intended resting state.

Use official 3GPP/ETSI/provider pages. Store URLs, access time, response/content hash, a short original summary, and uncertainties; do not copy long copyrighted passages. Create initial source manifests only after safe download/hash processing. Create an authorized successor only if all required evidence exists and the author’s recorded conclusion is yes; otherwise keep default deny and select B/C according to the product plan.

- [x] **Step 5: Run fixture-only route smoke**

Run: `python -m specpilot.cli provider route-smoke --fixture-only --route main`

Run: `python -m specpilot.cli provider route-smoke --fixture-only --route judge`

Expected: tool calling/structured-output metadata are captured when credentials/routes exist; missing credentials produce a documented blocked result, never a fabricated pass.

- [x] **Step 6: Commit code and non-sensitive records**

```bash
git add src/specpilot/cli.py docs tests/cli
git commit -m "feat: add auditable w0 validation commands"
```

Do not commit real DOCX/ZIP files, full terms pages, credentials, account-specific provider metadata, or restricted excerpts.

### Task 9: Compose and CI foundation

**Files:**
- Create: `compose.yaml`
- Create: `docker/api.Dockerfile`
- Create: `docker/mcp.Dockerfile`
- Create: `docker/ingestion.Dockerfile`
- Create: `.github/workflows/ci.yml`
- Create: `src/specpilot/mcp_server/app.py`
- Create: `tests/smoke/test_fixture_pipeline.py`

**Interfaces:**
- Produces internal services `postgres`, `qdrant`, `mcp`, `api`, and one-shot `fixture-init`; W0 API/MCP are health/transport skeletons, not claimed L1/L2 completion.
- Consumes Tasks 1–8.

- [x] **Step 1: Write failing fixture smoke test**

Assert the synthetic fixture passes archive/OOXML inspection, manifest creation, policy projection, fake-provider reservation/send, and sanitized trace creation. Assert a malicious fixture triggers quarantine and a policy violation triggers a no-send Verifier-style event. Do not calculate or print any quality metric.

- [x] **Step 2: Run and verify RED**

Run: `python -m pytest tests/smoke/test_fixture_pipeline.py -q`

Expected: missing composed fixture path.

- [x] **Step 3: Implement minimal fixture pipeline and Compose health dependencies**

Keep PostgreSQL/Qdrant/MCP on an internal network with no host port mapping. Demo profile may publish API/web later; real profile defaults to localhost/private binding. The ingestion image runs unprivileged, `network_mode: none`, read-only root filesystem, read-only input mount, writable output/quarantine mounts, dropped capabilities, and bounded memory/PIDs.

- [x] **Step 4: Add CI jobs**

Run Ruff, mypy, unit tests, PostgreSQL integration tests, fixture smoke, and Docker image build. CI must not download BGE-M3, call a live model, require a key, access real source files, or display accuracy-like metrics.

- [x] **Step 5: Verify the complete W0 gate**

Run: `make check`

Run: `make test-integration`

Run: `docker compose --profile demo config`

Run: `docker compose build api mcp fixture-init`

Run: `docker compose --profile demo up --wait`

Run: `python -m pytest tests/smoke/test_fixture_pipeline.py -q`

Expected: all local verification exits 0; unsafe fixtures exist only in quarantine; no service logs secrets/source/payload text; internal services publish no host ports.

- [x] **Step 6: Commit**

```bash
git add compose.yaml docker .github/workflows/ci.yml tests/smoke Makefile
git commit -m "ci: add isolated w0 service and fixture checks"
```

### Task 10: W0 evidence review and route decision

**Files:**
- Modify: `docs/runbooks/w0-go-no-go.md`
- Create: `artifacts/public/w0-verification.json`
- Create: `docs/reports/w0-foundation-report.md`

**Interfaces:**
- Produces one explicit route decision: `A`, `B`, `C`, or `extend`; no undecided state may enter W1.
- Consumes fresh outputs from Tasks 1–9 plus the compliance/provider evidence from Task 8.

- [ ] **Step 1: Re-run every hard verification from a clean test state**

Capture command, timestamp, code/config hash, exit code, test counts, and sanitized artifact hashes. Do not copy source text, payload text, credentials, or unrestricted logs into the report.

- [ ] **Step 2: Check W0 requirements line by line**

Confirm safe archive/OOXML rejection, initial manifests, successor/default-deny behavior, multi-round/retry/overreach/concurrency/recovery accounting, maximum legal envelope, fixture provider route evidence, Compose/CI skeleton, and explicit compliance conclusion.

- [ ] **Step 3: Record the only valid next state**

- Route A only when both actual provider routes are fixture-smoked and separately bound authorized successor manifests exist for their uses.
- Route B only when cloud egress conclusion is no and the local structured-output/tool-calling/latency/cost smoke is evidenced.
- Route C only when cloud egress conclusion is no and target hardware cannot sustain B; create a new RFC-specific design/plan before W1.
- Otherwise record `extend` and do not start W1.

- [ ] **Step 4: Run verification-before-completion and commit the sanitized evidence**

Run: `make check && make test-integration && python -m pytest tests/smoke/test_fixture_pipeline.py -q`

Inspect: `git status --short`, `git diff --check`, and the W0 requirement checklist.

```bash
git add artifacts/public/w0-verification.json docs/reports/w0-foundation-report.md docs/runbooks/w0-go-no-go.md
git commit -m "docs: record specpilot w0 route decision"
```

---

## Plan self-review record

- **Spec coverage:** W0 order and acceptance points from product-plan §§3.2, 4.6.1, 5.2, 6.4, 9.4, 11, and 12.2 map to Tasks 2–10. Gold annotation, retrieval, Agents, full MCP tools, SSE, and UI are intentionally deferred to W1–W5.
- **Placeholder scan:** Every implementation step names concrete behavior, files, and verification. External provider availability is represented as an evidenced blocked state, not a fabricated result.
- **Type consistency:** Archive output feeds manifests; authorized manifests plus typed payloads feed the enforcer; the enforcer emits `ReservationRequest`; the PostgreSQL ledger returns `Reservation`; only `PolicyBoundTransport` receives that reservation and may invoke a private adapter.
- **Scope decision:** The seven-week product is split into weekly plans. This document covers W0 only and points to the master roadmap for later weeks.
