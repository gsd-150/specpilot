# Task 1 Report: Migration 005 and typed trace contracts

## Status

DONE

## What was implemented

- Added transactional migration `005_run_trace.sql` after migrations 001--004.
- Added normalized owner-bound `specpilot_run` storage with query hash only,
  exact status checks, policy/corpus foreign keys, and timestamp/lease
  invariants that support queued/running lease-expiry projection as
  `interrupted` without adding reconciliation writer logic.
- Added append-only `specpilot_run_event` storage keyed by
  `(run_id, sequence)`, with an exact event-kind check and per-kind closed JSONB
  top-level payload keys.
- Added frozen, extra-forbidding Pydantic contracts for `RunStatus`,
  `TerminalReason`, `RunRecord`, `RunView`, and an 11-kind discriminated
  `RunEvent` union.
- Kept `AnswerOutcome.provider_error`, verifier verdict, and refusal reason as
  independent typed fields.  `answered` carries no invented failure reason;
  non-success terminal states retain stable reasons including the three named
  egress admission failures.
- Added run tables to integration cleanup before all egress tables.
- Added independently failing plaintext/disguised-extra cases, bounded metadata
  tests, exact PostgreSQL metadata tests, upgrade preservation, FK/PK/order,
  JSONB metadata agreement, and timestamp/lease tests.

## TDD evidence

### RED: missing contracts and migration

Command:

```text
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_api_test .venv/bin/python -m pytest tests/unit/runs/test_contracts.py tests/integration/runs/test_migration.py -q
```

Result before production files existed:

```text
28 failed, 2 errors in 0.44s
```

The expected failures were `ModuleNotFoundError: No module named
'specpilot.runs'`, missing `005_run_trace.sql`, and absent
`specpilot_run`/`specpilot_run_event` relations.  The command was run with
sandbox escalation solely to permit localhost access to the dedicated test
database.

### RED: successful answers do not invent a failure reason

Unit RED:

```text
FAILED test_answered_terminal_event_has_no_failure_or_refusal_reason
ValidationError: reason must be a valid string
```

Fresh PostgreSQL RED:

```text
FAILED test_answered_row_requires_completion_but_not_a_failure_reason
CheckViolation: specpilot_run_state_metadata_check
```

These failures showed that the first minimal contract incorrectly required a
reason for successful `answered` outcomes.  The contract and SQL were then
narrowed so only non-success terminal states require a stable reason.

### GREEN: fresh migration 001--005 and contracts

The database was dropped and recreated by its exact dedicated name before the
final run.  Command:

```text
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_api_test .venv/bin/python -m pytest tests/unit/runs/test_contracts.py tests/integration/runs/test_migration.py -q
```

Result:

```text
32 passed in 0.64s
```

An intermediate GREEN attempt produced `28 passed, 2 failed`; both failures
were caused by the test helper omitting migration 003's required
`corpus_ledger_id` and head row.  The helper was corrected to mirror the real
schema; no production constraint was loosened for that failure.

## Verification

- `.venv/bin/python -m ruff check .`: passed.
- `.venv/bin/python -m mypy src`: `Success: no issues found in 84 source files`.
- `make check`: `1159 passed, 2 skipped`; the two skips are pre-existing unit
  dependency skips reported by the repository's standard target.
- `git diff --check`: passed.
- Safety scan hits were reviewed: production hits are the required
  `query_hash`, sanitized candidate IDs/scores, enum names, and explanatory
  comments.  No query/excerpt/candidate body/provider response/credential/
  secret/local path field or column was introduced.

## Files changed

- `migrations/005_run_trace.sql`
- `src/specpilot/runs/__init__.py`
- `src/specpilot/runs/contracts.py`
- `tests/conftest.py`
- `tests/unit/runs/test_contracts.py`
- `tests/integration/runs/test_migration.py`
- `.superpowers/sdd/2026-08-11-w3-2-api-runs/task-1-report.md`

## Self-review

- Scope is limited to migration/contracts/tests/cleanup/report; no store,
  worker, reconciliation writer, or endpoint was implemented.
- The schema supports owner/session binding at creation and read-contract level.
- The event union has no generic dictionary escape hatch, and all nested
  summary models are frozen and extra-forbidding.
- Provider failure is not derived from verifier verdict anywhere in this task.
- No real provider call was made and no database other than
  `specpilot_w3_api_test` was touched.

## Concerns

None.

## Review fix round 1

Review found that the first SQL CHECK closed only the event payload's top
level.  Raw inserts could still place arbitrary fields inside candidates,
evidence, or verifier checks, and SQL did not enforce the Pydantic scalar,
enum, and bound contracts.  Review also found that normalized score bounds did
not fit real BM25/hybrid retrieval, and `RunView` did not enforce temporal or
event-order invariants.

### Fix-round RED

Unit command:

```text
.venv/bin/python -m pytest tests/unit/runs/test_contracts.py -q
```

Result before the fix:

```text
6 failed, 30 passed in 0.13s
```

The six expected failures covered a raw score above one, answered/failed views
without completion, queued with completion, and out-of-order/duplicate event
sequences.

Fresh raw PostgreSQL command:

```text
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_api_test \
  .venv/bin/python -m pytest tests/integration/runs/test_migration.py -q
```

Result before the fix:

```text
36 failed, 7 passed in 2.73s
```

These were independent raw insert failures that the old SQL accepted: every
combination of three nested containers and seven prohibited field classes,
plus representative malformed containers, objects, enums, bounds, and scalar
types.

A later compatibility RED proved uppercase UUID text accepted by Pydantic was
rejected by SQL.  It failed 1 of 21 focused cases and led to a case-insensitive
UUID format check.

### Fix-round implementation

- Replaced the shallow JSONB CHECK body with immutable, type-safe SQL validator
  functions called by the table CHECK.  They inspect type before any cast, close
  nested object keys, validate all 11 event kinds, and enforce the public model
  enums, nullability relationships, identifier/hash formats, array sizes, and
  numeric bounds.
- Allowed finite raw candidate scores in `[-1e12, 1e12]` in both Pydantic and
  SQL.  This is a corruption guard, not score normalization; real BM25 values
  above one remain intact.
- Made `RunView` enforce run-state timestamp consistency while preserving the
  derived `interrupted` view with no completion timestamp, and require strictly
  increasing unique event sequence numbers.
- Added malformed numeric/boolean/sequence strings to prove SQL produces clean
  CHECK violations instead of cast errors, all 11 valid event shapes, raw BM25
  compatibility, UUID format parity, and exact nested plaintext rejection.

### Fix-round GREEN and verification

After dropping and recreating only `specpilot_w3_api_test`, the focused command
reported:

```text
90 passed in 3.56s
```

Static and standard gates:

```text
.venv/bin/python -m ruff check .  # All checks passed
.venv/bin/python -m mypy src      # 84 source files, no issues
make check                         # 1169 passed, 2 pre-existing skips
```

No Task 2 store, writer, or endpoint code was added in this fix round.

The last fail-closed RED added explicit JSON null values for required enum
fields.  Four of 90 cases initially passed because SQL `NOT IN` propagated
NULL and PostgreSQL CHECK accepts NULL.  Explicit JSON string-type guards were
added for status, stage, and verdict before enum comparison; the fresh 90-case
GREEN above includes those regressions.

## Review fix round 2

A final narrow audit found the same PostgreSQL three-valued-logic gap on three
remaining required enum fields: `agent_step.agent`, `agent_step.phase`, and
`tool_finished.tool`.  Pydantic rejected JSON null, while the raw SQL CHECK
returned NULL from `NOT IN` and therefore admitted the row.

### RED

The focused parity command reported:

```text
3 failed, 36 passed in 2.56s
```

The only failures were the three requested raw INSERT cases.  The companion
Pydantic cases passed, proving the database boundary was the mismatch.

### Fix and audit

- Added explicit `jsonb_typeof(...) = 'string'` guards before the remaining
  agent, phase, and tool membership checks.
- Audited all status, stage, verdict, tool, agent, and phase membership checks;
  every required enum now proves string type before membership comparison.
- Added raw wrong-type tests for every optional string field when present.
  Existing helper validators already check JSON string type before length,
  pattern, hash, UUID, or reason validation, so JSON null/wrong types return
  ordinary CHECK failures without casts or function exceptions.

### GREEN

After rebuilding only the exact dedicated database:

```text
105 passed in 4.24s
```

Standard verification:

```text
make check  # Ruff passed; mypy passed for 84 files; 1172 passed, 2 skips
```
