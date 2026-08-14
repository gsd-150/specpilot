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
