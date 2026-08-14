# Task 8 report

Completed the fixture-only W4 integration and review remediation without a live
model/provider.

- Four production-HTTP L2 scenarios cover the happy path, deterministic
  rejection with one recovery, two semantic rejections, and process loss plus
  owner-assisted client resume.
- The process-loss scenario proves that the evaluation root, caps and recovery
  state survive; locally reconstructible evidence is not recollected; the lost
  Compliance result advances from generation 0 to generation 1; and both
  transmitted and unique disclosure ledgers increase by the exact disclosures
  introduced by the new reservations.
- `L2Outcome` carries ordinary evidence-tool summaries and per-claim
  deterministic results. A worker-injected real-time audit sink projects these
  through the existing closed `tool_finished` and `verifier_summary` contracts
  before later outward work, without persisting query, claim, excerpt or
  rationale prose. No trace migration was needed.
- PostgreSQL compaction retains sanitized final metadata while clearing active
  reconstruction detail. TTL deletion excludes completed checkpoints and runs
  that remain queued/running, and removes only inactive eligible state.
- The README now applies exactly the real migrations 006--012 in repository
  order instead of naming nonexistent 007/008 files, replaying 001--005, or
  silently including future migrations.

## RED/GREEN evidence

- Fresh database `specpilot_w4_review_red1`: the new trace/TTL tests failed with
  no ordinary tool summaries and two deleted checkpoints instead of one.
- Fresh database `specpilot_w4_review_green1`: the same two tests passed.
- Fresh database `specpilot_w4_review_resume1`: the process-loss test exposed an
  incorrect test assumption that unique disclosures could never grow. The
  recovery checkpoint contained evidence not transmitted before the lost model
  result, so the assertion was tightened to the exact set of novel disclosure
  IDs rather than weakening the production accounting.
- Fresh database `specpilot_w4_review_resume2`: the process-loss test passed.
- Fresh database `specpilot_w4_review_e2e2`: all four L2 HTTP scenarios passed.
- Fresh database `specpilot_w4_review_aggregate1`:

  ```text
  PYTHONPATH=src SPECPILOT_TEST_DSN=... SPECPILOT_TEST_QDRANT_URL=... \
    .venv/bin/python -m pytest --import-mode=importlib \
    tests/integration/api/test_l2_end_to_end.py \
    tests/integration/api/test_l2_resume.py \
    tests/integration/checkpoints/test_postgres_store.py -q
  17 passed in 11.82s
  ```

Round-2 trace durability remediation:

- Fresh database `specpilot_w4_r2_red1`: the process-loss E2E exposed duplicate
  checkpoint summaries and missing pre-crash audit facts.
- Fresh database `specpilot_w4_r2_green2`: real-time audit and checkpoint
  version uniqueness passed.
- Fresh database `specpilot_w4_r2_reconcile1`: the test removed one sealed
  Verifier egress trace, and client resume rebuilt it from the checkpoint-bound
  ledger without duplicates.
- Fresh database `specpilot_w4_r2_aggregate2`: 39 focused L2/checkpoint cases
  passed in 10.95s.

Every database above was newly created for its test command and deleted after
the result. The isolated PostgreSQL and Qdrant services and the separate
diagnostic database remain running for review.

## Final verification

- `PYTHONPATH=src make check` → ruff/mypy clean, 1,524 unit passed, 181 CLI
  passed.
- Fresh database `specpilot_w4_r2_final_20260814`, isolated Qdrant and
  `FakeProvider` only: `1981 passed in 31.33s`, 0 skipped, process exit 0. The
  database was dropped after the result.
- `git diff --check` and the committed range check from
  `1017c1668da7e6cb9a83dd6107be6a84b052f566` both completed without output.

The exact commands are also recorded in
`docs/reports/w4-compliance-verifier-recovery.md`.

## Concerns left explicit

- These are deterministic fixture and service-integration results, not L2
  calibration or live-provider acceptance.
- Trace rows are closed sanitized summaries. Provider response, retrieval query,
  claim/design/rationale and quoted evidence prose remain deliberately absent.
- Retention is operator-driven; W4 does not add an automatic cleanup daemon.
