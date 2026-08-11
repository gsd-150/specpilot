# Egress Ledger Policy Successor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, fail-closed corpus-ledger policy rebind that creates an auditable successor epoch without resetting corpus or per-document disclosure usage.

**Architecture:** Replace the corpus-keyed singleton ledger with UUID-keyed policy epochs and a separately locked corpus head. Reservations bind to the exact active epoch; an administrative transaction copies cumulative usage into one unique successor and atomically moves the head. Ordinary reservations still reject policy drift and never trigger rebinding.

**Tech Stack:** Python 3.12, Pydantic 2, psycopg 3 async API, PostgreSQL SQL migrations, argparse CLI, pytest/AnyIO, Ruff, mypy.

## Global Constraints

- Rebinding is explicit; `check_and_reserve` must never create a successor after `policy_snapshot_mismatch`.
- Successors inherit all corpus-wide and per-document disclosure IDs, token totals, and byte totals; no policy change restores budget.
- Ledger epoch identity, `policy_hash`, and predecessor are immutable after insertion; only the active epoch's usage grows.
- The corpus head is the sole mutable binding and is locked before a ledger epoch and evaluation root.
- Existing evaluation roots cannot cross a policy boundary; new-policy work requires a new `evaluation_root_id`.
- A tighter policy may be recorded even when inherited usage exceeds it, but the next reservation must fail closed.
- Rebind retries are idempotent and must never form a successor branch.
- No source text, query text, prompt, response, credential, or local path may enter ledger storage or command output.
- Do not make a real provider call or relax any cap in this task.

---

## File structure

- Create `migrations/003_egress_ledger_policy_successor.sql`: upgrade populated ledgers to epoch IDs, exact foreign keys, and a corpus head.
- Create `tests/unit/egress/test_ledger_policy_successor.py`: pure successor-copy and result-contract coverage.
- Create `tests/integration/egress/test_postgres_policy_successor.py`: migration, rebind, lineage, inherited accounting, retry, and concurrency coverage.
- Create `tests/integration/cli/test_egress_rebind_policy.py`: real-database CLI behavior and sanitized output coverage.
- Modify `src/specpilot/egress/ledger.py`: rebind result and error contracts plus the pure usage-copy function.
- Modify `src/specpilot/egress/postgres.py`: epoch-aware reservation queries and the transactional rebind operation.
- Modify `src/specpilot/cli.py`: the explicit `egress rebind-policy` command.
- Modify `tests/conftest.py`: truncate the new head table in dependency-safe order.
- Modify `docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md`: mark Task 11 complete with verification evidence.
- Modify `docs/handoff/2026-08-11-codex-handoff.md`: replace the Task 11 blocker with the implemented operator path.
- Modify `README.md`: document the deliberate rebind command and new-root requirement.

---

### Task 1: Define successor accounting contracts

**Files:**
- Create: `tests/unit/egress/test_ledger_policy_successor.py`
- Modify: `src/specpilot/egress/ledger.py`

**Interfaces:**
- Consumes: `CorpusUsage`, `Identifier`, and `Sha256` from the existing contracts.
- Produces: `successor_corpus_usage(existing: CorpusUsage, new_policy_hash: str) -> CorpusUsage`.
- Produces: `PolicyRebindResult`, `PolicyRebindConflict`, and `PolicyRebindAmbiguous`.

- [ ] **Step 1: Write failing tests for exact inherited accounting**

```python
from specpilot.contracts.egress import CorpusDocumentUsage, CorpusUsage
from specpilot.egress.ledger import successor_corpus_usage


def test_successor_changes_only_the_policy_binding() -> None:
    old = CorpusUsage(
        corpus_manifest_id="a" * 64,
        policy_hash="b" * 64,
        disclosure_ids=("c" * 64, "d" * 64),
        unique_tokens=13,
        unique_bytes=89,
        document_usage=(
            CorpusDocumentUsage(
                document_id="rfc-9110",
                disclosure_ids=("c" * 64,),
                unique_tokens=5,
                unique_bytes=34,
            ),
        ),
    )

    successor = successor_corpus_usage(old, "e" * 64)

    assert successor == old.model_copy(update={"policy_hash": "e" * 64})
    assert old.policy_hash == "b" * 64
    assert successor.document_usage == old.document_usage


def test_successor_rejects_the_same_policy_hash() -> None:
    old = CorpusUsage(corpus_manifest_id="a" * 64, policy_hash="b" * 64)

    with pytest.raises(ValueError, match="different policy hash"):
        successor_corpus_usage(old, old.policy_hash)
```

- [ ] **Step 2: Run the unit test and verify the missing interface fails**

Run: `.venv/bin/python -m pytest tests/unit/egress/test_ledger_policy_successor.py -q`

Expected: collection fails because `successor_corpus_usage` does not exist.

- [ ] **Step 3: Add the pure copy function and administrative contracts**

Add to `src/specpilot/egress/ledger.py`:

```python
class PolicyRebindConflict(LedgerError):
    def __init__(self, message: str = "corpus policy binding changed") -> None:
        super().__init__("corpus_policy_rebind_conflict", message)


class PolicyRebindAmbiguous(LedgerError):
    def __init__(self, message: str = "policy rebind outcome is unknown") -> None:
        super().__init__("policy_rebind_ambiguous", message)


class PolicyRebindResult(_FrozenModel):
    schema_version: Literal["egress-policy-rebind/v1"] = "egress-policy-rebind/v1"
    corpus_manifest_id: Sha256
    predecessor_ledger_id: Identifier
    successor_ledger_id: Identifier
    old_policy_hash: Sha256
    new_policy_hash: Sha256
    inherited_unique_excerpts: Annotated[int, Field(ge=0)]
    inherited_unique_tokens: Annotated[int, Field(ge=0)]
    inherited_unique_bytes: Annotated[int, Field(ge=0)]
    rebound: bool = True


def successor_corpus_usage(
    existing: CorpusUsage,
    new_policy_hash: str,
) -> CorpusUsage:
    if existing.policy_hash == new_policy_hash:
        raise ValueError("successor requires a different policy hash")
    return CorpusUsage.model_validate(
        {**existing.model_dump(mode="python"), "policy_hash": new_policy_hash}
    )
```

Export the new public contracts in `__all__` if the module gains an explicit export list.

- [ ] **Step 4: Add result/error validation tests and run the focused suite**

Assert `successor_corpus_usage` rejects an invalid new hash, result models reject
negative totals, and both errors expose their exact stable codes. Then run:

Run: `.venv/bin/python -m pytest tests/unit/egress/test_ledger_policy_successor.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the contract increment**

```bash
git add src/specpilot/egress/ledger.py tests/unit/egress/test_ledger_policy_successor.py
git commit -m "feat: define ledger policy successor contracts"
```

---

### Task 2: Migrate populated ledgers and bind reservations to active epochs

**Files:**
- Create: `migrations/003_egress_ledger_policy_successor.sql`
- Create: `tests/integration/egress/test_postgres_policy_successor.py`
- Modify: `tests/conftest.py`
- Modify: `src/specpilot/egress/postgres.py`
- Modify: `tests/integration/egress/test_postgres_reservation.py`
- Modify: `tests/integration/egress/test_postgres_recovery.py`

**Interfaces:**
- Consumes: the schema produced by migrations `001` and `002`.
- Produces: `egress_corpus_ledger.corpus_ledger_id`, `predecessor_ledger_id`, and `egress_corpus_ledger_head`.
- Produces: exact `corpus_ledger_id` attribution on `egress_reservation` and `egress_evaluation_root`.
- Produces: private `_LockedCorpus(corpus_ledger_id: str, usage: CorpusUsage | None)` and epoch-keyed reservation queries.

- [ ] **Step 1: Write a failing populated-upgrade integration test**

Create a unique temporary PostgreSQL schema, set `search_path` to it, apply only migrations `001` and `002`, insert one policy, corpus ledger, root, reservation, disclosure, and route-disclosure row, then apply migration `003`. Assert:

```python
assert migrated_ledger["predecessor_ledger_id"] is None
assert migrated_head["corpus_ledger_id"] == migrated_ledger["corpus_ledger_id"]
assert migrated_reservation["corpus_ledger_id"] == migrated_ledger["corpus_ledger_id"]
assert migrated_root["corpus_ledger_id"] == migrated_ledger["corpus_ledger_id"]
assert migrated_ledger["corpus_usage"] == original_usage
assert migrated_route_count == 1
```

Use a `try/finally` that drops only the generated schema. Never drop or recreate the database supplied by `SPECPILOT_TEST_DSN`.

- [ ] **Step 2: Run the migration test and verify it fails because migration 003 is absent**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress/test_postgres_policy_successor.py::test_migration_upgrades_a_populated_ledger_without_losing_audit_rows -q`

Expected: fail with missing `003_egress_ledger_policy_successor.sql`.

- [ ] **Step 3: Implement the migration**

The SQL must perform these concrete operations inside `BEGIN`/`COMMIT`:

```sql
ALTER TABLE egress_corpus_ledger ADD COLUMN corpus_ledger_id uuid;
UPDATE egress_corpus_ledger
SET corpus_ledger_id = md5(corpus_manifest_id)::uuid;
ALTER TABLE egress_corpus_ledger
    ALTER COLUMN corpus_ledger_id SET NOT NULL,
    ADD COLUMN predecessor_ledger_id uuid;

ALTER TABLE egress_reservation ADD COLUMN corpus_ledger_id uuid;
ALTER TABLE egress_evaluation_root ADD COLUMN corpus_ledger_id uuid;

UPDATE egress_reservation AS reservation
SET corpus_ledger_id = ledger.corpus_ledger_id
FROM egress_corpus_ledger AS ledger
WHERE ledger.corpus_manifest_id = reservation.corpus_manifest_id;

UPDATE egress_evaluation_root AS root
SET corpus_ledger_id = ledger.corpus_ledger_id
FROM egress_corpus_ledger AS ledger
WHERE ledger.corpus_manifest_id = root.corpus_manifest_id;

ALTER TABLE egress_route_disclosure
    DROP CONSTRAINT egress_route_disclosure_corpus_manifest_id_fkey;
ALTER TABLE egress_reservation
    DROP CONSTRAINT egress_reservation_corpus_manifest_id_fkey;
ALTER TABLE egress_corpus_ledger
    DROP CONSTRAINT egress_corpus_ledger_pkey;

ALTER TABLE egress_corpus_ledger
    ADD CONSTRAINT egress_corpus_ledger_pkey PRIMARY KEY (corpus_ledger_id),
    ADD CONSTRAINT egress_corpus_ledger_corpus_policy_key
        UNIQUE (corpus_manifest_id, policy_hash),
    ADD CONSTRAINT egress_corpus_ledger_corpus_epoch_key
        UNIQUE (corpus_manifest_id, corpus_ledger_id),
    ADD CONSTRAINT egress_corpus_ledger_predecessor_key
        UNIQUE (predecessor_ledger_id),
    ADD CONSTRAINT egress_corpus_ledger_predecessor_fkey
        FOREIGN KEY (predecessor_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_ledger_id);

CREATE TABLE egress_corpus_ledger_head (
    corpus_manifest_id text PRIMARY KEY
        CHECK (corpus_manifest_id ~ '^[0-9a-f]{64}$'),
    corpus_ledger_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT egress_corpus_ledger_head_epoch_fkey
        FOREIGN KEY (corpus_manifest_id, corpus_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_manifest_id, corpus_ledger_id)
);

INSERT INTO egress_corpus_ledger_head (corpus_manifest_id, corpus_ledger_id)
SELECT corpus_manifest_id, corpus_ledger_id FROM egress_corpus_ledger;

ALTER TABLE egress_reservation
    ALTER COLUMN corpus_ledger_id SET NOT NULL,
    ADD CONSTRAINT egress_reservation_corpus_epoch_fkey
        FOREIGN KEY (corpus_manifest_id, corpus_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_manifest_id, corpus_ledger_id);
ALTER TABLE egress_evaluation_root
    ALTER COLUMN corpus_ledger_id SET NOT NULL,
    ADD CONSTRAINT egress_evaluation_root_corpus_epoch_fkey
        FOREIGN KEY (corpus_manifest_id, corpus_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_manifest_id, corpus_ledger_id);
ALTER TABLE egress_route_disclosure
    ADD CONSTRAINT egress_route_disclosure_corpus_manifest_id_fkey
        FOREIGN KEY (corpus_manifest_id)
        REFERENCES egress_corpus_ledger_head (corpus_manifest_id);
```

Also add indexes on `egress_corpus_ledger(corpus_manifest_id)` and the two new referencing columns.

- [ ] **Step 4: Add the head table to test cleanup and run migration tests**

Insert `egress_corpus_ledger_head` in `tests/conftest.py::_TABLES` before `egress_corpus_ledger`; `TRUNCATE ... CASCADE` remains the cleanup mechanism.

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress/test_postgres_policy_successor.py -q`

Expected: populated-upgrade test passes and all old audit rows remain queryable.

- [ ] **Step 5: Add failing exact-attribution assertions**

Extend `test_first_reservation_persists_both_scopes` to query the database and assert:

```python
assert reservation_ledger_id == head_ledger_id == root_ledger_id
assert ledger_corpus_manifest_id == request.version.corpus_manifest_id
assert predecessor_ledger_id is None
```

Keep the existing no-plaintext test unchanged so the new identifiers do not weaken it.

- [ ] **Step 6: Run focused tests and verify the old corpus-keyed SQL fails**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress/test_postgres_reservation.py::test_first_reservation_persists_both_scopes -q`

Expected: fail because the runtime does not initialize or write epoch IDs.

- [ ] **Step 7: Make `_lock_corpus` initialize and lock the head first**

Add:

```python
@dataclass(frozen=True)
class _LockedCorpus:
    corpus_ledger_id: str
    usage: CorpusUsage | None
```

Change `_lock_corpus` to:

1. insert `egress_corpus_ledger_head(corpus_manifest_id, corpus_ledger_id=NULL)` on conflict do nothing;
2. select its `corpus_ledger_id FOR UPDATE`;
3. when null, generate `str(uuid.uuid4())`, insert the first ledger epoch with no predecessor and null usage, then update the head;
4. select the epoch's `corpus_usage FOR UPDATE` by `corpus_ledger_id`;
5. return `_LockedCorpus` with typed usage or `None`.

The new row insert must include `corpus_ledger_id`, `corpus_manifest_id`, and `policy_hash`; no query may select “latest” by timestamp.

- [ ] **Step 8: Thread the epoch ID through every reservation write**

Change signatures and queries exactly as follows:

```python
async def _lock_root(..., corpus_ledger_id: str) -> UsageSnapshot | None: ...
async def _write_scopes(..., corpus_ledger_id: str) -> None: ...
async def _write_reservation(..., corpus_ledger_id: str, ...) -> None: ...
```

- Insert `corpus_ledger_id` into `egress_evaluation_root` and `egress_reservation`.
- Update `egress_corpus_ledger WHERE corpus_ledger_id = %s`.
- Join replayed reservations to the corpus ledger on `r.corpus_ledger_id = c.corpus_ledger_id`.
- Select the root's stored `corpus_ledger_id`; if an existing root differs from the active epoch, raise `EgressPolicyViolation("policy_snapshot_mismatch", ...)` before any write.
- Keep route disclosures keyed by global `corpus_manifest_id`.

- [ ] **Step 9: Run reservation, recovery, concurrency, and provider-ledger integration tests**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress tests/integration/providers/test_transport_ledger_flow.py -q`

Expected: all tests pass; the existing policy-change test still returns `policy_snapshot_mismatch`.

- [ ] **Step 10: Commit the migration and epoch-aware reservation path**

```bash
git add migrations/003_egress_ledger_policy_successor.sql src/specpilot/egress/postgres.py tests/conftest.py tests/integration/egress/test_postgres_policy_successor.py tests/integration/egress/test_postgres_reservation.py tests/integration/egress/test_postgres_recovery.py
git commit -m "feat: bind reservations to ledger policy epochs"
```

---

### Task 3: Implement explicit transactional rebind

**Files:**
- Modify: `src/specpilot/egress/postgres.py`
- Modify: `tests/integration/egress/test_postgres_policy_successor.py`

**Interfaces:**
- Consumes: `successor_corpus_usage`, `PolicyRebindResult`, `PolicyRebindConflict`, and `PolicyRebindAmbiguous` from Task 1.
- Produces: `PostgresEgressLedger.rebind_policy(corpus_manifest_id: str, *, expected_policy_hash: str) -> PolicyRebindResult`.

- [ ] **Step 1: Write the failing end-to-end rebind test**

Seed one reservation under `old_book`, construct `new_book` with a changed policy, prove `new_book.check_and_reserve` first fails with `policy_snapshot_mismatch`, call rebind, then reserve a distinct disclosure under a new root. Assert:

```python
assert result.old_policy_hash == old_book_policy.policy_hash
assert result.new_policy_hash == new_book_policy.policy_hash
assert result.predecessor_ledger_id != result.successor_ledger_id
assert result.inherited_unique_excerpts == 1
assert after.corpus_usage.disclosure_ids == (first_id, second_id)
assert predecessor_usage.disclosure_ids == [first_id]
assert successor_predecessor_id == result.predecessor_ledger_id
assert head_id == result.successor_ledger_id
assert old_reservation_ledger_id == result.predecessor_ledger_id
assert new_reservation_ledger_id == result.successor_ledger_id
```

- [ ] **Step 2: Run the focused test and verify the method is missing**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress/test_postgres_policy_successor.py::test_explicit_rebind_preserves_usage_and_opens_one_successor_epoch -q`

Expected: fail because `PostgresEgressLedger.rebind_policy` does not exist.

- [ ] **Step 3: Implement the public transaction wrapper**

```python
async def rebind_policy(
    self,
    corpus_manifest_id: str,
    *,
    expected_policy_hash: str,
) -> PolicyRebindResult:
    connection = await self._connect()
    try:
        async with connection, connection.transaction():
            await _record_policy(connection, self._policy, self._policy.policy_hash)
            return await _rebind_policy(
                connection,
                corpus_manifest_id,
                expected_policy_hash=expected_policy_hash,
                new_policy_hash=self._policy.policy_hash,
            )
    except PolicyRebindConflict:
        raise
    except psycopg.OperationalError as error:
        raise PolicyRebindAmbiguous() from error
    except psycopg.Error as error:
        raise LedgerUnavailable() from error
```

- [ ] **Step 4: Implement `_rebind_policy` with head-first locking and idempotent retry**

The private function must:

1. select the head `FOR UPDATE`; missing/null head raises `PolicyRebindConflict`;
2. lock the active epoch by ID and read policy hash plus `corpus_usage`;
3. when active, expected, and new hashes are all equal, return the active epoch
   with `rebound=False` and both result IDs set to that epoch;
4. when active differs from expected, return `rebound=False` only if the active
   epoch has the new hash and directly supersedes an epoch with the expected
   hash; otherwise raise conflict;
5. when active equals expected and new differs, call `successor_corpus_usage`
   and generate one UUID;
6. insert the successor with copied JSON and normalized totals;
7. update the head using both corpus ID and old active ID in the predicate;
8. require exactly one updated row or raise conflict.

Build `PolicyRebindResult` from stored IDs and inherited totals. Do not validate the copied snapshot against the new cap during rebind; the enforcer owns cap arithmetic on the next reservation.

- [ ] **Step 5: Add failure and accounting-boundary tests**

Add focused tests proving:

- wrong `expected_policy_hash` creates no row and moves no head;
- identical rebind retry returns the same successor ID;
- rebind to the already-active policy returns `rebound is False`;
- a successor under a cap below inherited per-document usage is created, but a new-root reservation fails with `corpus_document_unique_excerpts_exceeded`;
- an old evaluation root fails after rebind even when called through the new-policy ledger;
- route disclosure rows remain present and are not duplicated by rebind.

- [ ] **Step 6: Run all successor and recovery tests**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress/test_postgres_policy_successor.py tests/integration/egress/test_postgres_recovery.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit the explicit rebind operation**

```bash
git add src/specpilot/egress/postgres.py tests/integration/egress/test_postgres_policy_successor.py
git commit -m "feat: add explicit ledger policy rebind"
```

---

### Task 4: Prove concurrency and expose the operator command

**Files:**
- Modify: `tests/integration/egress/test_postgres_policy_successor.py`
- Create: `tests/integration/cli/test_egress_rebind_policy.py`
- Modify: `src/specpilot/cli.py`

**Interfaces:**
- Consumes: `PostgresEgressLedger.rebind_policy` from Task 3.
- Produces: `specpilot egress rebind-policy --ledger-dsn --manifest-dir --corpus-manifest-id --expected-policy-hash [--policy]`.

- [ ] **Step 1: Write failing concurrency tests**

Use `asyncio.gather` to race:

1. two identical rebinds, asserting both results name one successor and the database contains exactly two epochs total;
2. one new reservation under the old policy against one rebind, asserting either the reservation is included in the successor's inherited usage or it fails after the head moves—never committed only to the predecessor after the copy;
3. two different new policies against one expected predecessor, asserting exactly one succeeds and no ledger row has two successors.

Treat only `PolicyRebindConflict` and the expected `EgressPolicyViolation` as valid loser outcomes; fail on database uniqueness errors leaking through.

- [ ] **Step 2: Run concurrency tests before any adjustment**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/egress/test_postgres_policy_successor.py -q -k concurrent`

Expected: tests expose any missing lock/retry handling; if they already pass, retain them as proof and do not add synchronization code.

- [ ] **Step 3: Make the minimal locking/retry corrections**

Keep the single lock graph `head -> active epoch -> evaluation root`. Translate unique-successor races into `PolicyRebindConflict`; do not retry a transaction automatically and do not add PostgreSQL advisory locks.

- [ ] **Step 4: Write failing CLI tests**

The integration CLI test writes the changed `fixture_policy()` to a temporary JSON file and invokes:

```python
code = main([
    "egress", "rebind-policy",
    "--ledger-dsn", clean_ledger,
    "--manifest-dir", str(tmp_path / "manifests"),
    "--policy", str(policy_path),
    "--corpus-manifest-id", corpus_id,
    "--expected-policy-hash", old_policy.policy_hash,
])
```

Assert exit 0 and parse one JSON object containing only:

```python
{
    "status": "rebound",
    "corpus_manifest_id": corpus_id,
    "predecessor_ledger_id": ANY_UUID,
    "successor_ledger_id": ANY_UUID,
    "old_policy_hash": old_policy.policy_hash,
    "new_policy_hash": new_policy.policy_hash,
    "inherited_unique_excerpts": 1,
    "inherited_unique_tokens": 2,
    "inherited_unique_bytes": 16,
}
```

Also assert a wrong expected hash returns `EXIT_REFUSED`, empty stdout, and exactly `corpus_policy_rebind_conflict` on stderr. Patch `PostgresEgressLedger.rebind_policy` to raise `PolicyRebindAmbiguous` and assert `EXIT_IO` plus exactly `policy_rebind_ambiguous`.

- [ ] **Step 5: Implement the CLI handler and parser**

Add synchronous `_egress_rebind_policy` and async `_egress_rebind_policy_async` handlers. Load `EgressPolicy.load(arguments.policy)`, construct `ManifestStore(arguments.manifest_dir)` and `PostgresEgressLedger`, await `rebind_policy`, and map:

- `PolicyRebindConflict` to `_refuse(error.code, EXIT_REFUSED)`;
- `PolicyRebindAmbiguous` and `LedgerUnavailable` to `_refuse(error.code, EXIT_IO)`;
- success to `_emit` with the exact sanitized fields above and status `rebound` or `unchanged` from `result.rebound`.

Register arguments:

```python
rebind = egress.add_parser("rebind-policy")
rebind.add_argument("--ledger-dsn", required=True)
rebind.add_argument("--manifest-dir", type=Path, required=True)
rebind.add_argument("--policy", type=Path, default=None)
rebind.add_argument("--corpus-manifest-id", type=_sha256_argument, required=True)
rebind.add_argument("--expected-policy-hash", type=_sha256_argument, required=True)
rebind.set_defaults(handler=_egress_rebind_policy)
```

Register both hash arguments with the existing `_sha256_argument` argparse type,
so malformed values fail as command-line usage before any database connection.

- [ ] **Step 6: Run CLI, concurrency, and no-plaintext tests**

Run: `SPECPILOT_TEST_DSN="$SPECPILOT_TEST_DSN" .venv/bin/python -m pytest tests/integration/cli/test_egress_rebind_policy.py tests/integration/egress/test_postgres_policy_successor.py tests/integration/egress/test_postgres_reservation.py::test_ledger_stores_no_query_claim_or_excerpt_text -q`

Expected: all tests pass and outputs contain only identifiers, counts, status, or stable refusal codes.

- [ ] **Step 7: Commit the operator surface**

```bash
git add src/specpilot/cli.py tests/integration/cli/test_egress_rebind_policy.py tests/integration/egress/test_postgres_policy_successor.py
git commit -m "feat: expose ledger policy rebind command"
```

---

### Task 5: Close Task 11 and run release-level verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md`
- Modify: `docs/handoff/2026-08-11-codex-handoff.md`

**Interfaces:**
- Consumes: the command and behavior verified in Tasks 1–4.
- Produces: operator instructions and exact completion evidence for Task 11.

- [ ] **Step 1: Document the operator workflow**

Add a README section with the exact command, explaining:

- ordinary policy mismatch remains fail-closed;
- `--expected-policy-hash` prevents rebinding the wrong head;
- the successor inherits corpus and per-document usage;
- a new evaluation-root ID is mandatory after success;
- `unchanged` is a safe retry result;
- lower new caps block later reservations rather than deleting history.

- [ ] **Step 2: Update project plans only with verified facts**

Mark Task 11 complete in the assisted-annotation plan and handoff. Record the migration number, command name, focused test command, pass counts, and commit IDs produced during execution. Preserve the original incident description as rationale; do not rewrite it as though the dead end never existed.

- [ ] **Step 3: Run static and unit verification**

Run: `make check`

Expected: Ruff, strict mypy, and all unit tests pass.

- [ ] **Step 4: Run fresh PostgreSQL integration verification**

Create or select a fresh throwaway database, export it only as `SPECPILOT_TEST_DSN`, then run:

Run: `make integration-db`

Expected: no skips caused by a missing DSN; all database, CLI, and provider-fixture integration tests pass.

- [ ] **Step 5: Run fixture smoke in a separate fresh database**

Use a second throwaway database because migrations are not designed to be replayed over the integration session.

Run: `make fixture-smoke`

Expected: all fixture-smoke tests pass with no real provider call.

- [ ] **Step 6: Run package and repository safety checks**

Run:

```bash
.venv/bin/python -m build
.venv/bin/python -m pip install --force-reinstall --no-deps dist/specpilot-*.whl
SPECPILOT_PYTHON=python make check
git diff --check
git status --ignored --short
```

Expected: wheel builds and installed-package checks pass; diff check is empty; `artifacts/restricted/`, `manifests/local/`, `data/`, and `tmp/` appear only with `!!` ignored markers and no restricted artifact is staged.

- [ ] **Step 7: Commit documentation and verification evidence**

```bash
git add README.md docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md docs/handoff/2026-08-11-codex-handoff.md
git commit -m "docs: close ledger successor task"
```

- [ ] **Step 8: Review the complete branch before publication**

Run:

```bash
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git status --short --branch
```

Expected: only Task 11 design, implementation, tests, migration, and documentation commits are ahead of `origin/main`; the worktree is clean. Do not push until this review and the user's publication instruction.
