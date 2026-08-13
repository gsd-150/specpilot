# Task 5 implementation report

## Commit

- `838e381 feat: persist sanitized W4 checkpoints and attempts`

## RED → GREEN evidence

- RED: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/checkpoints/test_contracts.py -q`
  failed with `ModuleNotFoundError: specpilot.checkpoints` (11 failures).
- GREEN: the same contract test is now included in the focused passing run.
- Focused checks:
  - `PYTHONPATH=src .venv/bin/python -m ruff check src/specpilot/checkpoints src/specpilot/runs tests/unit/checkpoints tests/integration/checkpoints`
  - `PYTHONPATH=src .venv/bin/python -m mypy --strict src/specpilot/checkpoints src/specpilot/runs`
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/checkpoints/test_contracts.py tests/unit/runs/test_contracts.py -q`
  - Result: ruff clean, mypy 0 errors, `60 passed`.
- Broad verification: `PYTHONPATH=src make check` → ruff clean, mypy clean,
  `1481 passed, 2 skipped` unit tests, `181 passed` CLI tests.

## Database evidence gap

`PYTHONPATH=src .venv/bin/python -m pytest tests/integration/checkpoints/test_postgres_store.py -q`
collected the three fresh-DB store tests but skipped all of them because
`SPECPILOT_TEST_DSN` is unset. No database was created or modified. The suite
must run migration 006 against a fresh PostgreSQL database before W4 can claim
full persistence evidence.

## Review remediation

- Added immutable/monotonic transition validation before a non-initial CAS
  update, including bindings, tool attempts, reservations, reconstruction
  generations, and recovery state.
- The L2 run binding now carries an explicit evaluation root and independent
  Compliance/Verifier prompt hashes. The checkpoint never derives any of them
  from a run ID or the L1 prompt hash; referenced reservations are verified
  against the same root and run.
- Resume key replay is checked before the interrupted-status gate, and a newly
  acquired attempt atomically updates the checkpoint attempt/version and emits
  both state-transition and `resume_summary` events. Lease reconciliation seals
  an open attempt with `lease_expired`.
- Migration validators now recurse through reservation UUIDs, stage
  generations, completed result/citation metadata and ordered claim IDs, and
  enforce Compliance summary hash uniqueness/counts. Completed checkpoints are
  retained as their sanitized result projection when compacted.
- Re-run after remediation: `make check` passed (`1483 passed, 2 skipped` unit;
  `181 passed` CLI). PostgreSQL tests still skipped only because the DSN is
  unset; no database was used.

## Second review remediation

- First write is now only `planned`; later stages must advance from a valid
  persisted checkpoint.
- Resume accepts only `interrupted/lease_expired`; replay remains available
  before the state gate, while different keys on a live attempt are closed.
- Resume revalidates every referenced reservation and accepts only unambiguous,
  terminal ledger states before it mutates run, attempt, or checkpoint state.
- Completed compaction now rewrites its sanitized payload timestamp and column
  timestamp together. SQL validators reject duplicate reservation/evidence/
  generation/completed claim identities, and L2 stage prompt hashes must differ.
- Second re-run: focused unit `62 passed`; integration checkpoint suite `4
  skipped` only because `SPECPILOT_TEST_DSN` is unset; `make check` unchanged
  and passing (`1483 passed, 2 skipped`; `181 passed`).

## Third review remediation

- Same-key replay remains first; a different key is `leased` while a queue or
  worker lease is live, while only `interrupted/lease_expired` can acquire.
- Unknown/nonterminal/mismatched reservation lineage now returns the closed
  `checkpoint_invalid` disposition without any state mutation.
- Contract and SQL both reject equal Compliance/Verifier prompt hashes. SQL
  generation identity now includes the generation number, matching Python.
- Completed compaction drops active reconstruction details (plan, evidence,
  tool count, reservation and generation detail) but retains the completed
  prose-free result metadata and synchronizes payload/column timestamps.
- Third re-run: focused unit `15 passed`; checkpoint integration `4 skipped`
  because `SPECPILOT_TEST_DSN` is unset; `make check` passed (`1485 passed, 2
  skipped`; `181 passed`).

## Scope

- Closed prose-free checkpoint envelope and legal stage transitions.
- Migration 006 adds L2 task-level support, checkpoint/attempt tables, strict
  checkpoint JSON validator, and closed W4 trace event kinds.
- Transactional checkpoint CAS write, atomic checkpoint summary event, hashed
  resume key, owner/query/binding checks, attempt rows and resume lease claim.
- The resume API and L2 orchestrator intentionally remain for Tasks 6–7.
