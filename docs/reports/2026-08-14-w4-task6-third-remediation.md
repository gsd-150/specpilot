# W4 Task 6: Third checkpoint remediation

## Delivered

- A prose-free `candidate_count` cursor is persisted with each candidate batch.
  Each semantic result is CAS-written before the next candidate starts; a resume
  skips its completed IDs, and a fully covered semantic cursor completes locally
  without plan/evidence restoration or egress.
- Evidence reconstruction compares the complete ordered checkpoint tuple:
  content and quote hashes, clause/document/version, section and both spans.
  Missing, extra, reordered, or substituted evidence fails closed before
  Compliance can send.
- New runs persist a `planned` generation-zero skeleton before planning egress.
  Provider/invalid-reply receipts are appended through a same-stage CAS before
  their sanitized failure result is returned. Recovery may not reduce a saved
  tool-attempt counter.
- Reconstruction history permits bounded repeated client resumes (64 opaque
  generation tokens); the actual agent contexts derive their egress suffix from
  `logical_stage_key`.
- `RuntimeJobBuilder` is the runtime delivery seam that dispatches L2 runs to
  `L2JobFactory` for both queued and already-acquired resume attempts. It does
  not add an HTTP endpoint (Task 7 remains owner of that route).

## Verification

`PYTHONPATH=.:src make check` passed on 2026-08-14:

- Ruff: passed
- Mypy: 105 source files, no issues
- Unit: 1506 passed, 2 skipped
- CLI: 181 passed

The unprefixed worktree command resolves the shared editable installation from
the primary checkout and therefore imports stale modules; the prefixed command
exercises this worktree's source without changing that shared environment.
