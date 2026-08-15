# W5 Streaming, Offline Demo, and Evaluation Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver owner-scoped resumable SSE, four complete offline demo paths, manifest-scoped fixture/real initialization, an optional local provider response cache, and an author-confirmed evaluation-freeze workflow.

**Architecture:** Extend the existing durable run-event ledger instead of adding a second trace store. Ship W5 as vertical slices through PostgreSQL, FastAPI, React, Compose, provider transport, and canonical artifact stores; preserve the snapshot endpoint as a fallback and keep every mutable or author-owned action explicit.

**Tech Stack:** Python 3.12+, Pydantic v2, psycopg 3, FastAPI/Starlette, PostgreSQL 17, Qdrant 1.12.4, React 19, TypeScript 5.9, Vite 7, Vitest, Playwright, Docker Compose.

## Global Constraints

- Preserve every invariant in `AGENTS.md`, especially fail-closed behavior, no source prose in committable records, and the enforcer as the only outward provider path.
- Never place a bearer token in a URL; bearer SSE uses `fetch` with the `Authorization` header and cookie mode uses `credentials: "same-origin"`.
- SSE, snapshots, logs, ready markers, cache audits, and evaluation artifacts contain no question, answer, claim, rationale, excerpt, or provider response.
- Event sequence is `1..10000`; an SSE cursor is canonical decimal `0..10000`.
- Fixture output proves engineering only and never emits a quality metric.
- Real-provider calls, compliance conclusions, scoring-route approval, and final freeze confirmation belong to `chunxue`.
- W6 is the first path permitted to execute locked evaluation cases.
- Startup never applies migrations, initializes a corpus, cleans retained data, rebinds policy, or freezes evaluation artifacts.

---

### Task 1: Synchronize the Current Project State

**Files:**
- Modify: `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`
- Modify: `docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md`
- Modify: `SpecPilot_项目方案.md`
- Create: `docs/handoff/2026-08-15-codex-handoff.md`
- Create: `tests/cli/test_documented_progress.py`

**Interfaces:**
- Consumes: aggregate annotation progress and current Git history.
- Produces: one dated, noncontradictory W5 starting snapshot.

- [ ] **Step 1: Recompute the source facts**

Run:

```bash
.venv/bin/python -m specpilot.cli annotation progress \
  --annotation-dir artifacts/restricted/annotations \
  --review-dir artifacts/restricted/reviews \
  --deep-review-dir artifacts/restricted/deep-reviews \
  --deep-review-rate 0.25 --deep-review-salt r1-2026-08 \
  --pool-dir artifacts/restricted/pooling
```

Expected: L1 40, L2 20, both awaiting 0, deep review 12/12, pooling sealed.

- [ ] **Step 2: Write the documentation regression test**

Assert all four documents carry L1 `40/40`, L2 `20/20`, deep review `12/12`, W4 fixture-only limitations, and no active claim that a mistyped pooling choice ends the pass.

- [ ] **Step 3: Run RED**

Run: `.venv/bin/python -m pytest tests/cli/test_documented_progress.py -q`

Expected: FAIL against stale 2026-08-13/current-open text.

- [ ] **Step 4: Add dated superseding blocks**

Preserve historical snapshots. Distinguish the current quick gate (1537 unit, 187 CLI) from dated 2026-08-14 full-service evidence (1998 passed, zero skipped at `b89339d`).

- [ ] **Step 5: Verify and commit**

Run the focused test and `git diff --check`. Commit:

```bash
git add SpecPilot_项目方案.md docs/roadmaps docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md docs/handoff tests/cli/test_documented_progress.py
git commit -m "docs: synchronize the W5 starting state"
```

### Task 2: Add Owner-Scoped Incremental Event Reads

**Files:**
- Modify: `src/specpilot/runs/contracts.py`
- Modify: `src/specpilot/runs/postgres.py`
- Modify: `src/specpilot/api/dependencies.py`
- Create: `tests/unit/runs/test_run_contracts.py`
- Modify: `tests/integration/runs/test_postgres_store.py`

**Interfaces:**
- Produces: `RunEventPage(events: tuple[RunEvent, ...], terminal: bool, last_sequence: int)`.
- Produces: `ApiRunStore.read_events_owned(run_id, session_id, *, after_sequence, limit) -> RunEventPage | None`.

- [ ] **Step 1: Write contract RED tests**

Valid example:

```python
page = RunEventPage(
    events=(StateTransitionEvent(
        sequence=1,
        previous_status=None,
        status=RunStatus.QUEUED,
        reason=None,
    ),),
    terminal=False,
    last_sequence=1,
)
```

Reject out-of-range sequence, more than 256 events, non-increasing events, final-event mismatch, and `terminal=True` without a terminal event.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/unit/runs/test_run_contracts.py -q`

Expected: FAIL because `RunEventPage` is absent.

- [ ] **Step 3: Implement the closed page contract and protocol**

Use a frozen extra-forbidding Pydantic model. Empty pages retain the requested cursor as `last_sequence`.

- [ ] **Step 4: Write PostgreSQL RED tests**

Cover cursor 0, second page, empty nonterminal page, terminal page, foreign/unknown equality, limit bounds 1..256, future cursor refusal, and corrupted row refusal.

- [ ] **Step 5: Implement one-snapshot incremental reads**

Within one repeatable-read transaction, authorize owner, read status/max sequence, reject a future cursor, then select `sequence > after_sequence ORDER BY sequence LIMIT limit`. Decode every row through `_event_from_row`.

- [ ] **Step 6: Verify and commit**

Run focused unit/integration tests, Ruff, and mypy. Commit `feat: read owner-scoped run events incrementally`.

### Task 3: Serve Bounded Resumable SSE

**Files:**
- Create: `src/specpilot/api/sse.py`
- Modify: `src/specpilot/api/app.py`
- Create: `tests/unit/api/test_sse.py`
- Modify: `tests/integration/api/test_run_ownership.py`

**Interfaces:**
- Produces: `encode_event(event: RunEvent) -> bytes`.
- Produces: `parse_last_event_id(value: str | None) -> int`.
- Produces: `stream_owned_events(store, run_id, session_id, *, after_sequence, config) -> AsyncIterator[bytes]`.
- HTTP: `GET /runs/{run_id}/events`, media type `text/event-stream`.

- [ ] **Step 1: Write encoder/cursor RED tests**

Assert compact frames with `id`, `event`, one `data` line, and a blank terminator. Reject `+1`, `01`, whitespace, negative, nonnumeric, and values above 10000.

- [ ] **Step 2: Implement pure helpers**

Heartbeat is exactly `: keep-alive\n\n` and never carries event data.

- [ ] **Step 3: Write lifecycle RED tests**

Use a fake store and controlled clock for initial replay, resumed batches, one heartbeat per idle interval, terminal close, cancellation cleanup, page cap, and sanitized store failure.

- [ ] **Step 4: Implement bounded polling**

Add `RunEventStreamConfig(heartbeat_seconds=10, poll_seconds=0.25, max_connection_seconds=60, page_size=256)`. Buffer no more than one page and never mutate the run during cancellation.

- [ ] **Step 5: Add the authenticated route**

Read cursor only from `Last-Event-ID`. Do an owner read before returning `StreamingResponse` so unknown and foreign runs share `404 run_not_found`. Set `Cache-Control: no-store` and `X-Accel-Buffering: no`.

- [ ] **Step 6: Add integration coverage**

Prove bearer/cookie auth, no query token, equivalent 404, malformed cursor 422, resume, terminal close, and absence of submitted question/excerpt in stream bytes.

- [ ] **Step 7: Verify and commit**

Run focused Python tests, Ruff, and mypy. Commit `feat: stream owner-scoped run events over SSE`.

### Task 4: Consume SSE with Snapshot Fallback

**Files:**
- Modify: `web/trace/src/api.ts`
- Create: `web/trace/src/sse.ts`
- Create: `web/trace/src/useRunStream.ts`
- Modify: `web/trace/src/App.tsx`
- Modify: `web/trace/src/components/StatusPanel.tsx`
- Create: `web/trace/src/sse.test.ts`
- Create: `web/trace/src/useRunStream.test.tsx`
- Modify: `web/trace/src/App.test.tsx`

**Interfaces:**
- Produces: `streamRunEvents(runId, options) -> AsyncIterable<RunEvent>`.
- Produces: `useRunStream(options) -> RunPollingResult`; the existing UI keeps the same view/refresh boundary.
- Consumes: SSE first and existing `getRun` only for initial/manual snapshots.

- [ ] **Step 1: Bring the TypeScript decoder to Python parity**

Support L1/L2, all W4 events and agent names, current status invariants, and stable reasons. Generate parity fixtures from Python model JSON.

- [ ] **Step 2: Write parser RED tests**

Cover chunks split inside UTF-8 and CRLF, multiple frames, comments, canonical IDs, identical duplicate suppression, sequence gap/decrease/conflict, unknown fields, oversized frames, and abort cleanup.

- [ ] **Step 3: Implement `sse.ts`**

Use `fetch` with same-origin credentials. Bearer exists only in `Authorization`; resume cursor exists only in `Last-Event-ID`. Use one bounded `TextDecoder` buffer and the closed decoder.

- [ ] **Step 4: Write hook RED tests**

Use fake timers for backoff 250/500/1000/2000/4000 ms, 60-second deadline, cursor reuse, terminal stop, unmount abort, last-view preservation, and manual snapshot fallback.

- [ ] **Step 5: Implement and wire the hook**

Never poll while the stream is healthy. On exhaustion or invalid data retain the last valid view, expose `stream_unavailable`, and keep Refresh.

- [ ] **Step 6: Verify and commit**

Run `npm --prefix web/trace test -- --run` and `npm --prefix web/trace run build`. Inspect the bundle for `?token=`. Commit `feat: resume trace streams with snapshot fallback`.

### Task 5: Register Four Offline Demo Scenarios

**Files:**
- Create: `src/specpilot/demo/__init__.py`
- Create: `src/specpilot/demo/scenarios.py`
- Modify: `src/specpilot/api/contracts.py`
- Modify: `src/specpilot/api/app.py`
- Modify: `src/specpilot/api/runtime.py`
- Modify: `src/specpilot/providers/fake.py`
- Modify: `src/specpilot/runs/contracts.py`
- Modify: `web/trace/src/api.ts`
- Modify: `web/trace/src/App.tsx`
- Create: `web/trace/src/components/ScenarioPicker.tsx`
- Create: `tests/unit/demo/test_scenarios.py`
- Modify: `tests/integration/api/test_l1_end_to_end.py`
- Modify: `tests/integration/api/test_l2_end_to_end.py`
- Modify: `tests/browser/test_trace_page.py`

**Interfaces:**
- Produces: exact IDs `l1_answered`, `l2_answered`, `evidence_refused`, `verifier_recovered`.
- Produces: optional closed `ChatRequest.scenario_id` and stable reason `unsupported_demo_case`.
- Consumes: existing L1/L2 job factories and the private fake adapter.

- [ ] **Step 1: Write registry RED tests**

Assert exactly four unique IDs, public labels, task levels, script versions, terminal states, and required sanitized event kinds. Public metadata contains no fixture question or reply.

- [ ] **Step 2: Implement immutable registry**

Keep fixture questions/scripts private. Export only ID, label, description, task level, and engineering limitation.

- [ ] **Step 3: Add request/profile RED tests**

Fixture accepts registered IDs. Real rejects every scenario ID with `invalid_demo_scenario`. Custom fixture input omits the ID.

- [ ] **Step 4: Implement scenario-bound scripts**

Select scripts inside the runtime job builder, never from client output. `verifier_recovered` fails the first gate, consumes exactly one recovery, then reaches its registered terminal state. Unregistered fixture input ends `unsupported_demo_case`.

- [ ] **Step 5: Add fixture-only UI controls**

Bootstrap page metadata includes profile. Fixture shows scenario selector plus custom input; real shows ordinary input only. Selected scenario controls task level and public scenario ID.

- [ ] **Step 6: Add five browser paths**

Run four registered scenarios and one unsupported custom question. Assert terminal state, required tool/egress/verifier/recovery events, SSE use, and no sensitive prose in DOM/responses.

- [ ] **Step 7: Verify and commit**

Run focused Python, frontend, and Playwright tests. Commit `feat: run four registered offline demo scenarios`.

### Task 6: Make Initialization Manifest-Scoped

**Files:**
- Create: `src/specpilot/deployment/__init__.py`
- Create: `src/specpilot/deployment/ready.py`
- Create: `src/specpilot/deployment/initialize.py`
- Modify: `src/specpilot/cli.py`
- Modify: `src/specpilot/api/runtime.py`
- Modify: `src/specpilot/mcp_server/runtime.py`
- Modify: `compose.yaml`
- Modify: `compose.demo.yaml`
- Create: `compose.real.yaml`
- Modify: `Makefile`
- Create: `fixtures/demo/source.xml`
- Create: `fixtures/demo/dense-points.jsonl`
- Create: `fixtures/demo/fixture-manifest.json`
- Create: `tests/unit/deployment/test_ready.py`
- Create: `tests/cli/test_corpus_initialize.py`
- Create: `tests/integration/qdrant/test_corpus_initialize.py`
- Modify: `tests/unit/test_compose_exposure.py`

**Interfaces:**
- Produces: closed `ReadyMarker`, `initialize_fixture(request)`, `initialize_real(request)`.
- Produces: CLI `corpus init-fixture`, `corpus init-real`, and `make ingest-real CORPUS_DIR=/absolute/path`.
- Consumes: secure RFC loader, `freeze_corpus`, `verify_corpus`, manifest store, and dense inventory checks.

- [ ] **Step 1: Write ready-store RED tests**

Bind schema/source/corpus/collection/point count/inventory/mode. Test 0700 root, atomic create, identical idempotency, mismatched rewrite refusal, symlink refusal, and canonical ID.

- [ ] **Step 2: Implement the secure marker store**

Reuse `SecureRecordDirectory`; omit host paths.

- [ ] **Step 3: Commit deterministic fixture artifacts**

Create synthetic RFC-shaped XML exercising all five MCP tools and fixed 1024-dimensional vectors. The manifest records exact hashes/counts.

- [ ] **Step 4: Write fixture-init integration RED tests**

Cover empty creation, identical rerun without upsert, wrong schema, same-size wrong inventory, partial collection, stale marker, and frozen immutability.

- [ ] **Step 5: Implement fixture init**

Validate committed hashes before Qdrant. Create only the derived collection, verify through existing inventory code, then atomically write ready state.

- [ ] **Step 6: Implement real init test-first**

Require absolute `CORPUS_DIR`; accept only secure/frozen artifacts; use existing freeze/verify; refuse in-place mutation; write `mode=real`.

- [ ] **Step 7: Gate health and Compose**

API/MCP require exact marker identity. Replace placeholder `fixture-init`, mount ready state read-only, preserve no ports in base/real, and loopback-only demo exposure.

- [ ] **Step 8: Verify and commit**

Run focused unit/CLI/Qdrant tests plus `docker compose config` for demo/real. Commit `feat: initialize manifest-scoped demo and real corpora`.

### Task 7: Add Optional Local Provider Response Caching

**Files:**
- Create: `src/specpilot/providers/cache.py`
- Modify: `src/specpilot/providers/transport.py`
- Modify: `src/specpilot/egress/enforcer.py`
- Modify: `src/specpilot/runs/contracts.py`
- Modify: `src/specpilot/answer/run.py`
- Modify: `src/specpilot/agents/planner.py`
- Modify: `src/specpilot/agents/compliance.py`
- Modify: `src/specpilot/verifier/semantic.py`
- Modify: `src/specpilot/runtime/worker.py`
- Modify: `src/specpilot/runtime/l2.py`
- Create: `migrations/016_w5_cache_trace.sql`
- Modify: `src/specpilot/api/runtime.py`
- Modify: `src/specpilot/cli.py`
- Create: `tests/unit/providers/test_response_cache.py`
- Modify: `tests/integration/providers/test_transport_ledger_flow.py`
- Modify: `tests/integration/runs/test_migration.py`
- Create: `tests/cli/test_cache_retention.py`

**Interfaces:**
- Produces: `CacheNamespace`, `CacheKey`, `CachedProviderResponse`, and `LocalResponseCache.get/put/delete_run/delete_session/delete_expired`.
- Modifies: `PolicyBoundTransport(..., cache: LocalResponseCache | None = None, cache_namespace: CacheNamespace | None = None)`.
- Produces: closed `CacheSummaryEvent(hit, stage, request_hash, record_hash)`.

- [ ] **Step 1: Write filesystem/key RED tests**

Key changes for provider/model, stage, prompt/config, source/corpus, policy, and payload. Test 0700 root, 0600 atomic files, no symlink following, TTL, corruption, and hashed run/session deletion indexes.

- [ ] **Step 2: Implement `cache.py`**

Persist `ProviderResponse` only inside ignored controlled storage. Errors expose stable codes, not path/content.

- [ ] **Step 3: Write transport-order RED tests**

Every call resolves adapter and executes `enforcer.prepare` before lookup. Hit: no ledger reservation and no adapter call. Miss: prepare, reserve, one send, attempt record, cache put. Cache fault fails closed.

- [ ] **Step 4: Implement transport caching and propagate cache identity**

Expose read-only `EgressPolicyEnforcer.policy_hash`. Build key from validated reservation primitives and namespace. Extend `TransportReceipt` with `cache_hit` and optional cache record hash; reservation ID is absent only for a hit.

Update planner, L1 answer, Compliance, semantic Verifier, worker audit, and L2
checkpoint assembly together: cached results carry no fabricated reservation,
are excluded from checkpoint `reservation_ids`, and emit one `CacheSummaryEvent`
instead of an admitted `EgressSummaryEvent`. Existing miss/replay behavior and
provider-error accounting remain byte-for-byte compatible.

- [ ] **Step 5: Add migration 016**

Add `cache_summary` to database kind/payload validators. Its exact keys are `kind`, `sequence`, `hit`, `stage`, `request_hash`, `record_hash`. Test fresh 001..016 and upgrade 015->016.

- [ ] **Step 6: Wire configuration and retention CLI**

Real defaults disabled. Enable only with explicit directory and positive TTL. Add `cache delete-run`, `delete-session`, and `delete-expired`; output counts only.

- [ ] **Step 7: Verify and commit**

Run cache/transport/migration/CLI tests, Ruff, and mypy. Commit `feat: cache provider responses without bypassing policy`.

### Task 8: Generate and Author-Confirm Evaluation Run Specs

**Files:**
- Create: `src/specpilot/contracts/evaluation.py`
- Create: `src/specpilot/evaluation/freeze.py`
- Modify: `src/specpilot/cli.py`
- Create: `tests/helpers/evaluation_factory.py`
- Create: `tests/unit/evaluation/test_freeze.py`
- Create: `tests/cli/test_evaluation_freeze.py`
- Create: `docs/runbooks/evaluation-freeze.md`

**Interfaces:**
- Produces: `EvaluationRunSpecCandidate`, `EvaluationRunSpec`, `EvaluationFreezeReport`.
- Produces: `build_candidate(inputs)` and `finalize_candidate(candidate_path, *, expected_hash, author_id, confirmed)`.
- Produces: CLI `evaluation freeze-candidate` and `evaluation freeze-confirm`.

- [ ] **Step 1: Write contract RED tests**

Require hashes for code/dependencies/source/corpus/collection/sets/scripts/prompts/config/policy/provider/models/scoring/environment. Recursively reject keys `question`, `claim`, `excerpt`, `answer`, and `rationale`.

- [ ] **Step 2: Implement canonical contracts**

Use existing canonical hashing/content-addressed records. Candidate and final spec differ only by confirmation metadata and contain no locked result.

- [ ] **Step 3: Write candidate-validation RED tests**

Cover incomplete L1/L2, missing deep read, unsealed pooling, overlapping L2-adv IDs/families, missing identities, absent dev scoring evidence, dirty tree, and a passing fixture. Failures write nothing.

- [ ] **Step 4: Implement candidate generation**

Read aggregate/status artifacts only; hash Git commit/tree and dependency lock; atomically write candidate; print path/hash/counts only.

- [ ] **Step 5: Write and implement confirmation**

Require exact candidate hash, author `chunxue`, and `--confirm-freeze`. Reject changed bytes, another author, missing flag, dirty tree, or evaluation execution. Identical retry returns `unchanged`.

- [ ] **Step 6: Document author handoff**

Runbook includes exact confirmation command and successor rule. Automated execution stops before confirmation and never reads locked output.

- [ ] **Step 7: Verify and commit**

Run evaluation unit/CLI tests, Ruff, mypy, and fixture candidate dry run. Commit `feat: freeze evaluation specs with author confirmation`.

### Task 9: Close the Packaged W5 Gate

**Files:**
- Modify: `Makefile`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`
- Modify: `SpecPilot_项目方案.md`
- Create: `docs/reports/w5-streaming-demo-and-freeze.md`
- Modify: `tests/smoke/test_fixture_pipeline.py`
- Modify: `tests/browser/test_trace_page.py`
- Modify: `pyproject.toml` only if packaged asset declarations change.

**Interfaces:**
- Produces: `make w5-check` and a dated evidence report.
- Consumes: all prior W5 slices.

- [ ] **Step 1: Add a hard gate**

Run lint, mypy, unit, CLI, frontend test/build, Compose config, packaging, browser, fresh DB integration, Qdrant integration, and four-scenario smoke. Required services fail rather than skip.

- [ ] **Step 2: Extend CI**

Use PostgreSQL/Qdrant fixture services, committed vectors, and fake provider only. Require zero service skips and build both images. Inspect base/real exposure.

- [ ] **Step 3: Run fast verification**

```bash
make check
make frontend-test
make frontend-build
git diff --check
```

- [ ] **Step 4: Run fresh-service verification**

Create one explicitly named scratch PostgreSQL database, set both service variables, run the whole tree with `--import-mode=importlib -q -rs`, require zero skips, then drop only that scratch database.

- [ ] **Step 5: Run packaged demo**

Build wheel/images, start explicit demo Compose, execute all four scenarios over SSE, repeat fixture-init, and record cold-build/warm-start samples with environment and n. Report no quality metric.

- [ ] **Step 6: Update evidence and limitations**

Record exact commit, commands, counts, services, hashes, timings, and open live-provider/author-freeze/W6 boundaries.

- [ ] **Step 7: Final verification and commit**

Re-run `make w5-check`, `git diff --check`, and `git status --short`. Commit `docs: record the verified W5 engineering package`. Do not push or open a pull request without a separate request.
