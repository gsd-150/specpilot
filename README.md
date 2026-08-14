# SpecPilot

SpecPilot is a safety-first foundation for specification intelligence.

**Working on this repository — start here:** [`AGENTS.md`](AGENTS.md) for how to
run it and which invariants must not be broken, then the newest file in
[`docs/handoff/`](docs/handoff/) for where things currently stand and what comes
next.

## Local development

Use Python 3.12 through 3.14 and run `make setup` to create a repository-local
virtual environment with development dependencies. Then run `make unit`,
`make lint`, and `make typecheck`.

The ledger integration tests need a local PostgreSQL. With Homebrew:

```
brew services start postgresql@17
createdb specpilot_test
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_test make integration-db
```

Use a fresh database. The fixture applies every file in `migrations/` in
filename order, so reusing one you migrated by hand hides a missing migration.

`make unit` and `make check` skip the integration, Qdrant and smoke suites and
still report "passed". A run that proves anything sets both
`SPECPILOT_TEST_DSN` and `SPECPILOT_TEST_QDRANT_URL`.

## Enabling ledger-bound planning

Migration 004 adds only the closed `planning` value to the reservation stage
constraint. It preserves populated ledgers and does not authorize or rebind a
corpus policy epoch. Apply the checked-out artifact explicitly before enabling
the planner:

```bash
psql "$SPECPILOT_LEDGER_DSN" -v ON_ERROR_STOP=1 \
  -f migrations/004_egress_planning_stage.sql
```

The packaged policy change still requires the explicit successor/rebind flow
below. Neither migration nor policy rebinding is automatic.

Migration 005 adds owner-bound asynchronous run metadata and its sanitized,
typed event stream. It never stores the question or corpus excerpts. Apply it
from the same checked-out repository only after migration 004:

```bash
psql "$SPECPILOT_LEDGER_DSN" -v ON_ERROR_STOP=1 \
  -f migrations/005_run_trace.sql
```

Neither the API image nor the wheel applies migrations, and
`specpilot_live` is never migrated or rebound automatically. An operator must
apply migrations 003--005 and complete the explicit policy successor/rebind
below before enabling the new `planning` stage against an existing corpus
ledger.

## Rebinding a corpus ledger to a successor policy

Migration 003 is a separate, versioned operations artifact from the repository
checkout. The current wheel and API image do not contain migrations. From the
repository root, apply the exact checked-out artifact before deploying or using
the operator command:

```bash
psql "$SPECPILOT_LEDGER_DSN" -v ON_ERROR_STOP=1 \
  -f migrations/003_egress_ledger_policy_successor.sql
```

Then run:

```bash
.venv/bin/python -m specpilot.cli egress rebind-policy \
  --ledger-dsn <ledger-dsn> \
  --manifest-dir <source-manifest-dir> \
  --policy <new-policy.json> \
  --corpus-manifest-id <corpus-manifest-sha256> \
  --expected-ledger-id <active-ledger-uuid> \
  --expected-policy-hash <active-policy-sha256>
```

This is the only deliberate policy-successor path. An ordinary reservation
under a different policy still fails closed with `policy_snapshot_mismatch`;
it never rebinds automatically. `--expected-ledger-id` is the authoritative
compare-and-swap identity, and `--expected-policy-hash` is a secondary guard;
both must match the active epoch before a successor can be created.

A successful rebind preserves the predecessor and creates an immutable
successor that inherits the complete corpus and per-document usage snapshot.
Use a new `evaluation_root_id` for the first reservation under that successor:
an old root remains bound to its original ledger epoch. A response with status
`unchanged` is a safe retry result only when the active epoch directly
supersedes that exact expected ledger UUID under the requested new policy (or
when the exact expected epoch already has that policy). A hash alone never
identifies a retry. Repeated policy hashes remain legal, so A→B→A creates three
distinct ledger epochs. If the new policy has lower caps than the inherited
totals, the successor is still recorded; later reservations are refused rather
than history being deleted or rewritten.

## Running the stack

Nothing in `compose.yaml` publishes a host port. Reaching the API needs the demo
override, which adds both the loopback port and a routable network:

```
docker compose -f compose.yaml -f compose.demo.yaml --profile demo up --wait
```

`--profile real` publishes nothing at all.

The API runtime has no deployment defaults. Compose passes only the named
`SPECPILOT_API_*`, frozen-manifest `SPECPILOT_MCP_*`, and real-profile provider
credential variables from the operator environment; invalid or missing values
produce only a sanitized unavailable health response. The fixture profile must
bind to loopback. The real profile may bind inside the container but remains
unpublished by the base Compose file.

Both API and MCP receive the same three frozen artifact trees through explicit
read-only bind mounts. Set
`SPECPILOT_MCP_CORPUS_MANIFEST_DIR_HOST`,
`SPECPILOT_MCP_SOURCE_MANIFEST_DIR_HOST`, and
`SPECPILOT_MCP_SOURCE_DATA_DIR_HOST` to host directories. Inside both
containers they are fixed at `/run/specpilot/corpus`,
`/run/specpilot/manifests`, and `/run/specpilot/sources`; each `xml_path` in
`SPECPILOT_MCP_SOURCES_JSON` must be an absolute path below the last directory.
Host paths are mount sources only and never become container environment
values.

The base API joins `internal` and `egress`, which permits the real provider
route while leaving MCP internal-only. `compose.demo.yaml` overrides the
fixture API to `internal` plus the demo bridge and publishes it only on
`127.0.0.1`; the fixture API never joins `egress`.

## L1 API and sanitized trace

The W3 HTTP surface is intentionally small:

- `POST /sessions/demo` exists only for the loopback fixture profile. It sets a
  five-minute HTTP-only, SameSite-strict session cookie and returns no token.
- `POST /chat` requires that cookie or an `Authorization: Bearer ...` header,
  validates the exact source/corpus binding, persists a query hash (never the
  question), queues one L1 run, and returns `202` with its `run_id`.
- `GET /runs/{run_id}` requires the same credential. Unknown and foreign-owned
  IDs have the identical `404` response, so the endpoint is not an ownership
  oracle. Its typed event stream contains stable codes, opaque IDs, hashes,
  counts, timings, and ledger summaries only.
- `GET /trace` serves the packaged React client. It polls the owner-scoped run
  endpoint, stops on a terminal state, and stops locally after 60 seconds. A
  local polling timeout preserves the last server state and offers a manual
  refresh; it never writes a new server status.

The terminal states are deliberately distinct. `refused` is a normal
evidence/citation decision; `egress_blocked` means the disclosure ledger denied
the provider send; `failed` is selected from `provider_error`, not the verifier
verdict; and `interrupted` is read-derived from an expired worker lease and is
never automatically resumed. The page also renders `answered`, but it does not
render the answer or any source excerpt in this W3 slice.

Migration 004 adds the closed `planning` egress stage. Migration 005 adds the
owner-bound run and sanitized event tables. Enabling 004 changes the packaged
policy hash, so an existing corpus ledger still needs the explicit migration
003 successor and `egress rebind-policy` operation described above. No API
startup performs migrations or policy rebinding.

W3 uses bounded HTTP polling only. SSE, `/chat/{run_id}/events`, reconnect
semantics, and SSE credential transport remain W5 work; this release contains
no `EventSource` client or events endpoint.

## W4 L2 gates, checkpointing, and owner-assisted recovery

An L2 run is bounded to at most three Compliance candidates and eight cumulative
tool attempts (including directed recovery); the L1 cap remains six. Compliance
first produces structured candidates. The local deterministic gate then checks
citation identity, manifest/version and scope before a semantic Verifier call is
admitted. A failed deterministic or semantic gate may consume one run-scoped,
directed recovery only. Recovery does not reset the tool or egress budget; every
recovered candidate goes through the complete deterministic and semantic gates
again. Semantic support remains a model judgement, not a quality metric.

W4 requires migrations 006--012 in filename order, in addition to the earlier
operator-applied migrations. Startup still never applies migrations:

```bash
for migration in migrations/006_w4_checkpoint_resume.sql migrations/007_w4_compliance.sql \
  migrations/008_w4_checkpoint_validator.sql migrations/009_w4_checkpoint_evidence_validator.sql \
  migrations/010_w4_checkpoint_generation_validator.sql migrations/011_w4_checkpoint_results_validator.sql \
  migrations/012_w4_checkpoint_verifier_claim_scope.sql; do
  psql "$SPECPILOT_LEDGER_DSN" -v ON_ERROR_STOP=1 -f "$migration"
done
```

The durable checkpoint is deliberately reconstruction-only: it holds frozen
bindings, hashes/opaque evidence IDs, budgets, reservation IDs, stage and
generation metadata. It never holds a question, design/claim/rationale,
retrieval query, excerpt, or provider response. `compact(run_id)` may erase the
completed checkpoint's evidence, reservations and generation state; an operator
must arrange retention by calling `delete_expired(now - 7 days)` for noncompleted
checkpoints. Neither operation is an automatic background cleanup job.

After a lease-expired L2 run, its owner may call `POST /runs/{run_id}/resume`
with the same question and an idempotency `resume_key`. Resume verifies owner,
question hash, frozen root/bindings, checkpoint, reservation terminal states and
the new lease before queue delivery. It preserves the original root and budgets;
locally reconstructible work is not repeated. A provider result lost across a
process boundary is sent under the next explicit reconstruction generation and
is charged as another transmitted attempt under the unchanged caps. The resume
request prose is used only to calculate the hash and build the ephemeral job.

For service evidence, use a new database name every time and an isolated Qdrant
endpoint. The suite uses only the fixture `FakeProvider`; do not supply a live
provider route for this check. The complete command record is in
[`docs/reports/w4-compliance-verifier-recovery.md`](docs/reports/w4-compliance-verifier-recovery.md).

The deterministic browser gate uses only a synthetic RFC fixture, the local
fake provider, a dedicated fresh database named `specpilot_w3_browser_test`,
and loopback HTTP. Run it with:

```bash
createdb specpilot_w3_browser_test
npm --prefix web/trace exec playwright install chromium
npm --prefix web/trace run build
SPECPILOT_BROWSER_DSN=postgresql://localhost:5432/specpilot_w3_browser_test \
  npm --prefix web/trace run test:browser
dropdb specpilot_w3_browser_test
```

The launcher refuses another database name or a non-loopback host, requires a
fresh schema, applies migrations 001--005, and clears that allowlisted schema
on bounded shutdown. It never calls a real provider.

On colima the published port binds to the VM's loopback, not the Mac's, so check
it from inside the VM: `colima ssh -- curl http://127.0.0.1:8000/health`. On
Docker Desktop and on Linux it is reachable from the host directly.

Copy `.env.example` to `.env` only for local settings. Never commit credentials,
provider account metadata, or restricted source material.
