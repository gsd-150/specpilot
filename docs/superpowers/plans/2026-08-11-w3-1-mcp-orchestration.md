# W3 Part 1: MCP and Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build five read-only Streamable HTTP MCP tools and a ledger-bound model planner that returns and executes a bounded typed tool plan.

**Architecture:** Add a source-free `l1_plan` egress contract, validate the model's JSON content as a four-step/six-call plan, and execute it through an MCP client. Local tool services hold corpus and retrieval objects; FastMCP wrappers contain no retrieval logic and traces receive only sanitized summaries.

**Tech Stack:** Python 3.12–3.14, Pydantic 2, MCP Python SDK/FastMCP, FastAPI/Starlette ASGI, httpx, PostgreSQL egress ledger, pytest/AnyIO.

## Global Constraints

- `SpecPilot_项目方案.md` is the first authority; `docs/superpowers/specs/2026-08-11-w3-mcp-api-trace-design.md` is the approved slice design.
- Every provider call goes through `PolicyBoundTransport`; no raw provider adapter escapes that module.
- Planning sends query plus authored tool schemas only; it sends no TOC, clause, candidate, or excerpt text.
- Plans have at most 4 steps and 6 total MCP calls, including a retry.
- All MCP tools are local, read-only, Pydantic-typed, and return stable public errors.
- Do not call a real provider, read restricted corpus artifacts into a test record, mutate `specpilot_live`, or apply migration 004 outside a throwaway database.
- Use TDD: capture RED before implementation, run the focused GREEN, then commit only the task's files.
- Before Task 5, recreate the exact throwaway database with `dropdb --if-exists specpilot_w3_mcp_test` followed by `createdb specpilot_w3_mcp_test`; never substitute `specpilot_live`.

---

## File map

- `src/specpilot/contracts/egress.py` — planning stage and payload union.
- `src/specpilot/egress/policies/default-v1.json` and `src/specpilot/egress/policy.py` — explicit planning allowlist and query cap.
- `src/specpilot/providers/http.py` and `src/specpilot/providers/fake.py` — render deterministic planning prompts/replies without native tool-call retention.
- `src/specpilot/agents/contracts.py` — `ToolPlan`, discriminated tool arguments, results, and sanitized call summaries.
- `src/specpilot/agents/planner.py` — build/send/parse the planning request.
- `src/specpilot/agents/evidence.py` — validate dependencies, enforce call budget, execute MCP, and build evidence.
- `src/specpilot/mcp_server/contracts.py` — five Pydantic tool request/response models.
- `src/specpilot/mcp_server/services.py` — read-only local implementations.
- `src/specpilot/mcp_server/server.py` — FastMCP registration.
- `src/specpilot/mcp_server/client.py` — narrow client protocol and Streamable HTTP implementation.
- `src/specpilot/mcp_server/app.py` — health plus MCP ASGI lifespan.

### Task 1: Planning egress contract and policy

**Files:**
- Modify: `src/specpilot/contracts/egress.py`
- Modify: `src/specpilot/egress/policy.py`
- Modify: `src/specpilot/egress/enforcer.py`
- Modify: `src/specpilot/egress/policies/default-v1.json`
- Test: `tests/unit/egress/test_planning_projection.py`
- Test: `tests/unit/egress/test_policy_projection.py`

**Interfaces:**
- Produces: `EgressStage.PLANNING`, `ToolSchema`, `L1PlanPayload`, and existing `EgressRequest` support for `kind="l1_plan"`.
- Preserves: `L1OnlinePayload` and all existing cap arithmetic.

- [ ] **Step 1: Write the planning projection RED**

```python
def test_planning_payload_discloses_query_and_no_source_text():
    request = planning_request(query="When may a sender retry?")
    reservation = fixture_enforcer().prepare(request, FixtureTokenCounter())
    assert request.stage is EgressStage.PLANNING
    assert request.payload.kind == "l1_plan"
    assert reservation.disclosures == ()
    assert request.payload.max_steps == 4
    assert request.payload.max_tool_calls == 6
```

- [ ] **Step 2: Run the RED**

Run: `.venv/bin/python -m pytest tests/unit/egress/test_planning_projection.py -q`

Expected: collection/import failure because `L1PlanPayload` and `PLANNING` do not exist.

- [ ] **Step 3: Add the minimal typed contract**

```python
class EgressStage(StrEnum):
    PLANNING = "planning"
    EVIDENCE = "evidence"
    COMPLIANCE = "compliance"
    VERIFIER = "verifier"
    JUDGE = "judge"

class ToolSchema(_FrozenModel):
    name: Identifier
    description: ShortText
    input_schema: dict[str, object]

class L1PlanPayload(_FrozenModel):
    kind: Literal["l1_plan"] = "l1_plan"
    query: ShortText
    version: VersionMetadata
    tool_catalog_version: Identifier
    tool_catalog_hash: Sha256
    tools: Annotated[tuple[ToolSchema, ...], Field(min_length=5, max_length=5)]
    max_steps: Literal[4] = 4
    max_tool_calls: Literal[6] = 6
```

Add `l1_plan_tokens: 1024`, allow only `l1_plan` at `planning`, and make policy projection treat `l1_plan` like `l1_query` for projected query tokens while deriving zero disclosures.

- [ ] **Step 4: Prove policy mismatch and all existing projections**

Run: `.venv/bin/python -m pytest tests/unit/egress/test_planning_projection.py tests/unit/egress/test_policy_projection.py tests/unit/egress/test_disclosure_caps.py -q`

Expected: PASS; a reservation against an old policy hash still raises `policy_snapshot_mismatch`.

- [ ] **Step 5: Commit**

```bash
git add src/specpilot/contracts/egress.py src/specpilot/egress/policy.py src/specpilot/egress/enforcer.py src/specpilot/egress/policies/default-v1.json tests/unit/egress/test_planning_projection.py tests/unit/egress/test_policy_projection.py
git commit -m "feat: define ledger-bound L1 planning payload"
```

### Task 2: Typed tool plan and validation

**Files:**
- Create: `src/specpilot/agents/__init__.py`
- Create: `src/specpilot/agents/contracts.py`
- Test: `tests/unit/agents/test_tool_plan.py`

**Interfaces:**
- Produces: `ToolPlan.model_validate_json`, `validate_tool_plan(plan) -> ToolPlan`, `ToolCallSummary`.
- Consumes: opaque prior-step result IDs only; no free-form output interpolation.

- [ ] **Step 1: Write REDs for bounds and dependency direction**

```python
def test_plan_rejects_forward_dependency():
    with pytest.raises(ValueError, match="prior step"):
        ToolPlan.model_validate({"plan_id": "p1", "steps": [
            {"step_id": "a", "tool": "get_clause", "args": {
                "corpus_manifest_id": FIXTURE_CORPUS_ID,
                "document_id": "synthetic-fixture-spec",
                "clauses": {"kind": "step_result", "step_id": "b", "take": 1},
            }, "depends_on": ["b"]},
            {"step_id": "b", "tool": "search_clauses", "args": {
                "query": "retry",
                "corpus_manifest_id": FIXTURE_CORPUS_ID,
                "document_ids": ["synthetic-fixture-spec"],
                "normative_levels": [],
                "limit": 5,
            }, "depends_on": []},
        ]})

def test_plan_rejects_more_than_six_expanded_calls():
    with pytest.raises(ValueError, match="six calls"):
        validate_tool_plan(plan_with_call_cost(7))
```

- [ ] **Step 2: Run the RED**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_plan.py -q`

Expected: FAIL because `specpilot.agents.contracts` does not exist.

- [ ] **Step 3: Implement discriminated arguments**

Define frozen `SearchClausesArgs`, `GetClauseArgs`, `GetTocArgs`, `ExpandReferencesArgs`, and `LookupTermArgs`; define a discriminated `ToolStep` union keyed by `tool`; require 1–4 unique step IDs. A clause-consuming step uses either `DirectClauseIds(kind="direct", clause_ids=...)` or `StepResultRef(kind="step_result", step_id=..., take=1..3)`. Validate every result reference names the same prior step listed in `depends_on`; reject forward references and cycles. Expand `take` into the plan's base call cost and reject more than six. At runtime increment the same counter before every attempt; one retry is allowed only when the counter remains below six, so a six-call base plan has no retry allowance.

```python
class ToolPlan(_FrozenModel):
    plan_id: Identifier
    steps: Annotated[tuple[ToolStep, ...], Field(min_length=1, max_length=4)]

class ToolCallSummary(_FrozenModel):
    step_id: Identifier
    tool: ToolName
    argument_keys: tuple[Identifier, ...]
    result_count: int
    duration_ms: int
    retry_count: int
    error_code: Identifier | None = None

class StepResultRef(_FrozenModel):
    kind: Literal["step_result"] = "step_result"
    step_id: Identifier
    take: Annotated[int, Field(ge=1, le=3)]
```

- [ ] **Step 4: Run contract tests and strict typing**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_plan.py -q && .venv/bin/python -m mypy src/specpilot/agents`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/specpilot/agents tests/unit/agents/test_tool_plan.py
git commit -m "feat: validate bounded typed tool plans"
```

### Task 3: Five read-only local services

**Files:**
- Create: `src/specpilot/mcp_server/contracts.py`
- Create: `src/specpilot/mcp_server/services.py`
- Modify: `src/specpilot/retrieval/local.py`
- Test: `tests/unit/mcp_server/test_services.py`

**Interfaces:**
- Produces: `McpToolServices.search_clauses`, `get_clause`, `get_toc`, `expand_references`, `lookup_term`.
- Consumes: `LocalCorpus`, a `SearchBackend` protocol, and precomputed reference/term maps.

- [ ] **Step 1: Write a RED proving exact reads, bounds, and no mutation surface**

```python
def test_services_are_read_only_and_return_typed_results(tool_services):
    before = tool_services.inventory_hash()
    result = tool_services.search_clauses(SearchClausesRequest(
        query="retry",
        corpus_manifest_id=FIXTURE_CORPUS_ID,
        document_ids=("synthetic-fixture-spec",),
        normative_levels=("MUST", "SHOULD"),
        limit=3,
    ))
    clause = tool_services.get_clause(GetClauseRequest(
        corpus_manifest_id=FIXTURE_CORPUS_ID,
        document_id="synthetic-fixture-spec",
        clause_id=result.hits[0].clause_id,
    ))
    assert len(result.hits) <= 3
    assert clause.content_hash == sha256(clause.text.encode()).hexdigest()
    assert tool_services.inventory_hash() == before
    assert not any(name.startswith(("put_", "add_", "delete_", "update_")) for name in public_methods(tool_services))
```

- [ ] **Step 2: Run the RED**

Run: `.venv/bin/python -m pytest tests/unit/mcp_server/test_services.py -q`

Expected: FAIL because the contracts and service do not exist.

- [ ] **Step 3: Implement the service boundary**

Every request carries `corpus_manifest_id`; document-scoped tools also carry an explicit `document_id` or bounded `document_ids`. `search_clauses` accepts a bounded normative-level filter and `limit` 1–20. `get_clause` requires the manifest/document/clause triple. `get_toc` has limit 1–12. `expand_references` traverses exactly one hop and returns at most 3 new clause IDs. `lookup_term` accepts 1–128 characters and returns definition/source clause IDs. Return clause text only from `get_clause`; search results return locator/hash/score metadata, not bodies. Raise `McpToolError(code, field, correction)` with closed codes `invalid_argument`, `not_found`, `invalid_reference`, `tool_timeout`, and `backend_unavailable`; never include a task, clause, or stack trace in the error.

- [ ] **Step 4: Prove deterministic order and error sanitization**

Run: `.venv/bin/python -m pytest tests/unit/mcp_server/test_services.py tests/unit/retrieval -q`

Expected: PASS; two identical searches have byte-identical model dumps.

- [ ] **Step 5: Commit**

```bash
git add src/specpilot/mcp_server/contracts.py src/specpilot/mcp_server/services.py src/specpilot/retrieval/local.py tests/unit/mcp_server/test_services.py
git commit -m "feat: add five read-only corpus tool services"
```

### Task 4: FastMCP server and protocol client

**Files:**
- Modify: `pyproject.toml`
- Create: `src/specpilot/mcp_server/server.py`
- Create: `src/specpilot/mcp_server/client.py`
- Modify: `src/specpilot/mcp_server/app.py`
- Test: `tests/unit/mcp_server/test_server.py`
- Test: `tests/integration/mcp_server/test_streamable_http.py`

**Interfaces:**
- Produces: `create_mcp_server(services) -> FastMCP`, `McpEvidenceClient` protocol, `StreamableMcpClient`.

- [ ] **Step 1: Add protocol RED for all five tools**

```python
@pytest.mark.anyio
async def test_all_tools_round_trip_over_streamable_http(mcp_asgi_client):
    listed = await mcp_asgi_client.list_tools()
    assert {tool.name for tool in listed.tools} == {
        "search_clauses", "get_clause", "get_toc", "expand_references", "lookup_term"
    }
    result = await mcp_asgi_client.call_tool("search_clauses", {
        "query": "retry",
        "corpus_manifest_id": FIXTURE_CORPUS_ID,
        "document_ids": ["synthetic-fixture-spec"],
        "normative_levels": [],
        "limit": 5,
    })
    assert result.isError is False
```

- [ ] **Step 2: Run the RED**

Run: `.venv/bin/python -m pytest tests/unit/mcp_server/test_server.py tests/integration/mcp_server/test_streamable_http.py -q`

Expected: FAIL because the MCP dependency/server is absent.

- [ ] **Step 3: Add and mount FastMCP**

Add `mcp>=1.26,<2` to project dependencies. Construct `FastMCP("SpecPilot", stateless_http=True, json_response=True)`, register five async wrappers that validate input and call `McpToolServices`, expose `/health`, and run `mcp.session_manager.run()` from the host ASGI lifespan. Configure host/origin protection explicitly for loopback tests.

- [ ] **Step 4: Run unit plus real protocol GREEN**

Run: `.venv/bin/python -m pytest tests/unit/mcp_server tests/integration/mcp_server -q`

Expected: PASS with no skipped MCP protocol test.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/specpilot/mcp_server tests/unit/mcp_server tests/integration/mcp_server
git commit -m "feat: serve corpus tools over Streamable HTTP MCP"
```

### Task 5: Ledger-bound planner and Evidence Agent

**Files:**
- Modify: `src/specpilot/providers/http.py`
- Modify: `src/specpilot/providers/fake.py`
- Modify: `src/specpilot/providers/transport.py`
- Modify: `src/specpilot/answer/run.py`
- Modify: `src/specpilot/cli.py`
- Create: `src/specpilot/agents/planner.py`
- Create: `src/specpilot/agents/evidence.py`
- Test: `tests/unit/providers/test_http_adapter.py`
- Test: `tests/unit/agents/test_planner.py`
- Test: `tests/unit/agents/test_evidence_agent.py`
- Test: `tests/integration/agents/test_planning_ledger_flow.py`

**Interfaces:**
- Produces: `TransportReceipt`, `ProviderAttemptError`, `Planner.plan(question, context) -> ToolPlan`, `EvidenceAgent.collect(plan, corpus_manifest_id) -> EvidenceResult`.
- `EvidenceResult` is a frozen dataclass with
  `evidence: tuple[Evidence, ...]` and
  `calls: tuple[ToolCallSummary, ...]`.
- Preserves: `AnswerOutcome.provider_error`, reservation ID, replay flag, request size, and verdict while removing direct adapter access from `run_answer`.

- [ ] **Step 1: Write REDs for no-bypass planning and invalid-plan no-call**

```python
@pytest.mark.anyio
async def test_planner_uses_transport_and_invalid_plan_calls_no_tools(planner_fixture):
    planner_fixture.provider.reply = "not-json"
    with pytest.raises(InvalidToolPlan):
        await planner_fixture.planner.plan("When may it retry?", planner_fixture.context)
    assert planner_fixture.ledger.reservation_count == 1
    assert planner_fixture.mcp.call_count == 0

@pytest.mark.anyio
async def test_answer_path_has_one_outward_transport(answer_fixture):
    outcome = await run_answer(
        answer_fixture.question,
        answer_fixture.evidence,
        transport=answer_fixture.transport,
        source_manifest=answer_fixture.source_manifest,
        corpus_manifest_id=answer_fixture.corpus_manifest_id,
        evaluation_root_id="root-1",
        run_id="run-1",
        model_id="fixture-model-v1",
    )
    assert outcome.reservation_id is not None
    assert answer_fixture.provider.call_count == 1
```

- [ ] **Step 2: Run the RED**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_planner.py tests/unit/agents/test_evidence_agent.py -q`

Expected: FAIL because planner and Evidence Agent do not exist.

- [ ] **Step 3: Implement planner rendering and bounded execution**

First make `PolicyBoundTransport.send` return a frozen `TransportReceipt(response, reservation_id, replayed, request_size)`. When an adapter raises, record the attempt and raise `ProviderAttemptError(public_error_code, reservation_id, replayed)` so callers retain the stable provider code and reservation identity without receiving the adapter. Refactor `run_answer` and the CLI answer path to accept only the transport plus explicit model ID; remove their direct `adapter.send`, `enforcer.prepare`, and `ledger.check_and_reserve` calls.

Then render a planning-specific system message only for `L1PlanPayload`; parse `receipt.response.content` with `ToolPlan.model_validate_json`; never read `message.tool_calls`. Execute dependencies in plan order, retry only `tool_timeout` once, increment budget before each attempt, convert selected clause IDs to `build_evidence_from_unit`, and retain no result text in `ToolCallSummary`.

- [ ] **Step 4: Prove fresh-PostgreSQL reservation and focused GREEN**

Run: `SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_mcp_test .venv/bin/python -m pytest tests/unit/agents tests/unit/answer/test_run.py tests/unit/providers tests/integration/agents/test_planning_ledger_flow.py tests/integration/providers/test_transport_ledger_flow.py -q`

Expected: PASS; the integration test shows one planning reservation/attempt and no disclosure rows for that reservation.

- [ ] **Step 5: Commit**

```bash
git add src/specpilot/agents src/specpilot/providers src/specpilot/answer/run.py src/specpilot/cli.py tests/unit/agents tests/unit/answer/test_run.py tests/unit/providers tests/integration/agents tests/integration/providers/test_transport_ledger_flow.py
git commit -m "feat: execute model-authored plans through MCP"
```

### Task 6: Part 1 verification gate

- [ ] **Step 1: Run focused tests**

Run: `.venv/bin/python -m pytest tests/unit/agents tests/unit/mcp_server tests/unit/egress/test_planning_projection.py tests/integration/mcp_server tests/integration/agents -q`

- [ ] **Step 2: Run static checks**

Run: `.venv/bin/python -m ruff check src/specpilot/agents src/specpilot/mcp_server src/specpilot/contracts/egress.py src/specpilot/providers tests/unit/agents tests/unit/mcp_server && .venv/bin/python -m mypy src`

- [ ] **Step 3: Inspect the diff for outbound bypasses and plaintext**

Run: `rg -n "httpx\.|AsyncClient\(|\.send\(" src/specpilot/agents src/specpilot/mcp_server && rg -n "query|excerpt|candidate|secret|authorization" src/specpilot/agents src/specpilot/mcp_server`

Expected: provider HTTP remains confined to the existing adapter; trace summaries have no text-bearing field.

- [ ] **Step 4: Record the gate**

Create `docs/reports/w3-part1-mcp-orchestration.md` with commands, exact pass counts, database identity, policy hash transition, and limitations; include no source text or credentials.

- [ ] **Step 5: Commit**

```bash
git add docs/reports/w3-part1-mcp-orchestration.md
git commit -m "docs: record W3 MCP orchestration evidence"
```

- [ ] **Step 6: Remove the throwaway database**

Run: `dropdb --if-exists specpilot_w3_mcp_test`

Expected: `psql -lqt` contains no `specpilot_w3_mcp_test` row.
