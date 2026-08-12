# W3 Part 1 MCP and Orchestration Verification

**Date:** 2026-08-12

**Scope:** Part 1 only, from base `f8f0b1d` through verified implementation
head `6697858`. The audited implementation diff contains 46 files, 6,304
insertions, and 169 deletions.

## Result

The Part 1 verification gate passed. The five read-only MCP tools run through
the real Streamable HTTP MCP SDK protocol, planning and answer provider calls
cross `PolicyBoundTransport`, the planner accepts ordinary JSON content rather
than native provider tool calls, and PostgreSQL records the planning attempt
without a source-disclosure row.

No real provider was called. No live ledger, restricted corpus, remote vector
service, or W3 Part 2 implementation was accessed or changed.

## Fresh verification evidence

Commands below are reproduced exactly except that the local PostgreSQL
connection value is represented as `<dedicated-test-dsn>`. The database name
and all test results are recorded separately; no connection string or
credential is included.

The exact Task 6 focused command without database configuration produced the
expected environment-only skip:

```text
.venv/bin/python -m pytest tests/unit/agents tests/unit/mcp_server tests/unit/egress/test_planning_projection.py tests/integration/mcp_server tests/integration/agents -q
70 passed, 1 skipped in 0.87s
```

The skip was only the PostgreSQL planning-ledger integration because
`SPECPILOT_TEST_DSN` was unset. After recreating the dedicated database, the
same focused scope ran with no skip:

```text
SPECPILOT_TEST_DSN=<dedicated-test-dsn> .venv/bin/python -m pytest tests/unit/agents tests/unit/mcp_server tests/unit/egress/test_planning_projection.py tests/integration/mcp_server tests/integration/agents -q
71 passed in 1.24s
```

The exact Task 5 database flow, from a separately recreated database, passed:

```text
SPECPILOT_TEST_DSN=<dedicated-test-dsn> .venv/bin/python -m pytest tests/unit/agents tests/unit/answer/test_run.py tests/unit/providers tests/integration/agents/test_planning_ledger_flow.py tests/integration/providers/test_transport_ledger_flow.py -q
87 passed in 1.70s
```

Migration 004 and the three database-backed replay/no-resend cases were then
run from another fresh recreation:

```text
SPECPILOT_TEST_DSN=<dedicated-test-dsn> .venv/bin/python -m pytest tests/integration/egress/test_planning_stage_migration.py tests/integration/providers/test_transport_ledger_flow.py::test_a_replayed_key_fails_closed_without_an_uncharged_transmission tests/integration/providers/test_transport_ledger_flow.py::test_a_failed_attempt_replay_is_not_sent_again tests/integration/providers/test_transport_ledger_flow.py::test_concurrent_same_key_has_one_send_and_one_closed_replay -q
4 passed in 0.59s
```

The real MCP protocol and runtime-composition matrix passed without a skip:

```text
.venv/bin/python -m pytest -q tests/integration/mcp_server/test_streamable_http.py tests/unit/mcp_server tests/unit/test_compose_exposure.py
57 passed in 0.92s
```

The source-free planning, formal response schema, no-native-tool, sanitized
summary, replay no-send, policy projection, disclosure-cap, and old-policy
mismatch guards passed:

```text
.venv/bin/python -m pytest -q tests/unit/providers/test_http_adapter.py::test_planning_payload_sends_source_free_catalog_json tests/unit/providers/test_http_adapter.py::test_planning_never_uses_native_provider_tool_calls tests/unit/providers/test_http_adapter.py::test_planning_payload_uses_the_formal_json_only_contract tests/unit/agents/test_tool_plan.py::test_call_summary_keeps_only_sanitized_metadata tests/unit/providers/test_transport_fail_closed.py::test_replayed_reservation_fails_closed_before_adapter_send tests/unit/egress/test_planning_projection.py tests/unit/egress/test_policy_projection.py tests/unit/egress/test_disclosure_caps.py tests/unit/egress/test_corpus_disclosure_budget.py::test_corpus_ledger_is_pinned_to_one_policy_snapshot
39 passed in 0.22s
```

The exact Task 6 static command passed:

```text
.venv/bin/python -m ruff check src/specpilot/agents src/specpilot/mcp_server src/specpilot/contracts/egress.py src/specpilot/providers tests/unit/agents tests/unit/mcp_server && .venv/bin/python -m mypy src
All checks passed!
Success: no issues found in 82 source files
```

Repository fast verification passed:

```text
make check
All checks passed!
Success: no issues found in 82 source files
1133 passed, 2 skipped in 2.72s
```

The two unit skips are explained and unrelated to Part 1: both require
restricted RFC fixtures that are not present in this worktree. No required MCP
protocol test skipped, and the PostgreSQL planning test was rerun with no skip.

`docker compose -f compose.yaml --profile demo config --quiet` and
`git diff --check f8f0b1d..HEAD` both exited zero.

## Database and migration evidence

The only mutable database target was exactly `specpilot_w3_mcp_test`. The
`postgres` catalog was queried read-only to confirm that exact identity. The
live database `specpilot_live` was never connected to or mutated.

The fresh planning flow proves exactly one planning reservation, one provider
attempt, and zero disclosure rows for that reservation. Migration
`004_egress_planning_stage.sql` preserves populated rows and replaces the named
stage constraint with exactly this five-value set:

- `planning`
- `evidence`
- `compliance`
- `verifier`
- `judge`

The migration test also rejects an unknown sixth value. Migration 004 changes
only the closed stage set; it does not authorize a route, change disclosure
usage, or rebind a corpus ledger policy epoch.

## Policy transition and operator action

The packaged canonical policy hash changes from
`be9efe3983a313532c53d4c4d9b2cf2acbcbb843ba02a91bad330ada69415b4c`
at `f8f0b1d` to
`1dc8c5f2fb100b07d87d1265226fcd9ad879253c1dbf8f776bd7d788f034a080`
at `6697858`. The added planning stage and `l1_plan_tokens` field are therefore
a new policy snapshot, not an in-place interpretation of an old epoch.

For an existing populated ledger, an operator must first apply migration 004,
then use the documented explicit `specpilot egress rebind-policy` successor
flow with the authoritative expected ledger UUID and expected predecessor
policy hash. Ordinary reservation remains fail-closed with
`policy_snapshot_mismatch`; it never rebinds automatically. The successor
inherits the complete corpus and per-document usage snapshot, and its first
reservation must use a new evaluation root. Applying migration 004, performing
the policy rebind, and enabling planning on `specpilot_live` remain separate
owner-controlled operations.

## Diff and disclosure audit

The complete `f8f0b1d..6697858` diff was reviewed, including implementation,
tests, migration, runtime configuration, Compose, and documentation.

- The exact outbound scan returned two expected matches: the planner calls
  `PolicyBoundTransport.send`, and the MCP client accepts a local
  `httpx.AsyncClient`. The broader repository scan finds provider
  `adapter.send` only inside `providers/transport.py`; provider HTTP client
  construction remains inside `providers/http.py`.
- Planning sends the authored query, version bindings, five authored schemas,
  and fixed plan bounds. It has no TOC, source excerpt, candidate body, clause
  body, credential, or local-artifact location field, and produces zero
  disclosure facts.
- Planning suppresses probe/native provider tools and parses only ordinary
  response content through the frozen, extra-forbidden `ToolPlan` model.
- `ToolCallSummary` retains only step/tool identity, argument-key names, result
  count, duration, retry count, and a stable error code. It has no query,
  result, excerpt, candidate, exception, credential, or location value field.
- MCP error boundaries return closed codes and fixed corrections. Protocol
  tests cover malformed envelopes, invalid tool arguments, service failures,
  oversized input, decoder failure, and malformed typed output without
  returning or logging caller payload or internal location values.
- A replayed reservation is rejected before `adapter.send`. The fresh database
  tests prove successful replay, failed-attempt replay, and concurrent
  same-key replay produce no second send.
- All five tool wrappers are read-only and delegate to the local service
  boundary. No mutation tool is registered.
- No file under `artifacts/restricted/`, `manifests/local/`, `data/`, or `tmp/`
  is tracked in the audited tree.

## Known limitations

- No live provider call was made. That spends a real budget and remains an
  author-controlled acceptance action.
- Protocol verification used the real MCP SDK and serialization against an
  in-process ASGI application with synthetic frozen inputs. It did not use a
  remote corpus or vector service.
- Runtime composition deliberately has no guessed repository-wide artifact
  defaults. A deployed MCP process requires explicit manifest and verified
  source bindings and otherwise exposes only a sanitized unavailable health
  response.
- The two `make check` skips require restricted fixtures absent from this
  worktree. They do not skip Part 1 MCP, planner, transport, or database tests.
- Part 1 supplies orchestration primitives, not durable run traces. The
  question/excerpt/candidate trace allowlists, asynchronous run persistence,
  API, and page belong to the unstarted W3 Part 2 and Part 3 plans.

The dedicated database is a throwaway verification resource and is removed
after this report commit; cleanup evidence is retained only in the ignored
Task 6 coordination report.
