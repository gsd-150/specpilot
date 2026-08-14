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

## Verification

- Fresh-schema focused Python unit and integration suite: 167 passed.
- The new process-restart recovery integration passed with a new FakeProvider
  instance and one recovery only.
- Migration upgrade `001` through `014`, followed by `015`, passed.
- Frontend unit suite: 148 passed.
- Frontend production build: passed.
- Playwright fixture paths over SSE: 5 passed.
- Ruff: passed.  Mypy: 108 source files, no issues.  `git diff --check`:
  passed.

The integration and browser suites required clearing only the dedicated local
test schemas on `localhost:55435` before a fresh migration; no provider was
contacted.
