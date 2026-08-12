# W3 MCP, L1 API, and Trace Page Design

**Date:** 2026-08-11

**Status:** approved

**Authority:** `SpecPilot_项目方案.md` first, the master roadmap second, and the
2026-08-11 handoff third. Where the roadmap's old week gate conflicts with the
product plan's dated scheduling revision, the product plan governs.

## Goal

Turn the MCP and FastAPI stubs into one demonstrable L1 loop: a model produces
a bounded typed tool plan, an Evidence Agent executes five read-only MCP tools
against the frozen local corpus, the existing disclosure ledger and verifier
govern the answer call, and an owner-authenticated React page renders the
sanitized run trace.

This slice makes the project's differentiators visible without pulling W5's
SSE protocol or W4's checkpoint recovery into W3.

## Scope and schedule correction

The master roadmap retains its original weekly text as a design record, but a
dated revision changes how it is read:

- W3 through W5 are parallel work packages and acceptance checklists, not
  sequential calendar gates.
- This W3 slice contains MCP, Orchestrator/Evidence, FastAPI, basic trace data,
  minimal session ownership, and the read-only part of the React trace page.
- SSE, reconnect semantics, and SSE-specific credential transport remain W5.
- Run ownership is not an early W4 feature. Product-plan section 9 binds it to
  `GET /runs/{run_id}`, so the endpoint and ownership check arrive together.
- The page is intentionally moved ahead of the old pruning order because an
  invisible disclosure block, citation decision, or provider failure cannot
  demonstrate the system's central claim.

This slice does not implement L2, Compliance Agent, semantic recovery,
LangGraph checkpoints, evaluation HTTP endpoints, the four W5 demo scenarios,
or a real-provider acceptance run. L2-adv construction and judge calibration
remain independent W3 checklist items, not hidden prerequisites of this API
slice.

## Selected architecture

The selected design is a bounded model-authored `ToolPlan`, followed by local
MCP execution and the existing L1 answer chain.

The planner is genuinely model-driven: it chooses among the five tools and
supplies their typed arguments. It is not native provider tool-calling. The
current provider adapter only counts `tool_calls` and deliberately discards
their arguments, so treating that probe as an agent loop would overclaim what
the route implements. A JSON `ToolPlan` keeps the provider boundary small,
auditable, and compatible with the existing `ProviderResponse.content`
contract.

Two alternatives were rejected:

1. A fixed local retrieval recipe renamed as an Orchestrator would exercise
   MCP transport but would not demonstrate autonomous tool selection.
2. A native multi-turn provider tool loop would require retaining provider tool
   arguments, returning tool results to the provider, and defining a second
   disclosure projection for every round. That is more surface than the W3
   acceptance claim requires.

## End-to-end flow

1. The client obtains or presents a short-lived session credential.
2. `POST /chat` validates the credential, request identifiers, task level,
   manifest binding, and configured route without calling a provider.
3. The API inserts a sanitized `queued` run row, places the question only in an
   in-process queue, and returns `202 Accepted` with `run_id`.
4. A worker claims the run, writes `running`, and starts refreshing its lease.
5. The Orchestrator builds an `l1_plan` egress request containing the question,
   the versioned tool catalog, and hard plan limits. It sends that request only
   through `PolicyBoundTransport`, so planning receives an atomic reservation
   and an attempt record like every other provider call.
6. The model returns one `ToolPlan` with no more than four steps. Pydantic
   validation rejects unknown tools, extra fields, invalid argument bounds,
   forward references, and plans whose possible execution exceeds six calls.
7. The Evidence Agent invokes the plan through an MCP client. Tool results stay
   local. Only opaque candidate/evidence identifiers, hashes, counts, score
   summaries, durations, and stable errors enter the run trace.
8. Selected local units become the existing `Evidence` objects. The answer
   call reuses the current `run_answer` disclosure reservation, provider send,
   response parser, and deterministic citation verifier.
9. The worker maps the outcome to one terminal run state and stops the lease.
10. The React page polls `GET /runs/{run_id}` until a terminal state or a
    client-side polling deadline.

The question is never stored in the run database. W3 has no durable checkpoint
capable of safely resuming it, so a process loss produces `interrupted` rather
than a silent provider retry and second reservation.

## Planning egress contract

Add `EgressStage.PLANNING` and an `L1PlanPayload` with kind `l1_plan`. The
payload contains:

- `query`;
- the same frozen `VersionMetadata` required by online answer payloads;
- a version/hash for an authored tool catalog;
- the allowed tool names and their bounded JSON schemas;
- `max_steps=4` and `max_tool_calls=6`.

It carries no TOC nodes, evidence excerpts, candidate text, clause text, or
local paths. The existing L1 projected-query token cap applies to its query.
Its reservation charges the planning transmission and provider attempt but
adds no disclosure facts because no source excerpt leaves the machine.

The packaged egress policy explicitly allows only `l1_plan` at `planning`.
Changing the policy creates a new policy hash and therefore follows the Task 11
successor/rebind procedure; it is not an in-place reinterpretation of an old
ledger epoch. The implementation and tests must use a fresh corpus ledger or
perform the explicit rebind in a throwaway database. `specpilot_live` is not
automatically migrated or rebound.

The HTTP adapter renders a planner-specific system contract and reads the plan
from ordinary response content. Provider errors retain their existing stable
public codes. Malformed plan content produces `refused` with
`invalid_tool_plan`; it is not presented as evidence insufficiency and it does
not execute guessed tools.

## Tool plan contract

`ToolPlan` is frozen and forbids extra fields. It contains a plan ID and an
ordered tuple of at most four steps. Every step contains:

- a local step ID;
- exactly one of the five allowed tool names;
- that tool's discriminated Pydantic argument model;
- zero or more prior step IDs whose outputs may supply opaque IDs.

Dependencies must point backward, the graph must be acyclic, and the expanded
maximum call count must not exceed six. Tool output is never inserted into a
subsequent free-form string template. A dependent step may consume only typed
opaque IDs from prior results.

## MCP boundary

Use the official Python MCP SDK's `FastMCP` server with Streamable HTTP,
stateless HTTP, and JSON responses. The ASGI application owns the MCP session
manager in its lifespan. The runtime endpoint is `/mcp`; tests use an in-memory
ASGI transport while still exercising the real MCP protocol and serialization.

The five tools are:

1. `search_clauses` — bounded hybrid retrieval with stable ordering and a
   result limit.
2. `get_clause` — exact local lookup by opaque clause ID.
3. `get_toc` — bounded section-tree lookup.
4. `expand_references` — bounded traversal of recorded local references.
5. `lookup_term` — bounded local definition/term lookup.

Each tool has a Pydantic input and output contract, is read-only, and exposes
only stable public errors. Calls time out and may retry once only when the
failure is classified retryable. The per-run budget counts the retry as a
call, so the worker cannot turn a timeout loop into unbounded work.

Business logic lives in local services behind the MCP wrappers. The Evidence
Agent calls the MCP client surface rather than importing those services, which
proves the protocol boundary. A future local HTTP gateway may reuse the same
services without copying tool behavior.

The MCP route is an internal local boundary. It is not a public corpus API and
does not share the browser's run endpoint. Non-loopback deployments must use an
explicit allowed-host/origin configuration rather than weakening the SDK's DNS
rebinding protection.

## Run persistence and sanitized trace

> **[已变更｜2026-08-12｜迁移编号顺延]** Part 1 的真实 PostgreSQL RED 证明已发布 migration 001 的 stage CHECK 不接受 `planning`。因此追加 migration 004 扩展该精确闭集，不改写历史迁移；下述 run/trace 两表迁移顺延为 `005`，其设计语义不变。

Migration `005` adds two normalized tables.

`specpilot_run` stores:

- `run_id`, `request_id`, and owner `session_id`;
- task/profile plus corpus, source, policy, configuration, prompt, provider,
  and model identifiers;
- persisted state, terminal reason, created/started/completed timestamps;
- `lease_owner`, `lease_expires_at`, and last heartbeat;
- query hash, never query text.

`specpilot_run_event` is an ordered append-only stream keyed by run and event
sequence. A discriminated Pydantic event union permits only:

- state transitions;
- plan and Agent-step summaries;
- tool name, sanitized argument summary, count, duration, retry, and error;
- candidate IDs and scores without candidate text;
- evidence IDs/hashes and reservation/ledger IDs;
- token/cost summaries and verifier checks;
- terminal reason.

Events are validated before insertion. There is no generic event dictionary
that can accidentally accept query text, excerpts, provider responses, local
paths, headers, secrets, or raw exception messages.

The run store enforces monotonic transitions and owner-scoped reads. Terminal
rows cannot return to `running`. Event append and the associated state change
use one transaction where both occur.

## State model

Persisted states are:

- `queued`
- `running`
- `answered`
- `refused`
- `egress_blocked`
- `failed`
- `interrupted`

Outcome projection has an explicit precedence:

1. If `AnswerOutcome.provider_error` is non-null, state is `failed` and reason
   is that stable provider code. The verifier verdict is ignored for state
   selection.
2. An enforcer or ledger admission rejection is `egress_blocked`, with its
   stable code such as `root_unique_excerpts_exceeded`,
   `excerpt_bytes_exceeded`, or `policy_snapshot_mismatch`.
3. A normal answered verdict is `answered`.
4. A normal refusal verdict is `refused`, preserving distinctions including
   `no_evidence_retrieved`, `evidence_insufficient`,
   `unverifiable_citation`, and `invalid_tool_plan`.

This prevents a provider timeout from appearing as the project's deliberate
evidence-based refusal, and prevents a successful disclosure block from
appearing as broken infrastructure.

`interrupted` cannot depend on a dying process writing its own epitaph. The
worker refreshes a bounded lease. Reads project an expired `queued` or
`running` lease as effective state `interrupted` immediately. Startup
reconciliation later persists that terminal state with a compare-and-set
update. Neither path resubmits the provider request.

## Session and ownership boundary

`POST /chat` and `GET /runs/{run_id}` accept the session credential through the
Authorization header or an HTTP-only cookie, never the URL. Tokens contain only
session ID, profile, audience, issued/expiry times, and a nonce/version. They
are signed and have a short lifetime.

For deterministic tests and localhost fixture demos, a fixed-clock issuer may
mint a fixture-only credential. In a real profile, an already issued bearer is
required. The signing secret comes from an environment variable or secret
provider and never appears in ordinary configuration, logs, traces, errors, or
reports. W3 does not build accounts, users, password storage, refresh tokens,
or an authorization administration UI.

Unknown run IDs and runs owned by another session return the same public
not-found shape so the endpoint does not become an ownership oracle.

## FastAPI lifecycle

The FastAPI lifespan owns:

- the PostgreSQL run store;
- the in-process bounded work queue and worker task group;
- the MCP server session manager and MCP client transport;
- the provider transport and close hooks;
- startup reconciliation for expired leases.

`POST /chat` persists the run before enqueueing. If enqueue fails, it closes the
new run with a stable failure without calling a provider. Backpressure returns
a stable service-unavailable response before a run is created when queue
capacity is already exhausted.

`GET /health` reports only coarse service/dependency states. It must not expose
provider names, model names, manifest contents, paths, connection strings, or
secrets.

## React trace page

Create one minimal React application, built to static assets and served by the
FastAPI package. It provides:

- a small L1 question form;
- run ID and status;
- a polling trace timeline for Agent and tool events;
- ledger/reservation, evidence-hash, and verifier summaries;
- visibly distinct treatments for `refused`, `egress_blocked`, `failed`, and
  `interrupted`;
- a bounded polling deadline and manual retry of the read request.

Polling stops on every terminal state. A client polling timeout is rendered as
a client connection condition and never overwrites the server's run state.
The page displays no full corpus clause or excerpt in this slice. SSE,
EventSource reconnect, workflow editing, user administration, and a general
dashboard remain out of scope.

## Error and retry rules

- MCP retry is at most once for a classified transient local/tool timeout and
  consumes the six-call budget.
- Provider sends are never silently retried by the run worker. A new attempt
  requires a new explicit run and a new reservation.
- Planning admission failures and answer admission failures are both
  `egress_blocked`, with the exact stable gate code.
- Provider faults are `failed`; their raw exception/body is discarded.
- Invalid model plans are safe refusals and produce no MCP calls.
- Empty retrieval is `refused/no_evidence_retrieved`.
- A model that sees evidence and declines is
  `refused/evidence_insufficient`.
- Citation/parser failure retains its precise refusal reason and faults in the
  sanitized verifier event.

## Test and release evidence

Development is test-first. The acceptance matrix includes:

- unit tests for every MCP input/output model, read-only service, stable error,
  planner contract, dependency rule, call budget, run transition, state
  precedence, session token, and trace allowlist;
- protocol tests that drive all five tools through an actual Streamable HTTP
  MCP ASGI app and client;
- PostgreSQL migration, ownership, append ordering, lease, startup
  reconciliation, concurrent claim, and terminal-state tests against fresh
  isolated databases;
- API tests for `202`, queue backpressure, bearer/cookie handling, expiration,
  cross-session non-disclosure, sanitized health, and all terminal states;
- explicit regressions proving provider timeout is `failed`, the three named
  gate errors are `egress_blocked`, and an expired lease is observed as
  `interrupted` without an infinite poll;
- trace scans proving no query, excerpt, candidate body, secret, credential,
  local path, or raw provider response is stored or returned;
- React component and browser-flow tests for polling, distinct terminal
  rendering, stop conditions, and the client deadline;
- a deterministic fake-provider end-to-end run through FastAPI, planner,
  Streamable HTTP MCP, retrieval, real PostgreSQL ledger, verifier, and page;
- the full Ruff, strict mypy, unit, fresh PostgreSQL plus isolated Qdrant
  integration, fixture-smoke, frontend build, wheel/sdist, and installed-package
  checks with zero unexplained skips.

No test or documentation step may call a real provider, disclose real corpus
text, mutate `specpilot_live`, or commit restricted fixture artifacts.

## Operational boundary

Migrations `004` and `005` are tested on throwaway databases and shipped with explicit
operator instructions. Applying it to `specpilot_live`, rebinding the packaged
policy after the new planning stage, and making any real provider call remain
separate owner-controlled operations.

Before any push, run `git status --ignored --short` and confirm that
`artifacts/restricted/`, `manifests/local/`, `data/`, and `tmp/` remain ignored
and untracked from the commit. No push is treated as a backup mechanism.
