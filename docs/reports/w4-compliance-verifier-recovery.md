# W4 Compliance, Verifier, and recovery engineering evidence

Date: 2026-08-14  
Code tested: `062c0bbb82b39f9c19789ea9fc9945d7c0d31c38`  
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
- Owner-assisted process loss at every nonterminal checkpoint stage (`planned`,
  `evidence_collected`, `candidate_built`, `deterministic_verified`,
  `recovery_completed`, and `semantic_verified`), including repeated client
  resume/idempotency, same/different resume keys, queue-delivery failure,
  locally rebuilt work, and lost provider-result generations.
- Persistence sentinel checks covering run, event, checkpoint, attempt and
  egress records. The question and excerpt sentinel prose are absent from all
  checked durable records.
- L1 integration and migration regressions, including closed W4 trace shapes,
  checkpoint CAS, and resumed attempt closure.

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
  `specpilot_w4_final_20260814`, newly created for this command and dropped
  after it completed. It was never a shared or hand-migrated database.

The test worktree needed the local restricted RFC fixture for two pre-existing
corpus invariants. The files were ignored, same-inode hard links to the
user-owned source files; a symlink was rejected by the intentional
`O_NOFOLLOW` ingestion guard. Neither form was staged or committed.

## Commands and measured results

```bash
PYTHONPATH=src make check
# ruff: all checks passed
# mypy: Success: no issues found in 105 source files
# unit: 1523 passed in 4.00s
# CLI: 181 passed in 1.58s
```

```bash
curl --fail --silent \
  http://127.0.0.1:6334/collections/specpilot_ff4841e2d846388014efa06870fbbdb7
# HTTP 200; frozen collection metadata recorded above

docker exec specpilot-w4-postgres-20260814 createdb -U specpilot \
  specpilot_w4_final_20260814
SPECPILOT_TEST_DSN='postgresql://specpilot:specpilot-w4-test-only@127.0.0.1:55432/specpilot_w4_final_20260814' \
SPECPILOT_TEST_QDRANT_URL='http://127.0.0.1:6334' \
PYTHONPATH=src .venv/bin/python -m pytest --import-mode=importlib -q -rs
docker exec specpilot-w4-postgres-20260814 dropdb -U specpilot \
  specpilot_w4_final_20260814
# 1978 passed in 32.31s; 0 skipped
```

`--import-mode=importlib` is required for the whole tree because the existing
non-package integration directories contain two distinct
`test_postgres_store.py` files. It prevents pytest's module-name collision; it
does not suppress collection or test execution. All migrations 001--012 were
applied by the fresh-database fixture in filename order.

```bash
git diff --check
# leakage-marker search over migrations, source and product documentation
# no whitespace errors; no marker occurs in product code or product docs
```

## Operational boundaries

The checkpoint retains only reconstruction-safe metadata: immutable bindings,
hashes/opaque evidence IDs, stage/budget metadata, reservations and explicit
generation keys. It does not retain question, claim/design, rationale, query,
excerpt or provider-response prose. Completed checkpoints can be compacted;
noncompleted checkpoints are deleted only when an operator invokes the supplied
seven-day retention operation. There is no automatic cleanup daemon.

Resume is client/owner assisted: the same question must hash to the stored
query hash, and root, bindings, reservation state and a new lease must all
validate. It keeps the same egress budget. Reconstructible local stages are
rebuilt without a provider resend; a lost provider result advances its explicit
generation and creates another charged transmission under the original caps.

Still open: L2 development-set calibration and reported numbers, live-provider
acceptance, SSE/reconnect, the full demo/profile matrix, and locked evaluation.
