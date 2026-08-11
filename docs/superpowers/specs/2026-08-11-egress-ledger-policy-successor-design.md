# Egress Ledger Policy Successor Design

**Status:** approved for implementation planning

**Date:** 2026-08-11

## Problem

`egress_corpus_ledger` currently has one row per `corpus_manifest_id`, and that
row is permanently bound to the policy hash which created it. This is safe while
the policy is stable: `check_and_reserve` rejects a different hash with
`policy_snapshot_mismatch`. It becomes a dead end after a deliberate policy
change, however. The old row cannot be reused, and it cannot be deleted because
reservations correctly retain a foreign key to it.

This has already constrained one real decision: changing policy field names was
ruled out because it would change the policy hash and strand the live corpus
ledger. Task 11 must provide a deliberate recovery path without weakening the
normal fail-closed boundary or destroying audit history.

## Decisions

1. Rebinding is an explicit administrative operation. Ordinary reservation code
   never creates a successor in response to a policy mismatch.
2. A successor inherits all cumulative corpus and per-document disclosure usage.
   Changing policy never restores disclosure budget.
3. Ledger epochs retain immutable identity, policy, and predecessor fields. The
   active epoch's usage may only grow; a superseded epoch is no longer writable
   through the reservation path.
4. A separate mutable head record selects the active epoch for a corpus. Moving
   this pointer is the only mutable part of the successor relationship.
5. Existing evaluation roots do not cross a policy boundary. Work under the new
   policy uses a new evaluation root.
6. A new policy whose cap is below inherited usage may still be recorded. The
   next reservation is rejected by the existing cap checks, so the historical
   overage is visible without permitting further disclosure.

## Alternatives considered

### Composite identity only

Keying ledger rows by `(corpus_manifest_id, policy_hash)` would preserve old
rows, but would not say which policy is active. Selecting the newest timestamp
would turn clock ordering into an authorization decision and would make
concurrent successors ambiguous.

### Mutable `is_current` on the ledger row

An `is_current` flag plus a partial unique index would make lookup simple, but a
policy transition would edit the prior audit row. It also mixes two different
responsibilities: immutable accounting epochs and the mutable current binding.

### Immutable epochs plus a head pointer

This design cleanly separates those responsibilities. The database can retain
every policy-specific accounting row and exact reservation attribution while a
single locked head record serializes reservations and rebinds. This is the
selected approach.

## Data model

Migration `003_egress_ledger_policy_successor.sql` changes the corpus ledger from
a corpus-keyed singleton into policy epochs.

### `egress_corpus_ledger`

The table gains:

- `corpus_ledger_id uuid`, the primary key;
- `predecessor_ledger_id uuid null`, a self-reference;
- a uniqueness constraint on `(corpus_manifest_id, policy_hash)`;
- a uniqueness constraint on `predecessor_ledger_id` when it is non-null, so one
  epoch cannot acquire two direct successors.

`corpus_manifest_id`, `policy_hash`, the JSON usage snapshot, normalized totals,
and timestamps remain. Existing rows receive IDs during migration and have no
predecessor.

The phrase "immutable ledger" applies to epoch identity, policy binding, and
lineage. Usage on the current epoch remains a monotonically increasing
accounting snapshot, as it is today. Once the head moves, normal reservation
writes cannot reach the predecessor again.

### `egress_corpus_ledger_head`

A new table contains:

- `corpus_manifest_id text primary key`;
- `corpus_ledger_id uuid`, referencing the active ledger epoch;
- `updated_at timestamptz`.

One head is created for every migrated corpus row. Route-level disclosure facts
remain corpus-global and reference this corpus identity rather than an arbitrary
policy epoch. A rebind therefore cannot make the system forget that a provider
route has already seen a disclosure.

### Exact epoch attribution

`egress_reservation` and `egress_evaluation_root` gain a non-null
`corpus_ledger_id`. Existing records are backfilled from their corpus's migrated
ledger row. New records store both the human-useful `corpus_manifest_id` and the
exact ledger epoch ID. Reservations reference the epoch ID instead of treating
the corpus ID as the ledger primary key.

The migration preserves every existing row and foreign-key relationship. It
does not delete, merge, or recompute historical usage.

## Initial ledger creation and lock order

For a corpus with no ledger, `_lock_corpus` inserts a nullable head shell with
`ON CONFLICT DO NOTHING`, locks that head `FOR UPDATE`, creates the first epoch,
and fills the head inside one transaction. The temporary null is never visible
after commit.

Every operation uses this lock order:

1. corpus head;
2. active corpus ledger epoch;
3. evaluation root, when applicable.

Reservations and rebinds therefore serialize at the same outermost scope. The
current fixed deadlock-avoidance rule is retained rather than supplemented with
a second lock graph.

## Explicit rebind operation

`PostgresEgressLedger.rebind_policy` accepts:

- `corpus_manifest_id`;
- `expected_policy_hash`;
- the ledger instance's configured policy as the requested new policy.

The CLI exposes it as:

```text
specpilot egress rebind-policy \
  --corpus-manifest-id <sha256> \
  --expected-policy-hash <sha256>
```

The command does not accept an arbitrary hash detached from policy content. It
records and binds the same server-owned `EgressPolicy` object used by the
enforcer.

Inside one transaction the operation:

1. records the new policy snapshot;
2. locks the corpus head and active epoch;
3. verifies the active hash equals `expected_policy_hash`;
4. copies and validates `CorpusUsage`;
5. changes only the copied usage's `policy_hash`;
6. inserts a successor with the complete inherited snapshot and normalized
   totals;
7. moves the head to the successor.

The result reports corpus ID, predecessor and successor ledger IDs, old and new
policy hashes, and inherited count totals. It never prints source text,
disclosure content, credentials, or local paths.

## Failure and retry semantics

- No ledger for the corpus: reject; rebind is not an implicit initializer.
- Expected hash differs from the active hash: reject with a stable conflict
  code and leave all rows unchanged.
- New hash equals the active hash: return the active binding as an idempotent
  no-op.
- A retry finds that the active row is the unique successor of the expected
  predecessor under the requested new hash: return that successor.
- The expected epoch already has another successor, or the head moved to an
  unrelated epoch: reject as a conflict; never create a branch.
- Database connectivity is lost before the outcome is known: return an
  ambiguous administrative result. The operator can safely repeat the same
  expected-old/new-policy request because the success case is idempotent.

Normal `check_and_reserve` continues to reject an active ledger whose policy
hash differs from the configured policy. It never invokes `rebind_policy`.

## Accounting behavior

`CorpusUsage` contains the corpus policy hash, corpus-wide disclosure IDs and
totals, and per-document disclosure IDs and totals. The successor copies all of
them. Only its policy hash changes. Consequently:

- previously disclosed source remains charged under every later policy;
- per-document one-fifth limits cannot be reset by policy rotation;
- re-sending a known disclosure remains non-unique after a rebind;
- tighter policies immediately see the inherited amount;
- the predecessor remains an exact record of the total reached under its own
  policy, while the successor represents that inherited floor plus later use.

Reservation accounting occurs before a provider send and in the same
transaction as the reservation row. A rebind that obtains the head lock
therefore copies every previously admitted reservation, including one whose
provider attempt has not completed. `record_attempt` does not alter disclosure
usage, so no post-rebind usage can arrive late in the predecessor.

## Evaluation-root boundary

An evaluation root is bound to both its policy hash and ledger epoch. After a
rebind, a worker configured with the old policy fails at the corpus head, and an
old root cannot be reopened under the new policy. The caller must create a new
evaluation root ID for new-policy work.

This deliberately favors audit clarity over carrying root-local transmitted and
stage budgets between policies. Corpus and per-document disclosure are the only
cross-policy quantities, and they are fully inherited by the successor.

## Test strategy

Unit coverage will prove that the successor-copy operation:

- preserves corpus and document disclosure IDs and totals exactly;
- changes only `policy_hash`;
- validates the resulting typed snapshot;
- does not mutate the predecessor object.

PostgreSQL integration coverage will prove:

- a policy change fails before explicit rebind;
- rebind preserves the predecessor and all existing reservations;
- the successor points to the predecessor and becomes the sole head;
- new reservations continue from inherited corpus and document totals;
- a cap below inherited usage rejects the next reservation;
- route-disclosure history survives policy rotation;
- old evaluation roots cannot cross the boundary;
- wrong expected hashes and competing successors fail closed;
- an identical retry returns the same successor;
- concurrent reservation/rebind operations serialize without lost accounting;
- migration of a populated v2 corpus-usage ledger preserves IDs, totals, and
  foreign-key validity.

The full unit, integration, fixture-smoke, lint, type, and wheel checks remain
required. CI uses a fresh database per migration session, matching the repair
already made to the workflow.

## Documentation and operational handoff

On completion, Task 11 is marked closed in the assisted-annotation plan and the
2026-08-11 handoff. The operator documentation records that policy edits require
the explicit command, its expected-hash guard, the inherited-budget behavior,
and the requirement for new evaluation-root IDs.

No real provider call, corpus content, policy relaxation, or new product depth
is part of this task.
