# W4 Task 6: final lease and planning-key audit

Semantic receipt sealing now precedes the post-response lease check. If a
provider reply races lease loss, the only permitted side effect is the fenced
same-stage checkpoint CAS that records its reservation; recovery and later
egress do not begin. A rejected CAS yields the existing safe failure path while
the independently ledgered reservation remains auditable.

Planning now supplies the exact canonical transport key
`<run>-planning-initial-g0`. Planner recognizes that already-generation-suffixed
root and never appends a second suffix; later reconstruction keys retain their
matching generation exactly.

Verification on 2026-08-14: `PYTHONPATH=.:src make check` (Ruff, mypy, unit,
and CLI suites).
