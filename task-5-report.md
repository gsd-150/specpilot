# W5 Task 5 report: registered offline demo scenarios

## Delivered

- Added the four closed scenario IDs: `l1_answered`, `l2_answered`,
  `evidence_refused`, and `verifier_recovered`.
- Kept fixture questions, replies, and script versions in the server-only
  registry.  The fixture bootstrap exposes only sanitized public metadata.
- Registered scenarios select scripts in the runtime job builder.  Real
  profiles reject every scenario ID with `invalid_demo_scenario`; fixture
  custom input has no ID and terminates as `unsupported_demo_case` without a
  provider call.
- Added the fixture-only scenario picker and retained ordinary input for real
  profiles.  A fast initial terminal snapshot now replays through SSE so every
  fixture browser path remains a streaming trace.
- Added one semantic recovery script: its first semantic decision fails, one
  directed recovery is emitted, and the next decision reaches the registered
  terminal state.
- Review fix: an authoritative initial terminal snapshot is preserved while a
  bounded cursor-zero SSE replay is checked in a separate buffer.  A partial
  replay failure cannot regress the terminal status or retry the connection.
- Review fix: the closed scenario ID is stored server-side only and omitted
  from `RunView`.  Resume reconstructs the registered script after a runtime
  restart; the recovery checkpoint prevents a second synthetic first-failure.
- Review fix: resume now derives a closed, owner-scoped recovery phase from
  durable sanitized events.  If the first semantic `false` survives a crash
  before `recovery_reserved`, a fresh runtime skips replaying that false,
  persists exactly one `recovery_reserved`/`recovery_completed` pair, emits one
  recovery, and reaches the registered terminal state with semantic outcomes
  exactly `[false, true]`.
- Migration coverage now checks the exact nullable scenario column and closed
  constraint after both a fresh `001` through `015` migration and a legacy
  `014` to `015` upgrade with a pre-existing row whose identity remains NULL.

## Verification

- Fresh-template0 focused Python unit and integration suite: 225 passed.
- Hard-bounded process-restart recovery integrations: 2 passed with new
  `FakeProvider` instances, semantic outcomes exactly `[false, true]`, one
  recovery only, and terminal completion.  The pre-reservation case also
  observed the exact resumed checkpoint subsequence `recovery_reserved`,
  `recovery_completed`.
- Focused fresh `001` through `015` and legacy `014` to `015` migration checks:
  4 passed.
- Frontend unit suite: 148 passed.
- Frontend production build: passed.
- Playwright fixture paths over SSE: 5 passed.
- Ruff: passed.  Mypy: 108 source files, no issues.  `git diff --check`:
  passed.

The integration and browser suites required clearing only the dedicated local
test schemas on `localhost:55435` before a fresh migration; no provider was
contacted.
