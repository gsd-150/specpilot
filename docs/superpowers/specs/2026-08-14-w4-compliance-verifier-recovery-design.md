# W4 Compliance, Verifier, and Recovery Design

## Status

Approved in conversation on 2026-08-14. This document defines the W4 product
slice that follows the completed W3 owner-bound asynchronous L1 run path.

## Goal

Add an L2 Compliance Agent, a deterministic evidence-integrity verifier, an
independent semantic support gate, exactly one directed retrieval recovery, and
a minimal sanitized checkpoint that an owning client can resume after process
loss without resetting either the tool budget or the egress ledger.

The output invariant is unchanged: an API response may expose a determinate
`compliant` or `violating` verdict only after every deterministic check and the
semantic support gate pass. Every other path produces `insufficient_evidence`
or a fail-closed run state.

## Scope

### Included

- `task_type=compliance` API requests and an L2 maximum of three atomic claims;
- structured pre-Verifier candidates from an independent Compliance Agent;
- frozen-corpus deterministic checks for every cited Evidence item;
- a separately prompted and separately metered semantic support call;
- one directed retrieval recovery per run, followed by the complete verifier;
- a PostgreSQL checkpoint containing only sanitized, bounded state;
- an owner-bound client-assisted resume endpoint;
- fixture and integration coverage across the complete L2 path.

### Excluded

- unattended server-side restart of a question;
- durable plaintext or encrypted storage of the original question or claim
  prose;
- SSE and reconnect credentials, which remain W5;
- automatic judge calibration, dev-set quality numbers, locked evaluation, and
  gate-only experiment artifacts;
- retrying arbitrary provider, parser, or policy failures;
- more than one directed retrieval recovery;
- dynamic Agent registries or a general-purpose workflow engine.

The existing AnyIO worker and PostgreSQL state machine remain the runtime. W4
does not add LangGraph merely to acquire its checkpoint vocabulary; the required
semantics are small enough to express as a closed state contract and transactional
store.

## Existing Baseline

W3 already provides:

- owner-authenticated `POST /chat` and owner-scoped run reads;
- queued/running leases, heartbeats, append-only sanitized events, and terminal
  projection;
- an ephemeral in-memory question that is absent from PostgreSQL;
- a planner, Evidence Agent, L1 answer path, and the sole policy-bound provider
  transport;
- stable planning and answer idempotency keys derived from `run_id`;
- a citation check against the exact Evidence IDs disclosed in one request.

W3 intentionally turns an expired lease into `interrupted` and cannot restart
the work because it stores only `query_hash`. W4 extends this design; it does not
reinterpret an expired lease as permission to rerun provider calls.

## Architecture

The L2 online path is:

```text
request
  -> orchestrator plan
  -> Evidence Agent
  -> Compliance Agent (pre-Verifier candidates)
  -> deterministic Verifier
       -> pass --------------------------------------+
       -> fail -> one directed retrieval recovery --+-> full deterministic rerun
  -> semantic support gate
       -> pass -> publish candidate verdict
       -> fail -> one directed retrieval recovery, if unused
                   -> full deterministic rerun -> semantic rerun
       -> still fail -> insufficient_evidence
```

The recovery branch returns to evidence collection. It never jumps from a
replacement Evidence item directly to publication. A recovered candidate must
pass the same full deterministic and semantic gates as the initial candidate.

Each external model stage travels through `PolicyBoundTransport` under the same
`evaluation_root_id` and resolved policy. The new Compliance and Verifier calls
use the existing `compliance` and `verifier` egress stages. No caller receives a
raw provider adapter.

## Contracts

### API request and result

The create request gains an explicit task type with `qa` as the compatibility
default and `compliance` selecting L2. Compliance requests use the existing
question field for the user's design description and the same manifest/scope
binding as L1.

An L2 result contains one to three ordered atomic results. Each public result
contains:

- an opaque `claim_id` derived from the normalized claim bytes;
- `verdict` in `compliant`, `violating`, or `insufficient_evidence`;
- verified citations only when the verdict is determinate;
- a verification status and stable, sanitized reason code.

Claim and rationale prose remain ephemeral. They may be returned to the owning
client in the terminal response while the process is alive, but they are not
written to the trace or checkpoint. A resumed process reconstructs them by
rerunning the relevant local/model stage from the question the client resubmits.
No trace event stores claim text.

### Compliance candidate

The Compliance Agent returns a closed structured object with one to three
atomic candidates. Each model candidate contains:

- the normalized atomic claim; the server derives `claim_id` as SHA-256 of
  those normalized UTF-8 bytes and never trusts a model-supplied identifier;
- a proposed verdict in `compliant`, `violating`, or
  `insufficient_evidence`;
- one or more Evidence IDs for a determinate proposal, or none for an
  insufficient proposal;
- a bounded rationale.

The candidate is not a publishable verdict. A proposed `insufficient_evidence`
needs no semantic gate and remains insufficient. A proposed determinate verdict
cannot be converted into an API result until both verifier layers pass.

More than three candidates is a schema failure, not a fourth item to truncate.
The run refuses with a stable `too_many_atomic_claims` reason. A malformed
Compliance reply gets no directed retrieval recovery; recovery is for evidence
support, not for arbitrary structured-output retry.

### Semantic support decision

The independent Verifier receives exactly one atomic claim, its proposed
verdict, and the Evidence excerpts that survived deterministic verification. It
returns:

- `supports_verdict: bool`;
- ordered per-Evidence support decisions;
- one closed reason code;
- a bounded rationale used only in the in-memory result/debug boundary.

The semantic decision has its own prompt ID/hash, provider reservation, trace
stage, and test fixtures. It must not import or reuse the Compliance prompt or
treat a Compliance rationale as evidence. It is an evaluated model judgement,
not an assertion of formal correctness.

## Deterministic Verifier

The deterministic verifier runs locally and has a frozen corpus resolver. For
each Evidence ID named by a candidate it checks, in order:

1. the ID was disclosed to the Compliance call for this run and resolves to one
   unambiguous Evidence record;
2. the Evidence corpus manifest equals the run's corpus manifest;
3. the document is a member of that manifest and its document version matches;
4. the clause/unit ID, section locator, and document identity resolve together
   in the frozen corpus;
5. the exact reconstructed bytes hash to `content_hash` and `quote_hash`, and
   the recorded normalized span identifies those same bytes;
6. the Evidence document lies within the request's authorized specification
   scope;
7. a determinate candidate has at least one verified Evidence item.

The output is a tuple of per-check records with stable fault codes and a final
pass/fail value. It contains identifiers and hashes but no excerpt or claim
prose. Checks do not stop at the first bad Evidence item, so the trace can show
the bounded complete set of deterministic reasons; publication remains all or
nothing.

No semantic provider call occurs when a deterministic check fails. A failed
item either triggers the one permitted directed recovery or becomes
`insufficient_evidence`.

The current L1 disclosure citation check remains valid but is no longer called
the complete Verifier. Shared primitives may be extracted, but the L1 behavior
must remain backward compatible.

## Directed Recovery

### Bound

`recovery_attempted` is run-scoped and monotonic. At most one recovery action is
permitted across all atomic claims in one run. This conservative bound makes the
W4 phrase "one directed recovery" auditable, prevents three claims from silently
receiving three fresh search budgets, and leaves the existing root accounting
meaning intact.

Before the first recovery MCP call, the checkpoint advances to
`recovery_reserved`, sets `recovery_attempted`, and reserves the bounded maximum
tool cost for that directed action. If the process loses the returned tool
result before `recovery_completed`, resume never repeats MCP work: it publishes
a safe `recovery_result_lost` insufficient result and preserves the reserved
counter.

The recovery consumes the remaining L2 tool budget. Initial collection plus
recovery may use at most eight tool calls in total. It uses the same
`evaluation_root_id`, `run_id`, policy hash, corpus ledger, and provider routes.
It never creates a new disclosure budget.

### Direction table

| Failure family | Recovery action |
| --- | --- |
| no Evidence | one `search_clauses` using the atomic claim as the bounded query |
| Evidence ID, clause, span, or hash mismatch | one `get_clause` for the locally known clause identity; an invented identity falls back to scoped search |
| manifest, version, document, or scope mismatch | one `search_clauses` restricted to the run's manifest and authorized document scope |
| semantic `unsupported`, `condition_mismatch`, `exception_missing`, or `polarity_mismatch` | one bounded retrieval action selected from adjacent clause lookup or cross-reference expansion when a verified source clause exists, otherwise scoped search |

Only closed semantic reason codes reach the recovery selector. Verifier free
text, hidden chain of thought, unverified excerpts, and raw provider errors do
not feed another prompt or tool.

After recovery, Evidence is rebuilt from the frozen corpus, the candidate is
reconstructed if its Evidence set changes, and all deterministic and semantic
checks rerun. A second failure, lack of remaining tool budget, or an inapplicable
direction produces `insufficient_evidence`.

## Minimal Checkpoint

### Durable shape

There is at most one current checkpoint row per run plus append-only checkpoint
events in the existing trace. The row is compare-and-set by monotonically
increasing `checkpoint_version` and is written in the same transaction as the
event that announces the stage.

The checkpoint contains only:

- `run_id`, `attempt`, `checkpoint_version`, and stage;
- task level and request `query_hash`;
- source/corpus manifest, policy, configuration, prompt, provider, and model
  bindings already frozen on the run;
- plan ID and the validated typed plan, excluding query/claim prose in tool
  arguments; where a planned argument contains prose, only its hash is stored
  and the step must be reconstructed on resume;
- opaque Evidence IDs, clause/unit IDs, document IDs, content/quote hashes, and
  normalized spans, never excerpt bytes;
- used and remaining tool-call counts;
- planning, Compliance, Verifier, and answer reservation IDs that already
  exist;
- `recovery_attempted`, the recovery reason code, and completed claim IDs;
- completed `ComplianceResult` metadata: claim hash, verdict, verification
  status, verified citations, and stable reason code, never claim/rationale
  prose;
- timestamps needed for retention and recovery audit.

The allowed stages are:

1. `planned`
2. `evidence_collected`
3. `candidate_built`
4. `deterministic_verified`
5. `recovery_reserved`
6. `recovery_completed`
7. `semantic_verified`
8. `completed`

Stage transitions form a closed directed graph. `recovery_reserved` can be
reached only once and only from a failed deterministic or semantic stage; new
recovery executions pass through that durable reservation. Pre-migration
`recovery_completed` checkpoints remain resumable. A
checkpoint with an impossible stage/version/recovery combination is corrupt and
cannot be resumed.

The `completed` checkpoint is immediately compacted to the already sanitized
run metadata. Active checkpoints follow the product plan's seven-day
last-access retention. Retention cleanup is explicit and owner/run scoped; its
scheduler is outside W4, but the store exposes the bounded deletion operation.

### Why prose is absent

The original question, atomic claims, rationales, tool query strings, and
excerpt bytes remain absent from PostgreSQL. This preserves W3's disclosure and
privacy boundary. Consequently, W4 recovery is client-assisted rather than
automatic.

## Client-Assisted Resume

The owning client calls `POST /runs/{run_id}/resume` with:

- the session credential;
- the original question;
- a client-generated resume idempotency key.

The server performs all checks before reserving queue capacity or invoking a
provider:

1. owner/session matches the run;
2. the persisted run is interrupted because its lease expired, not terminal for
   another reason;
3. a checkpoint exists, validates, and belongs to this run;
4. the normalized resubmitted question hashes to the run's `query_hash`;
5. source/corpus/policy/configuration/prompt/provider/model bindings still match;
6. the egress ledger is readable and every recorded reservation has an
   unambiguous committed/failed state;
7. the supplied resume key is either new or an exact replay, and no other
   process attempt currently holds a live lease.

Success appends `resume_requested`, increments the attempt number, leases the
same `run_id`, and delivers a reconstructed in-memory job. The original
`evaluation_root_id` must therefore become a durable opaque checkpoint field;
it is not sensitive prose and is required to continue the same root budget.

Provider operations use keys derived from `(run_id, logical stage, claim_id,
recovery_attempt, reconstruction_generation)`. Within one process generation a
duplicate call reuses the same key and the transport refuses a second send. The
ledger intentionally stores no provider response, so a consumed reservation
cannot replay model output after process loss. If a checkpoint proves that a
model result existed but its prohibited prose was lost, resume increments the
stage's reconstruction generation and sends that stage again with a new key.
That transmission is charged in full to the same evaluation root, run, stage,
corpus and transmitted caps; it receives no fresh unique, tool or recovery
allowance. Locally reconstructible stages are never resent. An ambiguous
reservation or reconstruction generation fails closed.

Concretely, recovery from `planned` resends planning because the plan's query
arguments are not durable; recovery from `candidate_built` or
`deterministic_verified` resends Compliance because atomic claim prose is not
durable. Evidence references are reconstructed locally from the frozen corpus,
and completed `ComplianceResult` metadata is reused without another semantic
call. A reconstructed batch must reproduce the same server-derived claim IDs
for any completed claim; disagreement is integrity failure, not a new result.

A duplicate resume request with the same idempotency key returns the existing
attempt and never enqueues twice. A later process loss may be resumed with a new
key; it inherits the same checkpoint, tool usage, recovery flag, and ledger, so
repeated process failure never creates fresh work or disclosure allowance.
Wrong owner, wrong question, corrupt checkpoint, binding drift, or unreadable
ledger produces a sanitized refusal and zero provider calls.

## Run and Trace Semantics

`interrupted` remains the observable state of an expired process attempt. It is
not changed back in place. Resume appends a new state-transition/attempt event
for the same logical run; all history remains ordered and owner scoped.

New sanitized event kinds cover:

- checkpoint stage/version;
- Compliance candidate counts and opaque claim IDs;
- deterministic check type, Evidence ID, pass/fault code;
- semantic support pass/fault code;
- directed recovery type and remaining tool budget;
- resume request and attempt number.

Real-time audit batches use a stable, prose-free identity derived from opaque
claim ID, phase, generation and run attempt. An exact retry is idempotent while
identical verifier/tool facts from distinct claims or phases remain separate.
On resume, missing egress audit rows are reconciled from every settled ledger
reservation bound to the same run, evaluation root, policy and frozen runtime
bindings; checkpoint reservation IDs are not treated as the complete ledger.

Events contain no question, claim, rationale, tool query, excerpt, provider
response, or stack trace. Existing terminal distinctions remain: an egress gate
failure is `egress_blocked`, a provider failure is `failed`, and unsupported
evidence is a domain verdict rather than an exception.

The run store must distinguish a resumable interrupted attempt from a persisted
terminal interruption. Reconciliation seals the expired attempt in the event
history but leaves the logical run resume-eligible while a valid checkpoint and
resume allowance remain. Owner reads project the latest attempt and preserve
all preceding events.

## Failure Semantics

- Deterministic or semantic support failure after the recovery allowance is
  exhausted produces `insufficient_evidence` for that claim.
- If any claim cannot be published, other independently verified claims may be
  returned, but each failed claim remains explicit; the system never promotes a
  partial candidate's rationale into a verdict.
- More than three claims, malformed stage output, invalid checkpoint shape,
  binding drift, ledger uncertainty, and budget corruption fail closed with
  stable codes.
- Provider errors do not become `insufficient_evidence`; they retain the W3
  `failed/provider_error` semantics.
- Policy denials retain `egress_blocked` and never trigger retrieval recovery.
- Resume validation failures occur before job delivery and make no provider or
  MCP call.

## Persistence and Migration

A new forward-only migration adds:

- the sanitized checkpoint table with a strict stage/checkpoint-version
  contract and one current row per run;
- logical attempt and resume-idempotency records;
- the new closed event shapes/check constraints;
- any L2 terminal result metadata required for owner reads, limited to opaque
  IDs, verdicts, citations, and reason codes.

Migration fixtures continue applying every migration by filename on a fresh
database. No existing W3 row is rewritten. Pre-W4 interrupted runs have no
checkpoint and are not resumable.

## Testing Strategy

Implementation follows red-green-refactor. Tests cross component joins rather
than proving only isolated Pydantic objects.

### Contract and unit tests

- reject zero/four claims and determinate candidates without Evidence;
- accept at most three ordered atomic candidates;
- prove every deterministic fault code against a real frozen fixture resolver;
- prove the semantic gate cannot run after deterministic failure;
- prove a semantic failure cannot publish a determinate verdict;
- prove the recovery selector maps only closed reasons to bounded tools;
- prove one recovery is run-scoped and tool usage cannot exceed eight;
- prove recovery reruns the complete verifier;
- reject illegal checkpoint transitions and any prose-bearing checkpoint field;
- preserve all existing L1 citation behavior.

### Store and process-loss integration tests

On a fresh PostgreSQL database, stop the worker after each checkpoint stage and
then submit an owner-bound resume:

- the same owner and same question continue the same run/root/budget;
- wrong owner, wrong question, missing/corrupt checkpoint, manifest/policy/
  prompt drift, and ambiguous ledger state invoke no provider;
- duplicate resume keys enqueue once;
- a distinct concurrent resume loses the lease race and does not enqueue;
- a lost model result is resent under a new reconstruction-generation key and
  transmitted accounting increases without resetting any cap;
- tool calls before interruption remain charged against the L2 limit;
- event/checkpoint writes stay monotonic under concurrent resume requests.

### End-to-end fixture scenarios

- three claims pass Compliance, deterministic verification, and semantic gate;
- a fourth claim is refused;
- empty retrieval recovers once and succeeds;
- invalid citation/hash recovers by exact clause lookup;
- scope/version mismatch recovers only inside the frozen scope;
- semantic distractor evidence is rejected, directed retrieval is attempted
  once, and a second failure becomes `insufficient_evidence`;
- a policy denial and a provider failure retain their distinct terminal states;
- persisted trace and checkpoint contain none of the fixture's sentinel prose.

### Verification commands

The implementation is not complete until fresh evidence exists for:

```bash
make check
```

and the complete suite on fresh PostgreSQL plus the frozen Qdrant service:

```bash
createdb specpilot_w4_scratch
SPECPILOT_TEST_DSN=postgresql:///specpilot_w4_scratch \
SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 \
  .venv/bin/python -m pytest -q
dropdb specpilot_w4_scratch
```

The database is dropped after a passing or failing run. A test summary with
skips is not full W4 verification.

## Delivery Order

1. Define L2/Compliance/semantic contracts and render/parse joins.
2. Implement the frozen-corpus deterministic verifier.
3. Add the independent semantic provider stage.
4. Add bounded recovery and L2 worker orchestration.
5. Add checkpoint persistence and closed transitions.
6. Add owner-bound client-assisted resume and concurrency controls.
7. Cross the complete fixture path, run fresh-service verification, and update
   the roadmap/report with measured status only.

Each step must preserve the W3 L1 path and the sole policy-bound transport.

## Acceptance Criteria

- L2 returns one to three atomic results and never publishes a determinate
  result that failed either verifier layer.
- Deterministic checks bind every citation to the run manifest, frozen document
  identity/version, clause/span, exact bytes, and authorized scope.
- Semantic support is an independent metered stage with its own prompt and
  directly testable decisions.
- A run performs at most one directed recovery and at most eight L2 tool calls;
  neither tool nor egress accounting resets.
- An owning client can resume the same interrupted run by resubmitting a
  question with the same hash; no question or excerpt prose is durable.
- Wrong or unreadable recovery state fails before any outward call.
- Fixture end to end, lint, strict typing, unit/CLI, fresh PostgreSQL, and frozen
  Qdrant verification all pass with zero skipped tests in the complete run.
