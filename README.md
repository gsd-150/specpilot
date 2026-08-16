# SpecPilot

SpecPilot answers questions about HTTP specifications (RFC 9110 and 9112) and
assesses whether a described design conforms to them, with every excerpt that
leaves the machine passing through one egress gate that meters and records it.
The design premise is that a specification assistant is only useful if you can
tell when it is wrong, so the evaluation apparatus is the product as much as
the answering chain is.

Two things follow from that premise and shape the whole repository:

- **The enforcer is the only path outward,** and it holds a lifetime budget per
  source document — a data-minimization argument, made concrete as a cap that
  refuses rather than warns.
- **Every claim the system makes cites the exact bytes it was shown,** recorded
  in a ledger that can be reconstructed from the corpus independently of what
  the model says it used. When the two disagree, the disagreement is the
  finding.

**Working on this repository — start here:** [`AGENTS.md`](AGENTS.md) for how to
run it and which invariants must not be broken, then the newest file in
[`docs/handoff/`](docs/handoff/) for where things currently stand and what comes
next.

## What the locked evaluation found

Full report:
[`docs/reports/2026-08-16-w6-locked-evaluation.md`](docs/reports/2026-08-16-w6-locked-evaluation.md).
Release evidence is tagged `w6-locked-evaluation-2026-08-16`, at the commit the
gate actually ran on.

The locked sets were executed once, against a frozen, author-confirmed run spec
(`270f7957…`). What ran:

| set | n | outcome |
|---|---|---|
| L1 | 25 | 21 answered, 4 refused; all 20 answerable items answered |
| L2 | 12 | 11 verdicts; 1 refused by the egress quota before any bytes left |
| L2-adv | 10 | **0 executed** — the quota gate refused the budget |

Three results are worth more than an accuracy score, and the report leads with
them rather than burying them:

1. **A verified false confirmation.** On one L2 case the chain returned a
   determinate "compliant" that its own verifier passed — while the two gold
   clauses that decide the question were never retrieved. Three independent
   measures point at it (key points 0/3, claim label insufficient, gold coverage
   none), and the model's own rationale reads as sound. This is the case for
   keeping an evidence ledger that does not depend on the model's account of
   itself.
2. **The development loop spent the measurement budget.** The per-document cap
   is lifetime and unique-keyed, so every rehearsal that surfaced a new clause
   permanently consumed budget the locked run later needed. RFC 9112 ended at
   98.3% used, which is why L2-adv never ran. The caps were not raised and no
   ledger was rebound: a gate verdict, not an omission.
3. **Human review is load-bearing in one place and near pass-through in
   another.** The annotation stores record 40 of 41 model proposals adopted with
   no key point ever edited, against 5 of 16 adversarial groups rejected on
   construction review. Both numbers are in the report; "human-reviewed" is not
   a uniform guarantee across the item set.

**Bounds that travel with every number above.** The sets are small (`n` = 25,
12, 10 groups). Most gold and scenario proposals came from a model, reviewed by
one person. The §8.4 audit labels were produced by that same model family, so
they do not discharge an independent human audit and every agreement figure
inherits the defect. None of these are unbiased performance estimates, and the
report says so in its own body rather than in a footnote.

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

W5 upgrades that fallback to an owner-scoped SSE stream at
`GET /runs/{run_id}/events`. The browser uses `fetch` streaming so credentials
remain in an `Authorization` header or secure cookie, and resumes only through
`Last-Event-ID`; credentials never enter the URL. Sequence gaps, conflicting
replays, oversized frames, malformed UTF-8, and exhausted reconnects fail
closed while preserving the last valid snapshot for manual refresh.

The fixture profile exposes exactly four deterministic scenarios:
`l1_answered`, `l2_answered`, `evidence_refused`, and
`verifier_recovered`. They use committed vectors, a packaged fixture-only
policy overlay, and `FakeProvider`; they demonstrate wiring and failure
semantics, not answer quality. `fixture-init` publishes a deterministic
successor authorized only for `fixture-provider/fixture-smoke`, then binds the
corpus and ready marker to that successor. The default/real policy and exact
real provider route remain unchanged.

Run the complete engineering gate with explicit disposable services and an
explicit Compose env file:

```bash
SPECPILOT_TEST_DSN=postgresql://... \
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6333 \
SPECPILOT_BROWSER_DSN=postgresql://.../specpilot_w5_task9_browser_scratch \
SPECPILOT_COMPOSE_ENV_FILE=/absolute/path/to/fixture.env \
make w5-check
```

The Task 9 browser fixture accepts that database name through an exact literal
allowlist (the earlier `specpilot_w3_browser_test` name remains available for
the standalone W3 browser gate); near matches and arbitrary environment names
are rejected. The gate rejects service skips, renders demo/base/real Compose shapes, checks
that base/real publish no host port, verifies the wheel, runs all five browser
paths, and builds API, MCP, fixture-init, real-init, and ingestion images.

## W4 L2 gates, checkpointing, and owner-assisted recovery

An L2 run is bounded to at most three Compliance candidates and eight cumulative
tool attempts (including directed recovery); the L1 cap remains six. Compliance
first produces structured candidates. The local deterministic gate then checks
citation identity, manifest/version and scope before a semantic Verifier call is
admitted. A failed deterministic or semantic gate may consume one run-scoped,
directed recovery only. Recovery does not reset the tool or egress budget; every
recovered candidate goes through the complete deterministic and semantic gates
again. Semantic support remains a model judgement, not a quality metric.

W4 requires migrations 006--014 in filename order, in addition to the earlier
operator-applied migrations. The explicit list neither reapplies 001--005 nor
silently expands to a future migration. Startup still never applies migrations:

```bash
for migration in \
  migrations/006_w4_checkpoint_resume.sql \
  migrations/007_w4_checkpoint_generation_history.sql \
  migrations/008_w4_checkpoint_candidate_cursor.sql \
  migrations/009_w4_checkpoint_evidence_validator.sql \
  migrations/010_w4_checkpoint_generation_validator.sql \
  migrations/011_w4_checkpoint_results_validator.sql \
  migrations/012_w4_checkpoint_verifier_claim_scope.sql \
  migrations/013_w4_recovery_reservation.sql \
  migrations/014_w4_recovery_claim_binding.sql
do
  psql "$SPECPILOT_LEDGER_DSN" -v ON_ERROR_STOP=1 -f "$migration"
done
```

Before applying 014, quiesce new L2 submissions, client resumes and recovery
writes, then let only workers that still hold their original in-memory recovery
context finish. Version 013 did not retain the opaque claim ID that owns a
`recovery_reserved` checkpoint, so an operator must not guess one, fabricate a
binding, or client-resume an interrupted reserved run under 013. Run this
preflight query and require a count of zero:

```sql
SELECT run_id, checkpoint_version, stage
FROM specpilot_run_checkpoint
WHERE stage = 'recovery_reserved'
   OR payload ->> 'stage' = 'recovery_reserved';
```

Migration 014 performs the same preflight before any schema mutation and exits
with `W4_014_RECOVERY_RESERVED_DRAIN_REQUIRED` if rows remain. Non-reserved
legacy checkpoints are backfilled with a JSON `null` binding; the binding is
present only during a newly reserved recovery transition.

An interrupted row returned by the query cannot be safely reconstructed by
013. Either keep the deployment quiesced on 013, or explicitly abandon its
checkpoint through the existing operator retention path after the seven-day
retention boundary by calling
`PostgresCheckpointStore.delete_expired(UTC_now - timedelta(days=7))`. This
removes only the noncompleted checkpoint; the interrupted run, attempts, events
and ledger audit remain. Do not apply 014, and do not re-enable L2 writers,
until the query returns zero. If policy does not permit waiting for retention
or abandoning resumability, remain on 013 and escalate the affected runs for a
separate audited migration; there is no safe automatic owner inference.

The durable checkpoint is deliberately reconstruction-only: it holds frozen
bindings, hashes/opaque evidence IDs, budgets, reservation IDs, stage and
generation metadata. It never holds a question, design/claim/rationale,
retrieval query, excerpt, or provider response. `compact(run_id)` may erase the
completed checkpoint's evidence, reservations and generation state; an operator
must arrange retention by calling `delete_expired(now - 7 days)`. That operation
deletes only old, noncompleted checkpoints whose run is no longer queued or
running; completed, queued and running state is retained. Neither operation is
an automatic background cleanup job.

After a lease-expired L2 run, its owner may call `POST /runs/{run_id}/resume`
with the same question and an idempotency `resume_key`. Resume verifies owner,
question hash, frozen root/bindings, checkpoint, reservation terminal states and
the new lease before queue delivery. Before any new attempt is acquired, the
checkpoint attempt must equal the locked maximum attempt-ledger row; active
same-key replay requires that row to be open, while an interrupted resume
requires the matching closed terminal lineage. The first checkpoint write is
always `planned` attempt 1. It preserves the original root and budgets;
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

## Optional local provider-response cache

Compose gives the API a persistent named volume at
`/run/specpilot/provider-cache`. The image creates that directory for the
unprivileged API user with mode `0700`; cache records remain private regular
files. Both fixture and real profiles leave the cache disabled unless the
operator supplies the complete pair below. There is no implicit real-profile
TTL:

```dotenv
SPECPILOT_API_CACHE_DIR=/run/specpilot/provider-cache
SPECPILOT_API_CACHE_TTL_SECONDS=604800
```

`604800` is the registered seven-day retention period. Cache entries can hold
provider responses, so the volume is local sensitive state: do not copy it into
logs, reports, source control, or evaluation artifacts. Expiry is enforced on
lookup, but physical retention cleanup is deliberately operator-owned and does
not run at service startup. Run the bounded count-only cleanup explicitly:

```bash
python -m specpilot.cli cache delete-expired \
  --directory /run/specpilot/provider-cache --ttl-seconds 604800
python -m specpilot.cli cache delete-run \
  --directory /run/specpilot/provider-cache --ttl-seconds 604800 \
  --run-id RUN_ID
python -m specpilot.cli cache delete-session \
  --directory /run/specpilot/provider-cache --ttl-seconds 604800 \
  --session-id SESSION_ID
```

Disable the cache by leaving both variables empty or unset. Never configure
only one member of the pair; startup fails closed when the pair is incomplete,
the TTL is nonpositive, or the directory is not absolute.
