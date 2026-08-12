# W3 Part 2: Asynchronous API and Run Trace Evidence

**Date:** 2026-08-12

## Result

Part 2 implements owner-authenticated asynchronous L1 runs, typed sanitized
traces, leased interruption semantics, and deployment assembly for the API and
MCP client. It does not add SSE, a browser page, durable question checkpoints,
or a real-provider run.

The deployed deterministic fixture assembly crossed environment loading,
FastAPI, the leased worker, its configured fixture provider, Streamable HTTP
MCP, the E2E fixture's local deterministic search backend, the real
PostgreSQL disclosure ledger, and the citation verifier. It reached
`answered`, recorded exactly one successful `planning` reservation and one
successful `evidence` reservation, and returned 17 typed trace events. The
trace contained neither the question nor the fixture excerpt.

## Fresh end-to-end evidence

The E2E case consumed only the dedicated `specpilot_w3_e2e_test` PostgreSQL
database and its local deterministic fixture search backend. The database was
dropped and recreated before the successful run so migrations 001 through 005
were applied in filename order. The isolated Qdrant service at
`127.0.0.1:6334` was available and its URL was present in the pytest
environment, but this one API E2E case did not call it.

```text
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_e2e_test \
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6334 \
.venv/bin/python -m pytest tests/integration/api/test_l1_end_to_end.py -q

1 passed in 1.31s
```

The test selects the fixture provider through `_assemble_runtime`; it does not
inject an alternate answer provider. Unit regressions cover startup
cancellation, partial entry with a `BaseException`, cleanup failure while
preserving the primary failure, concurrent start/close, close cancellation,
same-task exit, and repeated close.

## Full verification

The dedicated database was rebuilt again before the final complete suite. The
existing real-manifest safety test requires the two ignored local source
records; exact temporary copies were supplied with the secure store's required
permissions, remained ignored, and were deleted immediately after the run.

```text
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_e2e_test \
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6334 \
.venv/bin/python -m pytest -q

1837 passed, 2 skipped in 23.30s
```

The two skips are the repository's pre-existing unit dependency skips. No
integration, database, Qdrant, API end-to-end, or fixture-smoke case skipped.
Unlike the focused API E2E above, this complete invocation executed the real
`tests/integration/qdrant` cases against the isolated service; those tests are
the Qdrant consumption evidence for Part 2.

Additional gates:

```text
.venv/bin/python -m ruff check .
All checks passed!

.venv/bin/python -m mypy src
Success: no issues found in 93 source files

docker compose -f compose.yaml config --no-interpolate
exit 0

.venv/bin/python -m pytest tests/unit/test_compose_exposure.py \
  tests/unit/api/test_api_runtime.py -q
27 passed in 0.57s

.venv/bin/python -m pip wheel . --no-deps --no-build-isolation -w <temp>
Successfully built specpilot
```

The wheel contained the API runtime, MCP server/client modules, and packaged
policy. It was installed without dependencies into an isolated temporary
target and imported from outside the repository using the verified workspace
runtime dependencies.

Three bounded local Docker build attempts reached runtime image step 9 of 13
(`pip install /wheels/*.whl uvicorn`) but did not produce an image while public
dependencies were still downloading. The final attempt reached the
`psycopg-binary` and Pydantic dependency metadata downloads. This is an
external network evidence gap, not recorded as a passed Docker gate. Both
Dockerfiles build the same wheel dependency set, which now includes
`mcp>=1.26,<2`; Compose and the wheel/package checks above passed.

## Operational boundary

Migration 004 extends the closed reservation stage set with `planning`.
Migration 005 adds owner-bound run and typed trace tables. The wheel and images
do not apply either migration. Existing corpus ledgers also require the
explicit Task 11 policy-successor/rebind operation before planning is enabled.
Nothing in this work migrated or rebound `specpilot_live`, called a real
provider, or used the real corpus in an outbound request.

The base Compose file publishes no host port. API and MCP share three explicit
read-only frozen-artifact mounts at fixed container paths; host paths are used
only as mount sources. MCP remains internal-only. The base/real API joins the
egress network, while the demo override replaces that topology with internal
plus its loopback-published demo bridge. Missing or invalid runtime
configuration exposes only a sanitized unavailable health response.
