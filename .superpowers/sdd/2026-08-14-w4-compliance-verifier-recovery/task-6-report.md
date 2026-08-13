# Task 6 report: L2 attempt orchestration

## Delivered

- Added `runtime.l2` as a separately injected L2 state machine: plan →
  evidence → Compliance → deterministic verification → semantic verification.
- Enforced the L2 eight-attempt ceiling through the existing Evidence and
  recovery interfaces; recovery is a single mutable run-scoped allowance and
  reruns deterministic verification before semantic verification.
- Added a prose-free `L2Outcome`, generation-aware logical stage-key helper,
  lease fences after awaited operations, and L2/L1 worker dispatch without
  widening L1's existing limits.
- Added unit coverage for the initial logical key, verified two-gate result,
  and deterministic-failure recovery that reruns deterministic plus semantic.

## Checkpoint boundary

Task 6 accepts an injected checkpoint and writer and advances only a supplied
safe envelope. Initial envelope creation and owner-bound resume delivery remain
Task 7 responsibilities. The state machine does not persist question, claim,
rationale, evidence text, or provider response.

## Evidence

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/runtime/test_l2.py tests/unit/runtime/test_worker.py -q
31 passed

PYTHONPATH=src make check
ruff: passed
mypy: passed (104 source files)
unit: 1488 passed, 2 skipped
cli: 181 passed
```

PostgreSQL integration was not run: the local database service is unavailable;
the existing W4 fresh-service verification is deferred to Task 8.

## Commit

`354a290 feat: run L2 candidates through both verifier layers`
