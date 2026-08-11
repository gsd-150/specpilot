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

On colima the published port binds to the VM's loopback, not the Mac's, so check
it from inside the VM: `colima ssh -- curl http://127.0.0.1:8000/health`. On
Docker Desktop and on Linux it is reachable from the host directly.

Copy `.env.example` to `.env` only for local settings. Never commit credentials,
provider account metadata, or restricted source material.
