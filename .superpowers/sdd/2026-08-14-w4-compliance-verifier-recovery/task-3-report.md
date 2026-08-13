# Task 3 report: Ledger-bound Compliance and semantic agents

## Status

Implemented and committed as `82ab9f9 feat: meter Compliance and semantic
verification separately`.

## RED / GREEN evidence

- RED: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_compliance.py tests/unit/verifier/test_semantic.py -q` initially failed during collection with missing `specpilot.agents.compliance` and `specpilot.verifier.semantic` modules.
- GREEN: the same focused unit command passed after implementation (later expanded to five boundary tests).
- Focused verification: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_compliance.py tests/unit/verifier/test_semantic.py tests/unit/egress -q` passed: `85 passed`.
- Full project check: `PYTHONPATH=src make check` passed: Ruff clean, mypy clean over 99 source files, `1454 passed, 2 skipped` unit tests, and `181 passed` CLI tests.
- Fresh diff validation: `git diff --check` and `git show --check HEAD` were clean.

## Files

- Added `src/specpilot/agents/compliance.py`: L2 Compliance request construction, 12-excerpt cap, parsed batch, server-owned claim IDs, generation-aware stage key, and sanitized malformed-reply error.
- Added `src/specpilot/verifier/semantic.py`: local deterministic gate, citation-bound Evidence projection, L2 Verifier request construction, exact response Evidence-ID binding, generation-aware stage key, and sanitized malformed-reply error.
- Added `tests/unit/agents/test_compliance.py` and `tests/unit/verifier/test_semantic.py` for payload/stage/key, malformed parse, and no-send boundaries.
- Added `tests/integration/agents/test_l2_ledger_flow.py` for the same root/run and independent Compliance/Verifier reservations.

## Self-review

- Both outward calls are exclusively through `PolicyBoundTransport`.
- Compliance and semantic use distinct payload types and egress stages; no policy fields or cap values changed.
- Semantic sends only after `DeterministicResult.passed` and only projects candidate Evidence IDs which also appear in deterministic citations; empty evidence therefore cannot reach `FakeProvider`.
- Provider prose is neither persisted nor attached to exceptions; failure metadata is limited to reservation, replay, and request size.
- Each provider operation key contains run ID, stage, explicit logical label, and reconstruction generation.

## Concern / integration blocker

Fresh PostgreSQL integration evidence was not produced. `pg_isready` returned `/tmp:5432 - no response`; therefore `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/agents/test_l2_ledger_flow.py -q` correctly reported `1 skipped` because `SPECPILOT_TEST_DSN` is unset. No database was created or reused, and no dirty-DB run was substituted.

## Review fix round 1

Addressed I1, I2, and the related Task 1 parser clarification.

- RED: new focused tests initially failed: semantic transport occurred when the deterministic citation set had an extra Evidence ID, and traceback traversal exposed Compliance's sentinel raw provider reply.
- GREEN: Semantic now requires exact set equality between candidate Evidence IDs and deterministic citation content hashes before any transport. A candidate naming `{A}` with deterministic citations `{A, B}` raises locally with zero reservations, attempts, or provider calls.
- GREEN: parse/send helpers return `None` for invalid content and delete the transport receipt before control reaches the frame that raises a sanitized exception. Adversarial traceback-local tests confirm Compliance raw reply and semantic rationale sentinels are absent from production traceback frames; cause/context remain empty.
- GREEN: Compliance preserves a syntactically valid undisclosed model Evidence ID unchanged, leaving Task 2 to classify it as `not_disclosed`.
- Verification: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_compliance.py tests/unit/verifier/test_semantic.py tests/unit/egress -q` -> `87 passed`; `PYTHONPATH=src make check` -> Ruff clean, mypy clean, `1456 passed, 2 skipped` unit tests, `181 passed` CLI tests.
