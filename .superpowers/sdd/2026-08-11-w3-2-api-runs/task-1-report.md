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
