# W4 Compliance, Verifier, and recovery engineering evidence

Date: 2026-08-14
Code tested: `b89339d2c9118fc3e5915735151e18eb3a504059`
Scope: fixture-only engineering and service integration evidence; this is not a
quality, calibration, or release-evaluation report.

## What the fixture paths exercised

- L2 happy path: `POST /chat`, owner-scoped trace, planning → Compliance →
  Verifier egress order, and a terminal answer.
- A deterministic citation mismatch: no semantic send before the one directed
  recovery; the recovered candidate passes both gates.
- Two semantic `exception_missing` decisions: two separately recorded Verifier
  receipts, exactly one recovery, and an `insufficient_evidence` result after
  the second failure.
- Sanitized tool and deterministic-verifier trace summaries, in execution
  order, with bounded opaque evidence IDs and no query, claim, excerpt or
  rationale prose.
- Real-time, lease-fenced audit batches: every provider reservation has exactly
  one egress trace, checkpoint versions are emitted only by the atomic
  checkpoint writer, and stable opaque claim/phase identities preserve
  legitimate identical verifier facts while making exact retries idempotent.
- Client resume reconciles all settled ledger receipts strictly bound to the
  run/root/policy/runtime identities, including a provider response lost before
  its audit or checkpoint reservation update.
- Recovery reserves its run-scoped attempt and maximum bounded tool cost before
  MCP execution, bound to one opaque claim ID. A tool result lost before
  `recovery_completed` cannot trigger a second MCP call or attach to a reordered
  pending candidate; resume closes only that claim as `recovery_result_lost`
  without resetting the eight-call cap.
- Owner-assisted process loss at every nonterminal checkpoint stage (`planned`,
  `evidence_collected`, `candidate_built`, `deterministic_verified`,
  `recovery_reserved`, `recovery_completed`, and `semantic_verified`),
  including repeated client resume/idempotency, same/different resume keys,
  queue-delivery failure,
  locally rebuilt work, and lost provider-result generations.
- Persistence sentinel checks covering run, event, checkpoint, attempt and
  egress records. The question and excerpt sentinel prose are absent from all
  checked durable records.
- Completed-checkpoint compaction and operator-driven TTL cleanup: sanitized
  terminal metadata remains, active reconstruction detail is cleared, only an
  old inactive eligible checkpoint is deleted, and running/retained rows remain.
- L1 integration and migration regressions, including closed W4 trace shapes,
  checkpoint CAS, and resumed attempt closure.
- Attempt-lineage fencing: a first checkpoint write is only `planned` attempt
  1; every resume compares its persisted checkpoint attempt with the locked
  maximum ledger attempt before an acquisition. A mismatch fails closed with no
  new run, checkpoint, attempt or event mutation; valid attempt 1 → 2 and
  same-key replay remain intact.

The fixture adapter is `FakeProvider`; no live model or provider route was
enabled. Deterministic checks are implementation facts. Semantic support is a
model judgement represented by a fixture response, so these tests do not claim
accuracy, recall, calibration, latency, or production quality.

## Fresh-service environment

- PostgreSQL: isolated container `postgres:17-alpine`, server `17.10`, exposed
  only at `127.0.0.1:55432`.
- Qdrant: isolated container `qdrant/qdrant:v1.12.4`, exposed only at
  `127.0.0.1:6334`.
- Frozen collection:
  `specpilot_ff4841e2d846388014efa06870fbbdb7`; service inspection reported
  1,922 points, vector size 1,024, cosine distance and green status.
- PostgreSQL test database:
  `specpilot_w4_r9_attempt_lineage_20260814`, newly created for this command and dropped
  after it completed. It was never a shared or hand-migrated database.

The test worktree needed the local restricted RFC fixture for two pre-existing
corpus invariants. The files were ignored, same-inode hard links to the
user-owned source files; a symlink was rejected by the intentional
`O_NOFOLLOW` ingestion guard. Neither form was staged or committed.

## Commands and measured results

```bash
PYTHONPATH="$PWD:$PWD/src" SPECPILOT_PYTHON=.venv/bin/python make check
# ruff: all checks passed
# mypy: Success: no issues found in 105 source files
# unit: 1534 passed in 4.55s
# CLI: 181 passed in 1.61s
```

```bash
curl --fail --silent \
  http://127.0.0.1:6334/
# HTTP 200; Qdrant 1.12.4, commit 5b578c4f34188f0474f901e49d4726213596433d

curl --fail --silent \
  http://127.0.0.1:6334/collections/specpilot_ff4841e2d846388014efa06870fbbdb7
# HTTP 200; frozen collection metadata recorded above

PGPASSWORD='specpilot-w4-test-only' psql -h 127.0.0.1 -p 55432 \
  -U specpilot -d postgres -Atc 'select version();'
# PostgreSQL 17.10 on aarch64-unknown-linux-musl

PGPASSWORD='specpilot-w4-test-only' createdb -h 127.0.0.1 -p 55432 \
  -U specpilot specpilot_w4_r9_attempt_lineage_20260814
SPECPILOT_TEST_DSN='postgresql://specpilot:specpilot-w4-test-only@127.0.0.1:55432/specpilot_w4_r9_attempt_lineage_20260814' \
SPECPILOT_TEST_QDRANT_URL='http://127.0.0.1:6334' \
PYTHONPATH="$PWD:$PWD/src" \
  .venv/bin/python -m pytest --import-mode=importlib -q -rs
# unified execution session exit code: 0
# 1998 passed in 31.92s; 0 skipped
PGPASSWORD='specpilot-w4-test-only' dropdb -h 127.0.0.1 -p 55432 \
  -U specpilot specpilot_w4_r9_attempt_lineage_20260814
```

`--import-mode=importlib` is required for the whole tree because the existing
non-package integration directories contain two distinct
`test_postgres_store.py` files. It prevents pytest's module-name collision; it
does not suppress collection or test execution. All migrations 001--014 were
applied by the fresh-database fixture in filename order.

```bash
git diff --check
git diff --check 1017c1668da7e6cb9a83dd6107be6a84b052f566..HEAD
# no whitespace errors
```

## Operational boundaries

The checkpoint retains only reconstruction-safe metadata: immutable bindings,
hashes/opaque evidence IDs, stage/budget metadata, reservations and explicit
generation keys. It does not retain question, claim/design, rationale, query,
excerpt or provider-response prose. Completed checkpoints can be compacted. Old
noncompleted checkpoints are deleted only when an operator invokes the supplied
retention operation and the owning run is neither queued nor running; completed
and active state is retained. There is no automatic cleanup daemon.

Resume is client/owner assisted: the same question must hash to the stored
query hash, and root, bindings, reservation state and a new lease must all
validate. The persisted checkpoint attempt must match the locked maximum
attempt-ledger row before a fresh acquisition; any disagreement fails closed
without new durable records. It keeps the same egress budget. Reconstructible local stages are
rebuilt without a provider resend; a lost provider result advances its explicit
generation and creates another charged transmission under the original caps.

Still open: L2 development-set calibration and reported numbers, live-provider
acceptance, SSE/reconnect, the full demo/profile matrix, and locked evaluation.
