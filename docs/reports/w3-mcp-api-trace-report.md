# W3 MCP, API, and Trace Release Report

**Date:** 2026-08-13

**Scope:** branch `feat/w3-mcp-api-trace`, 49 commits from base `f8f0b1d`
through implementation head `5052b4c` before this report commit. The audited
range changes 106 files with 22,786 insertions and 181 deletions.

## Result

The W3 slice implements the five read-only tools over real Streamable HTTP MCP,
a model-authored bounded planner, the Evidence Agent, owner-scoped asynchronous
FastAPI runs, typed sanitized traces, bounded React polling, installed-resource
static serving, and a local fake-provider browser closure. Every planner and
answer provider attempt crosses `PolicyBoundTransport` and the PostgreSQL
ledger.

The state regressions are explicit: a non-null `provider_error` selects
`failed` regardless of verifier verdict; disclosure admission errors select
`egress_blocked`; an expired running lease reads as `interrupted`; and the
client polling deadline preserves the last server status. There is no SSE or
events endpoint in this release.

No real provider was called. No real source corpus, provider credential,
`specpilot_live` row, or existing Qdrant collection was used or changed.

## Fresh release evidence

`make check` completed with clean Ruff and strict mypy over 94 source files,
then 1,419 unit tests passed. Two restricted-fixture tests skipped because their
gitignored material was intentionally outside this release gate.

Against a newly created database named `specpilot_w3_release_test` and an
ephemeral `qdrant/qdrant:v1.12.4` container named `specpilot-w3-qdrant`, exposed
only at `127.0.0.1:6334`, the complete integration suite passed 248 tests with
zero skips. The separately invoked Qdrant scope passed 17 tests with zero
skips. A separately fresh `specpilot_w3_smoke_test` database passed all five
fixture-smoke tests; four non-fixture-smoke tests were intentionally deselected
by the exact marker command.

A clean `npm ci` installed the locked frontend dependencies. Vitest passed 104
tests across the API decoder/client, bounded polling hook, and UI. TypeScript
and Vite produced the packaged trace HTML, hashed CSS, and hashed JavaScript.
The final Playwright run passed one browser case in 4.3 seconds. That case used
`POST /sessions/demo` to obtain an HTTP-only cookie, submitted a question,
observed real worker and MCP activity, observed planning and evidence
reservations, reached `answered`, and found no submitted question, synthetic
excerpt, cookie value, local path, raw fixture answer, or model identity in the
rendered document.

The browser launcher accepts only the fresh loopback database
`specpilot_w3_browser_test`, applies migrations 001--005, assembles the same API
runtime with a synthetic RFC corpus and local `FakeProvider`, and clears that
allowlisted schema on bounded shutdown. It does not intercept the primary flow.

`python -m build` produced both sdist and wheel in isolated environments. The
wheel and sdist each contain trace HTML, hashed CSS, and hashed JavaScript. A
new operating-system temporary virtual environment outside the checkout
installed the wheel and its declared dependencies and loaded `create_app`, the
MCP app factory, packaged policy, `/trace`, and both static assets successfully.

## Packaging evidence

The API Docker build was attempted twice after supplying ignored empty mount
directories so Compose could resolve the service. Both attempts reached step 1
of the 21-step Dockerfile and failed before reading project code because the
Docker daemon could not resolve `registry-1.docker.io`: its DNS query to
`[::1]:53` was refused while resolving the pinned
`node:22.12-bookworm-slim` base. This is a local Docker DNS/environment failure,
not a passing image-build result. CI now runs `npm ci`, frontend tests, and the
frontend build before Python packaging, and the multi-stage Dockerfile copies
only built assets and the wheel into the Python runtime image.

After this release report was written, draft PR #1 closed the environment-
specific evidence gap. Its PR-triggered and push-triggered GitHub Actions runs
both built the API, MCP, and ingestion images successfully. The same portability
repair made Compose artifact mounts runner-local, made Playwright honor
`SPECPILOT_PYTHON`, and removed the API integration test's assumption that
Qdrant must use host port 6334. Current Vitest verification passes 106 tests;
the 104-test result above remains the original release-gate record.

## Operator boundary and remaining work

Migration 004 extends the egress-stage closed set with `planning`. Migration
005 adds owner-bound runs and sanitized events. Neither artifact is applied by
the wheel or API image. An existing ledger also needs migration 003 and an
explicit `egress rebind-policy` successor because the planning policy changes
the policy hash. `specpilot_live` was deliberately left untouched.

SSE, reconnect semantics, `/chat/{run_id}/events`, real-provider acceptance,
L2/Compliance, checkpoint recovery, and the full W5 demo matrix remain outside
this W3 slice.
