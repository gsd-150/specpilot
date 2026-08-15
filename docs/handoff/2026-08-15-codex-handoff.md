# Codex handoff — 2026-08-15 W5 starting state

## Current state — 2026-08-15

This handoff is the dated starting snapshot for the first W5 vertical slice.
It supersedes earlier status prose without deleting that historical record.

- The recomputed restricted stores report **L1 40/40**, **L2 20/20**, zero
  awaiting adjudication, **deep review 12/12**, and fully sealed pooling.
- The current quick gate is **1537 unit, 187 CLI**.
- The dated 2026-08-14 full-service run at `b89339d` recorded **1998 passed, 0
  skipped** against a fresh PostgreSQL database and frozen Qdrant collection.

The full-service result is fixture-only engineering and service-integration
evidence. It does not establish real-provider acceptance, quality, calibration,
latency, L2 development metrics, locked evaluation, or release evidence. A
mistyped pooling choice re-prompts the reviewer and **does not end the pass**.

Still open for W5/W6: SSE/reconnect, the four-scenario demo/profile matrix, the
evaluation `run_spec`, and first locked evaluation. Do not use fixture evidence
as a quality metric or as real-provider evidence.

## Judge scoring — 2026-08-15

The author chose the auto-judge route (recorded in §8.3) and the judge scoring
path was delivered the same day. Shipped: the closed scoring contracts, the
versioned/hashed judge prompt with its reply schema, the §8.3.2 calibration
mathematics (per-label-set agreement, Cohen's kappa, confusion, severe-flag
cross-tab), the §8.4 answer-metric aggregation, the content-addressed judge and
human-label stores, the prose-free freeze evidence builder, and the
`judge calibrate` / `labels-template` / `labels-add` / `score` CLI.

- The quick gate after the delivery is **1993 unit + CLI passed**, Ruff clean,
  strict mypy clean over **126 source files**. Fixture-only; no provider call
  is exercised by the suite.
- One real defect was caught during the build by the cross-join regression
  test: the prompt asked for a `key_points` field the parser contract names
  `key_point_hits` — the fault a contract-to-contract test cannot see.
- `judge score` is an author-run real-provider command (judge credential in
  the environment, `offline_judge` route authorization from the source
  manifest). The dev runs, the human label sheets, real judge calls, and
  calibration acceptance remain author-owned.

The freeze path is now: dev runs → `judge score` → `labels-template` →
author labels → `labels-add` → `judge calibrate` (evidence + hash) →
`dev-scoring-status.json` → `freeze-candidate` → author `freeze-confirm`.
