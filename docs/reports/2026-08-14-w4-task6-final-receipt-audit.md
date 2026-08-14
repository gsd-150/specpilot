# W4 Task 6: final receipt and recovery audit

## Final fixes

- The first semantic receipt is written through same-stage CAS before recovery
  can await local MCP. Later recovery, second semantic receipt, outcomes, and
  trace projection therefore retain both receipt identities.
- A reconstructed Compliance batch must retain every saved completed claim ID
  and the saved bounded batch cardinality. Missing, duplicated, or altered
  cursor membership fails closed after its receipt is saved.
- `L2JobFactory` replaces the caller's planner context with run-bound corpus,
  root, model, run ID, L2 level, and canonical planning idempotency root.
- Checkpoint generation and reservation histories are bounded at eight: the
  actual L2 call allowance. Generation exhaustion is detected before egress;
  evidence attempt counters cannot decrease or exceed eight.
- Transport replay refusal is a sanitized terminal fault, with its reservation
  durably appended first.

## Verification

`PYTHONPATH=.:src make check` passed on 2026-08-14: Ruff; mypy (105 source
files); 1508 unit passed / 2 skipped; and 181 CLI passed.
