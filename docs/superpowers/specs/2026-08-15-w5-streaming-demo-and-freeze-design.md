# W5 Streaming, Offline Demo, and Evaluation Freeze Design

**Date:** 2026-08-15
**Status:** Approved in conversation; implementation not started
**Canonical product plan:** `SpecPilot_项目方案.md` §§8–12
**Roadmap:** `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`

## Goal

Complete the W5 engineering package without weakening SpecPilot's disclosure,
ownership, frozen-corpus, or locked-evaluation boundaries. The package adds an
owner-scoped resumable SSE trace, four reproducible offline demo scenarios,
manifest-scoped fixture and real initialization, an optional local provider
response cache, and an author-confirmed evaluation-freeze path.

The work does not make live provider calls, select quality thresholds on the
author's behalf, run the locked sets, or turn fixture behavior into a quality
claim.

## Delivery Shape

W5 is implemented as five independently reviewable vertical slices:

1. synchronize the authoritative progress documents;
2. add the SSE endpoint, resume contract, and snapshot fallback;
3. expose the four registered fixture scenarios through the complete demo path;
4. make fixture and real initialization manifest-scoped and idempotent;
5. add the optional local response cache, evaluation-freeze candidate, and
   release gates.

Each slice crosses the boundaries needed to demonstrate its behavior and ends
with its own tests and commit. Backend, frontend, Compose, and documentation are
not deferred into separate horizontal phases because this repository's repeated
failure class is a value that exists in one component but never reaches the
actual bytes at the next boundary.

## Current-State Documentation

Before product changes, update the roadmap, product-plan annotations, live
annotation execution log, and a new dated handoff with facts recomputed from the
current restricted stores:

- L1 is 40/40: dev 15/15 and locked 25/25;
- L2 is 20/20: dev 8/8 and locked 12/12;
- deep review is 12/12;
- pooling has 60 adjudicated items, no active block, and all runs sealed;
- the W4 fixture engineering package is complete, while live-provider
  acceptance, L2 dev calibration, W5, and W6 remain open;
- the mistyped pooling-choice restart and retired-item evaluation leak are
  fixed in the current main branch.

Dated full-service results remain dated evidence. They must not be restated as
a fresh current-HEAD all-service run when Colima, PostgreSQL, or Qdrant is not
running.

## SSE Trace Contract

### Endpoint and ownership

Add `GET /runs/{run_id}/events` beside the existing owner-scoped snapshot
endpoint. It uses the same bearer-or-cookie authentication dependency and the
same indistinguishable `404 run_not_found` response for unknown and
foreign-owned run IDs.

The stream contains only the existing closed, sanitized run-event union. It
does not add the question, answer, excerpt, claim, rationale, provider response,
or any derived source prose. Every emitted event is validated through the same
contract used by the snapshot endpoint before it is serialized.

### Cursor and ordering

The durable event `sequence` is the SSE `id`. A reconnect supplies
`Last-Event-ID`; credentials never appear in the URL. The server accepts only a
canonical non-negative sequence belonging to the requested run. Malformed,
negative, noncanonical, or future cursors fail closed with a stable sanitized
error.

After ownership and cursor validation, the server reads events strictly after
the cursor. It never reorders, fills a gap with an invented event, or silently
drops an event that fails validation. A retry can repeat the last received
event at the transport boundary, so the client de-duplicates only the identical
sequence and rejects conflicting or decreasing data.

### Stream lifecycle

The stream first returns all persisted events after the cursor, then waits for
new durable events using bounded reads. A finite comment heartbeat keeps an
idle connection observable without becoming a run event. A terminal event is
emitted once and closes the stream normally. Cancellation releases every
database or wait resource and never changes run state.

The service caps heartbeat interval, read batch size, idle lifetime, and total
connection lifetime. A slow or abandoned client cannot hold an unbounded event
buffer. Database and serialization faults close the stream without exposing
exception text.

### Browser transport and fallback

The React client uses `fetch` streaming rather than native `EventSource` so a
real-profile bearer remains in the `Authorization` header. Cookie mode uses the
same implementation with `credentials: "same-origin"`. Both modes prohibit
query-string credentials.

The client strictly decodes SSE framing and the existing closed event union,
tracks the last accepted sequence, and reconnects with bounded exponential
backoff. The total automatic connection window remains 60 seconds. On terminal
state it stops. On exhaustion, invalid data, or an unrecoverable response it
keeps the last valid snapshot and switches to the existing owner-scoped
`GET /runs/{run_id}` manual refresh. Snapshot polling is retained as a safety
fallback, not run concurrently with a healthy stream.

## Four-Scenario Offline Demo

### Registered scenarios

Create one versioned fixture scenario registry with stable IDs:

- `l1_answered`: the L1 retrieval and cited-answer path reaches `answered`;
- `l2_answered`: planning, Compliance, both verifier layers, and the L2 answer
  path reach `answered`;
- `evidence_refused`: insufficient evidence produces a normal refusal;
- `verifier_recovered`: a verifier rejection triggers exactly one directed
  recovery and then reaches its registered terminal result.

Each registry entry binds task level, public display metadata, a fixture
question identity, the fake-provider script version, expected terminal status,
and required sanitized trace-event kinds. The browser submits only the public
scenario ID; it cannot provide or override provider scripts.

Fixture profile displays the scenario selector and retains a custom-question
entry. Real profile displays the ordinary question entry only. An unregistered
fixture question still enters through the normal request boundary and ends with
the stable `unsupported_demo_case` reason; it does not fall back to a generic
fake answer.

### What remains real

All four scenarios use the actual PostgreSQL stores, Qdrant service, MCP
transport and tools, run worker, egress reservations, budgets, deterministic
verifier, recovery code, and SSE trace. Only the source material, vectors, and
provider outputs are synthetic fixtures. The UI and report explicitly state
that this proves engineering paths, not tool-selection quality, retrieval
quality, semantic-verifier quality, or real-provider behavior.

## Manifest-Scoped Initialization

### Fixture initialization

`fixture-init` validates a committed synthetic source manifest, corpus manifest,
precomputed sparse/dense artifacts, collection schema, vector size, point count,
and inventory root. It then creates the expected collection or verifies an
identical existing collection. It never mutates a collection whose frozen
identity already exists with different content.

Success writes a manifest-scoped ready marker atomically. Re-running against an
identical initialized service returns the same identity and does not rewrite
points. Partial collections, mismatched schema, mismatched inventory, or stale
ready markers fail closed.

### Real initialization

`make ingest-real CORPUS_DIR=/absolute/path` accepts only an absolute directory
containing artifacts already admitted by the secure ingestion and freeze
boundaries. It validates source and corpus manifests, constructs a new
content-addressed collection when absent, or verifies an identical frozen
collection when present. It never updates a frozen collection in place.

Both API and MCP health require a ready marker matching their configured source
manifest, corpus manifest, collection identity, and inventory root. Empty,
partially initialized, or differently bound services report unavailable rather
than healthy.

The base and real Compose profiles publish no host port. The explicit demo
override remains the only host exposure and binds the API to `127.0.0.1`.

## Local Provider Response Cache

The cache sits at the provider-adapter boundary. Its key covers provider and
model IDs, stage, prompt ID/hash, configuration hash, source and corpus manifest
IDs, policy epoch, and the canonical request hash. A change to any of those
values creates a different cache namespace.

Fixture profile may enable the cache for repeatable demonstrations. Real
profile defaults to disabled and requires an explicit local cache directory and
TTL. The default TTL is seven days. The root directory is mode `0700`; records
are written atomically and are never tracked by Git.

A cache hit avoids a provider transmission and therefore creates no new egress
reservation. It does create a sanitized cache-hit audit event bound to run,
stage, request hash, and cache-record hash. A miss follows the existing reserve,
send, receipt, and checkpoint path unchanged. Corrupt, expired, mismatched, or
unreadable entries are treated as unavailable; corruption fails closed for that
entry and never returns partial provider data.

Operator commands support expiry cleanup and deletion by run or session. Cache
contents, keys, questions, and provider responses never enter ordinary logs or
the public trace.

## Evaluation Freeze

Add a candidate-generation and explicit-confirmation workflow rather than an
automatic freeze. Candidate generation validates:

- current L1 and L2 targets and split counts;
- complete deep-review coverage;
- sealed pooling with no awaiting adjudication;
- versioned, mutually exclusive L2-adv dev and locked sets with the registered
  family-overlap report;
- exact source/corpus manifests, collection identity, prompts, configuration,
  provider/model identities, policy hash, dependencies, and evaluation scripts;
- selected scoring route and its dev-only calibration evidence.

It emits a canonical candidate `run_spec`, its content hash, and a validation
report. The candidate contains no question text, claim text, excerpt, or locked
output. Finalization requires an explicit confirmation flag and author ID and
records the immutable artifact without running an evaluation.

W6 is the first path allowed to execute locked L1, L2, or L2-adv cases. Locked
results cannot modify the frozen run spec, prompts, thresholds, routes, tools,
or gold. A changed input creates a successor run spec rather than rewriting the
old one.

Live provider calls and the final author judgement on scoring route,
calibration adequacy, and freeze are outside automated W5 execution. The
implementation prepares the artifacts and exact command for the author.

## Failure Semantics

- Authentication and ownership failures preserve the current closed status
  codes and sanitized bodies.
- Stream faults never rewrite a run terminal status.
- Unsupported fixture inputs terminate explicitly as
  `unsupported_demo_case`.
- Initialization never repairs a mismatched frozen collection in place.
- Cache faults never fall back to an unmetered provider send.
- Freeze validation reports exact stable fault codes and writes no final
  artifact when any required identity or evidence is missing.
- No startup path applies migrations, rebinds a policy, initializes a corpus,
  cleans retention data, or freezes an evaluation automatically.

## Verification

Each slice uses test-first changes and focused tests before the repository gate.
The final W5 gate includes:

- Ruff and strict mypy;
- Python unit and CLI suites;
- TypeScript typecheck, unit tests, and production build;
- API integration tests for SSE ownership, cursor validation, replay,
  cancellation, heartbeats, terminal closure, and snapshot fallback;
- browser tests for bearer/cookie credential handling, reconnect, fallback, and
  all four registered scenarios;
- fixture-init and real-init idempotency, identity mismatch, partial-state, and
  ready-marker tests;
- cache-key isolation, expiry, corruption, deletion, and no-unmetered-send tests;
- evaluation candidate/finalization tests proving locked data is not executed;
- Compose config validation, wheel/static-asset verification, and Docker image
  builds;
- a fresh PostgreSQL database with all migrations and the expected frozen
  Qdrant service, with zero skipped service-dependent tests;
- four-scenario offline smoke through the packaged demo profile.

If local services are unavailable, fast checks may be reported separately but
must not be described as the full W5 gate. Fixture results never become quality
metrics. Cold-build and warm-start timings are reported only after measurement,
with sample count and environment.

## Deliverables

- synchronized roadmap, product-plan annotations, live execution log, and dated
  handoff;
- closed SSE API contract and resumable browser client with snapshot fallback;
- versioned four-scenario fixture registry and selector;
- manifest-scoped, idempotent fixture-init and real-init workflows;
- optional local provider response cache with retention operations;
- candidate and author-confirmed evaluation-freeze commands;
- W5 engineering report with current command evidence and explicit limitations;
- updated README and runbooks for demo, real profile, recovery, cache, and
  evaluation freeze.
