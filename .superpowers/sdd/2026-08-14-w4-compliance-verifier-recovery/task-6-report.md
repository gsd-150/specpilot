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

## Review remediation

`eed0d99 fix: preserve L2 recovery state across process boundaries`

- records reconstruction generations before reconstructed Compliance/Semantic
  provider calls and keeps the same root/run bindings in injected contexts;
- carries the run-scoped recovery flag across all three candidates and refuses
  a recovery send once eight MCP attempts are exhausted;
- allows a job whose resume transaction already acquired its lease to bypass a
  second queue-only `claim`, while normal jobs retain the claim path;
- projects sanitized Compliance/Semantic/Recovery/checkpoint metadata from the
  L2 outcome through the existing typed trace events;
- adds focused regression coverage for three-candidate recovery sharing and
  zero recovery sends after the eight-call budget is consumed.

Evidence after remediation:

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/runtime/test_l2.py tests/unit/runtime/test_worker.py -q
33 passed

PYTHONPATH=src make check
ruff: passed
mypy: passed (104 source files)
unit: 1490 passed, 2 skipped
cli: 181 passed
```

## Second review remediation

`e5a05d4 fix: enforce L2 checkpoint CAS recovery`

- aligns the L2 writer protocol with `PostgresCheckpointStore.write(previous,
  checkpoint)` and persists the first `planned` envelope using previous version
  `None`;
- permits strictly monotonic same-stage checkpoint mutations only for durable
  generation/reservation progress, while the transition validator still rejects
  every backwards or binding-changing write;
- adds a stateful in-memory writer that enforces the real CAS/version and
  `validate_transition` contract through an L2 happy path;
- adds an awaited-write lease fence regression and keeps semantic reservations
  in the checkpoint that records `semantic_verified`.

Evidence:

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/runtime/test_l2.py tests/unit/runtime/test_worker.py tests/unit/checkpoints/test_contracts.py -q
50 passed

PYTHONPATH=src make check
ruff: passed
mypy: passed (104 source files)
unit: 1492 passed, 2 skipped
cli: 181 passed
```
