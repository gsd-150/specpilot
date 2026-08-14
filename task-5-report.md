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

## Verification

- Python unit focus: 112 passed.
- API integration focus on the dedicated local W5 database: 9 passed.
- Trace asset integration: 3 passed.
- Frontend unit suite: 147 passed.
- Frontend production build: passed.
- Playwright fixture paths over SSE: 5 passed.
- Ruff: passed.  Mypy: 108 source files, no issues.  `git diff --check`:
  passed.

The integration and browser suites required clearing only the dedicated local
test schemas on `localhost:55435` before a fresh migration; no provider was
contacted.
