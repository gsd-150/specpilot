from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from specpilot.contracts.egress import CorpusUsage, UsageSnapshot
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.ledger import LedgerError, PolicyRebindConflict
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from tests.integration.egress.test_postgres_reservation import reservation_for
from tests.unit.egress.test_disclosure_caps import distinct_excerpt
from tests.unit.egress.test_policy_projection import (
    FIXTURE_DOCUMENT,
    NOW,
    FixtureTokenCounter,
    fixture_policy,
    fixture_store,
)

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def _ledger(dsn: str, policy: EgressPolicy) -> PostgresEgressLedger:
    return PostgresEgressLedger(
        dsn,
        policy=policy,
        manifests=fixture_store(),
        clock=lambda: NOW,
    )


async def _active_ledger_id(dsn: str, corpus_manifest_id: str) -> str:
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s",
                (corpus_manifest_id,),
            )
        ).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


async def _ledger_audit_state(dsn: str) -> tuple[object, ...]:
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        ledgers = await (
            await connection.execute(
                "SELECT corpus_ledger_id::text, corpus_manifest_id, policy_hash, "
                "corpus_usage::text, unique_excerpts, unique_tokens, unique_bytes, "
                "predecessor_ledger_id::text FROM egress_corpus_ledger "
                "ORDER BY corpus_ledger_id"
            )
        ).fetchall()
        heads = await (
            await connection.execute(
                "SELECT corpus_manifest_id, corpus_ledger_id::text "
                "FROM egress_corpus_ledger_head ORDER BY corpus_manifest_id"
            )
        ).fetchall()
        reservations = await (
            await connection.execute(
                "SELECT reservation_id::text, corpus_ledger_id::text "
                "FROM egress_reservation ORDER BY reservation_id"
            )
        ).fetchall()
        policies = await (
            await connection.execute(
                "SELECT policy_hash FROM egress_policy_snapshot ORDER BY policy_hash"
            )
        ).fetchall()
    return (ledgers, heads, reservations, policies)


def _named_dsn(dsn: str, application_name: str) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(dsn, application_name=application_name)


async def _hold_corpus_head(
    dsn: str,
    corpus_manifest_id: str,
    locked: asyncio.Event,
    release: asyncio.Event,
    holder_pid: asyncio.Future[int],
) -> None:
    import psycopg

    async with (
        await psycopg.AsyncConnection.connect(dsn) as connection,
        connection.transaction(),
    ):
        row = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s FOR UPDATE",
                (corpus_manifest_id,),
            )
        ).fetchone()
        assert row is not None and row[0] is not None
        pid_row = await (
            await connection.execute("SELECT pg_backend_pid()")
        ).fetchone()
        assert pid_row is not None
        holder_pid.set_result(int(pid_row[0]))
        locked.set()
        await release.wait()


async def _wait_for_postgres_lock(
    dsn: str,
    application_name: str,
    task: asyncio.Task[Any],
    blocker_pids: set[int],
) -> int:
    import psycopg

    # PostgreSQL may cache statistics for an observer transaction. Autocommit
    # makes every poll see newly registered contender sessions.
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True
    ) as connection:
        for _ in range(200):
            row = await (
                await connection.execute(
                    "SELECT pid, wait_event_type, query, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity "
                    "WHERE application_name = %s AND state = 'active'",
                    (application_name,),
                )
            ).fetchone()
            if (
                row is not None
                and row[1] == "Lock"
                and "egress_corpus_ledger_head" in str(row[2])
                and "FOR UPDATE" in str(row[2])
                and blocker_pids.intersection(row[3])
            ):
                assert not task.done()
                return int(row[0])
            if task.done():
                raise AssertionError(
                    f"{application_name} completed before reaching the held head lock"
                )
            await asyncio.sleep(0.01)
    raise AssertionError(f"{application_name} never waited on the held head lock")


async def _force_head_schedule(
    dsn: str,
    corpus_manifest_id: str,
    first_application_name: str,
    first: Callable[[], Awaitable[Any]],
    second_application_name: str,
    second: Callable[[], Awaitable[Any]],
) -> tuple[object, object]:
    locked = asyncio.Event()
    release = asyncio.Event()
    holder_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    holder = asyncio.create_task(
        _hold_corpus_head(dsn, corpus_manifest_id, locked, release, holder_pid)
    )
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=2)
        blocker_pid = await asyncio.wait_for(holder_pid, timeout=2)
        first_task = asyncio.ensure_future(first())
        first_pid = await _wait_for_postgres_lock(
            dsn, first_application_name, first_task, {blocker_pid}
        )
        second_task = asyncio.ensure_future(second())
        await _wait_for_postgres_lock(
            dsn,
            second_application_name,
            second_task,
            {blocker_pid, first_pid},
        )
        assert not first_task.done()
        assert not second_task.done()
    except BaseException:
        release.set()
        await holder
        started = [task for task in (first_task, second_task) if task is not None]
        if started:
            await asyncio.gather(*started, return_exceptions=True)
        raise
    release.set()
    await holder
    assert first_task is not None and second_task is not None
    outcomes = await asyncio.gather(first_task, second_task, return_exceptions=True)
    return outcomes[0], outcomes[1]


def test_migration_upgrades_a_populated_ledger_without_losing_audit_rows(
    ledger_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    schema_name = f"test_policy_successor_{uuid.uuid4().hex}"
    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    first_request = reservation_for(distinct_excerpt(1)).model_copy(
        update={"evaluation_root_id": "migrated-root", "run_id": "migrated-run"}
    )
    second_request = reservation_for(distinct_excerpt(2)).model_copy(
        update={"evaluation_root_id": "successor-root", "run_id": "successor-run"}
    )
    first_fact = first_request.disclosures[0]
    corpus_manifest_id = first_request.version.corpus_manifest_id
    reservation_id = str(uuid.uuid4())
    original = EgressPolicyEnforcer(
        old_policy,
        manifests=fixture_store(),
        clock=lambda: NOW,
    ).apply_reservation(None, None, first_request, FixtureTokenCounter())
    original_usage = original.corpus_usage
    original_snapshot = original.usage

    with psycopg.connect(
        ledger_dsn, autocommit=True, row_factory=dict_row
    ) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
        )
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name))
            )
            for migration_name in (
                "001_egress_ledger.sql",
                "002_attempt_request_size.sql",
            ):
                connection.execute(
                    (MIGRATIONS_DIR / migration_name).read_text(encoding="utf-8")
                )

            connection.execute(
                "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
                "VALUES (%s, %s)",
                (old_policy.policy_hash, old_policy.schema_version),
            )
            connection.execute(
                """
                INSERT INTO egress_corpus_ledger (
                    corpus_manifest_id, policy_hash, corpus_usage,
                    unique_excerpts, unique_tokens, unique_bytes
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    corpus_manifest_id,
                    old_policy.policy_hash,
                    Jsonb(original_usage.model_dump(mode="json")),
                    len(original_usage.disclosure_ids),
                    original_usage.unique_tokens,
                    original_usage.unique_bytes,
                ),
            )
            connection.execute(
                """
                INSERT INTO egress_evaluation_root (
                    evaluation_root_id, policy_hash, task_level,
                    corpus_manifest_id, usage_snapshot
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    first_request.evaluation_root_id,
                    old_policy.policy_hash,
                    first_request.task_level.value,
                    corpus_manifest_id,
                    Jsonb(original_snapshot.model_dump(mode="json")),
                ),
            )
            connection.execute(
                """
                INSERT INTO egress_reservation (
                    reservation_id, idempotency_key, evaluation_root_id, run_id,
                    policy_hash, corpus_manifest_id, stage, provider_id,
                    endpoint_purpose, provider_use, model_id, state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    reservation_id,
                    "migrated-key",
                    first_request.evaluation_root_id,
                    first_request.run_id,
                    old_policy.policy_hash,
                    corpus_manifest_id,
                    first_request.stage.value,
                    first_request.route.provider_id,
                    first_request.route.endpoint_purpose,
                    first_request.route.use.value,
                    first_request.model_id,
                    "reserved",
                ),
            )
            connection.execute(
                """
                INSERT INTO egress_reservation_disclosure (
                    reservation_id, disclosure_id, token_count, byte_count
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    reservation_id,
                    first_fact.disclosure_id,
                    first_fact.token_count,
                    first_fact.byte_count,
                ),
            )
            connection.execute(
                """
                INSERT INTO egress_route_disclosure (
                    corpus_manifest_id, provider_id, endpoint_purpose, disclosure_id
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    corpus_manifest_id,
                    first_request.route.provider_id,
                    first_request.route.endpoint_purpose,
                    first_fact.disclosure_id,
                ),
            )

            connection.execute(
                (MIGRATIONS_DIR / "003_egress_ledger_policy_successor.sql").read_text(
                    encoding="utf-8"
                )
            )

            migrated_ledger = connection.execute(
                "SELECT corpus_ledger_id, predecessor_ledger_id, corpus_usage "
                "FROM egress_corpus_ledger"
            ).fetchone()
            assert migrated_ledger is not None
            migrated_ledger_id = str(migrated_ledger["corpus_ledger_id"])
            schema_dsn = make_conninfo(
                ledger_dsn,
                options=f"-csearch_path={schema_name}",
            )

            async def exercise_public_api() -> tuple[object, object]:
                successor_book = _ledger(schema_dsn, new_policy)
                rebound = await successor_book.rebind_policy(
                    corpus_manifest_id,
                    expected_ledger_id=migrated_ledger_id,
                    expected_policy_hash=old_policy.policy_hash,
                )
                reservation = await successor_book.check_and_reserve(
                    second_request,
                    FixtureTokenCounter(),
                    idempotency_key="post-migration-successor",
                )
                return rebound, reservation

            rebound, successor_reservation = asyncio.run(exercise_public_api())
            ledger_rows = connection.execute(
                "SELECT corpus_ledger_id, policy_hash, predecessor_ledger_id, "
                "corpus_usage, unique_excerpts, unique_tokens, unique_bytes "
                "FROM egress_corpus_ledger ORDER BY predecessor_ledger_id NULLS FIRST"
            ).fetchall()
            head = connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s",
                (corpus_manifest_id,),
            ).fetchone()
            reservations = connection.execute(
                "SELECT reservation_id::text, corpus_ledger_id::text "
                "FROM egress_reservation ORDER BY reservation_id"
            ).fetchall()
            roots = connection.execute(
                "SELECT evaluation_root_id, corpus_ledger_id::text, usage_snapshot "
                "FROM egress_evaluation_root ORDER BY evaluation_root_id"
            ).fetchall()
            route_rows = connection.execute(
                "SELECT disclosure_id FROM egress_route_disclosure "
                "WHERE corpus_manifest_id = %s ORDER BY disclosure_id",
                (corpus_manifest_id,),
            ).fetchall()

            assert len(ledger_rows) == 2
            predecessor, successor = ledger_rows
            predecessor_usage = CorpusUsage.model_validate(predecessor["corpus_usage"])
            successor_usage = CorpusUsage.model_validate(successor["corpus_usage"])
            assert UsageSnapshot.model_validate(roots[0]["usage_snapshot"])
            assert UsageSnapshot.model_validate(roots[1]["usage_snapshot"])
            assert predecessor["predecessor_ledger_id"] is None
            assert str(successor["predecessor_ledger_id"]) == migrated_ledger_id
            assert predecessor_usage == original_usage
            assert predecessor["policy_hash"] == old_policy.policy_hash
            assert successor["policy_hash"] == new_policy.policy_hash
            assert (
                predecessor["unique_excerpts"],
                predecessor["unique_tokens"],
                predecessor["unique_bytes"],
            ) == (1, 2, 16)
            assert (
                successor["unique_excerpts"],
                successor["unique_tokens"],
                successor["unique_bytes"],
            ) == (2, 4, 32)
            assert successor_usage.disclosure_ids == (
                first_fact.disclosure_id,
                second_request.disclosures[0].disclosure_id,
            )
            assert successor_usage.document_usage[0].disclosure_ids == (
                first_fact.disclosure_id,
                second_request.disclosures[0].disclosure_id,
            )
            assert head is not None
            assert str(head["corpus_ledger_id"]) == rebound.successor_ledger_id
            reservation_epochs = {
                row["reservation_id"]: row["corpus_ledger_id"] for row in reservations
            }
            assert reservation_epochs == {
                reservation_id: migrated_ledger_id,
                successor_reservation.reservation_id: rebound.successor_ledger_id,
            }
            root_epochs = {
                row["evaluation_root_id"]: row["corpus_ledger_id"] for row in roots
            }
            assert root_epochs == {
                first_request.evaluation_root_id: migrated_ledger_id,
                second_request.evaluation_root_id: rebound.successor_ledger_id,
            }
            assert [row["disclosure_id"] for row in route_rows] == sorted(
                [
                    first_fact.disclosure_id,
                    second_request.disclosures[0].disclosure_id,
                ]
            )
            assert (
                rebound.inherited_unique_excerpts,
                rebound.inherited_unique_tokens,
                rebound.inherited_unique_bytes,
            ) == (1, 2, 16)
        finally:
            connection.rollback()
            connection.execute("SET search_path TO public")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )


@pytest.mark.anyio
async def test_cross_corpus_predecessor_is_rejected_by_epoch_lineage_fk(
    clean_ledger: str,
) -> None:
    import psycopg

    policy_hash = "c" * 64
    parent_corpus = "a" * 64
    child_corpus = "b" * 64
    parent_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    parent_usage = CorpusUsage(
        corpus_manifest_id=parent_corpus,
        policy_hash=policy_hash,
    )
    child_usage = CorpusUsage(
        corpus_manifest_id=child_corpus,
        policy_hash=policy_hash,
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
            "VALUES (%s, %s)",
            (policy_hash, "1"),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger ("
            "corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
            "unique_excerpts, unique_tokens, unique_bytes"
            ") VALUES (%s, %s, %s, %s, 0, 0, 0)",
            (
                parent_id,
                parent_corpus,
                policy_hash,
                parent_usage.model_dump_json(),
            ),
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await connection.execute(
                "INSERT INTO egress_corpus_ledger ("
                "corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
                "unique_excerpts, unique_tokens, unique_bytes, predecessor_ledger_id"
                ") VALUES (%s, %s, %s, %s, 0, 0, 0, %s)",
                (
                    child_id,
                    child_corpus,
                    policy_hash,
                    child_usage.model_dump_json(),
                    parent_id,
                ),
            )


@pytest.mark.anyio
async def test_explicit_rebind_preserves_usage_and_opens_one_successor_epoch(
    clean_ledger: str,
) -> None:
    import psycopg

    old_book_policy = fixture_policy()
    new_book_policy = old_book_policy.model_copy(update={"toc_per_run": 25})
    old_book = _ledger(clean_ledger, old_book_policy)
    new_book = _ledger(clean_ledger, new_book_policy)
    first_request = reservation_for(distinct_excerpt(1))
    second_request = reservation_for(distinct_excerpt(2)).model_copy(
        update={"evaluation_root_id": "case-2", "run_id": "run-2"}
    )
    first_id = first_request.disclosures[0].disclosure_id
    second_id = second_request.disclosures[0].disclosure_id

    first = await old_book.check_and_reserve(
        first_request,
        FixtureTokenCounter(),
        idempotency_key="old-policy-first",
    )
    with pytest.raises(EgressPolicyViolation) as caught:
        await new_book.check_and_reserve(
            second_request,
            FixtureTokenCounter(),
            idempotency_key="new-policy-before-rebind",
        )
    assert caught.value.code == "policy_snapshot_mismatch"
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, first_request.version.corpus_manifest_id
    )

    result = await new_book.rebind_policy(
        first_request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_book_policy.policy_hash,
    )
    after = await new_book.check_and_reserve(
        second_request,
        FixtureTokenCounter(),
        idempotency_key="new-policy-second",
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        predecessor = await (
            await connection.execute(
                "SELECT corpus_usage FROM egress_corpus_ledger "
                "WHERE corpus_ledger_id = %s",
                (result.predecessor_ledger_id,),
            )
        ).fetchone()
        successor = await (
            await connection.execute(
                "SELECT predecessor_ledger_id FROM egress_corpus_ledger "
                "WHERE corpus_ledger_id = %s",
                (result.successor_ledger_id,),
            )
        ).fetchone()
        head = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s",
                (first_request.version.corpus_manifest_id,),
            )
        ).fetchone()
        reservation_rows = await (
            await connection.execute(
                "SELECT reservation_id, corpus_ledger_id FROM egress_reservation "
                "WHERE reservation_id IN (%s, %s)",
                (first.reservation_id, after.reservation_id),
            )
        ).fetchall()

    assert predecessor is not None
    assert successor is not None
    assert head is not None
    reservation_ledgers = {str(row[0]): str(row[1]) for row in reservation_rows}
    predecessor_usage = predecessor[0]
    successor_predecessor_id = str(successor[0])
    head_id = str(head[0])
    old_reservation_ledger_id = reservation_ledgers[first.reservation_id]
    new_reservation_ledger_id = reservation_ledgers[after.reservation_id]

    assert result.old_policy_hash == old_book_policy.policy_hash
    assert result.new_policy_hash == new_book_policy.policy_hash
    assert result.predecessor_ledger_id != result.successor_ledger_id
    assert result.inherited_unique_excerpts == 1
    assert result.inherited_unique_tokens == 2
    assert result.inherited_unique_bytes == 16
    assert after.corpus_usage.disclosure_ids == (first_id, second_id)
    assert predecessor_usage["disclosure_ids"] == [first_id]
    assert successor_predecessor_id == result.predecessor_ledger_id
    assert head_id == result.successor_ledger_id
    assert old_reservation_ledger_id == result.predecessor_ledger_id
    assert new_reservation_ledger_id == result.successor_ledger_id


@pytest.mark.anyio
async def test_wrong_expected_policy_creates_no_epoch_and_does_not_move_head(
    clean_ledger: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    request = reservation_for(distinct_excerpt(1))
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key="wrong-expected-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, request.version.corpus_manifest_id
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        before_head = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s",
                (request.version.corpus_manifest_id,),
            )
        ).fetchone()
        before_count = await (
            await connection.execute("SELECT count(*) FROM egress_corpus_ledger")
        ).fetchone()

    with pytest.raises(PolicyRebindConflict):
        await _ledger(clean_ledger, new_policy).rebind_policy(
            request.version.corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash="f" * 64,
        )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        after_head = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s",
                (request.version.corpus_manifest_id,),
            )
        ).fetchone()
        after_count = await (
            await connection.execute("SELECT count(*) FROM egress_corpus_ledger")
        ).fetchone()

    assert before_head is not None and after_head is not None
    assert before_count is not None and after_count is not None
    assert after_head[0] == before_head[0]
    assert after_count[0] == before_count[0] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["rebind", "reservation", "retry"])
@pytest.mark.parametrize(
    "tamper",
    [
        "json_corpus_manifest_id",
        "json_policy_hash",
        "row_unique_excerpts",
        "row_unique_tokens",
        "row_unique_bytes",
        "malformed_json",
    ],
)
async def test_tampered_corpus_accounting_fails_closed_without_mutation(
    clean_ledger: str,
    operation: str,
    tamper: str,
) -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    first_request = reservation_for(distinct_excerpt(1)).model_copy(
        update={"evaluation_root_id": "integrity-seed", "run_id": "integrity-run"}
    )
    second_request = reservation_for(distinct_excerpt(2)).model_copy(
        update={"evaluation_root_id": "integrity-next", "run_id": "integrity-next"}
    )
    old_book = _ledger(clean_ledger, old_policy)
    await old_book.check_and_reserve(
        first_request,
        FixtureTokenCounter(),
        idempotency_key=f"integrity-seed-{operation}-{tamper}",
    )
    corpus_manifest_id = first_request.version.corpus_manifest_id
    expected_ledger_id = await _active_ledger_id(clean_ledger, corpus_manifest_id)
    new_book = _ledger(clean_ledger, new_policy)
    if operation == "retry":
        await new_book.rebind_policy(
            corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash=old_policy.policy_hash,
        )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        row = await (
            await connection.execute(
                "SELECT corpus_usage, unique_excerpts, unique_tokens, unique_bytes "
                "FROM egress_corpus_ledger WHERE corpus_ledger_id = %s",
                (expected_ledger_id,),
            )
        ).fetchone()
        assert row is not None
        usage = dict(row[0])
        if tamper == "json_corpus_manifest_id":
            assert usage["corpus_manifest_id"] != "f" * 64
            usage["corpus_manifest_id"] = "f" * 64
            await connection.execute(
                "UPDATE egress_corpus_ledger SET corpus_usage = %s "
                "WHERE corpus_ledger_id = %s",
                (Jsonb(usage), expected_ledger_id),
            )
        elif tamper == "json_policy_hash":
            assert usage["policy_hash"] != "f" * 64
            usage["policy_hash"] = "f" * 64
            await connection.execute(
                "UPDATE egress_corpus_ledger SET corpus_usage = %s "
                "WHERE corpus_ledger_id = %s",
                (Jsonb(usage), expected_ledger_id),
            )
        elif tamper == "row_unique_excerpts":
            await connection.execute(
                "UPDATE egress_corpus_ledger SET unique_excerpts = %s "
                "WHERE corpus_ledger_id = %s",
                (int(row[1]) + 1, expected_ledger_id),
            )
        elif tamper == "row_unique_tokens":
            await connection.execute(
                "UPDATE egress_corpus_ledger SET unique_tokens = %s "
                "WHERE corpus_ledger_id = %s",
                (int(row[2]) + 1, expected_ledger_id),
            )
        elif tamper == "row_unique_bytes":
            await connection.execute(
                "UPDATE egress_corpus_ledger SET unique_bytes = %s "
                "WHERE corpus_ledger_id = %s",
                (int(row[3]) + 1, expected_ledger_id),
            )
        else:
            await connection.execute(
                "UPDATE egress_corpus_ledger SET corpus_usage = %s "
                "WHERE corpus_ledger_id = %s",
                (Jsonb({"secret": "tamper-sentinel"}), expected_ledger_id),
            )
        await connection.commit()

    before = await _ledger_audit_state(clean_ledger)
    with pytest.raises(LedgerError) as caught:
        if operation in {"rebind", "retry"}:
            await new_book.rebind_policy(
                corpus_manifest_id,
                expected_ledger_id=expected_ledger_id,
                expected_policy_hash=old_policy.policy_hash,
            )
        else:
            await old_book.check_and_reserve(
                second_request,
                FixtureTokenCounter(),
                idempotency_key=f"integrity-next-{tamper}",
            )
    after = await _ledger_audit_state(clean_ledger)

    assert caught.value.code == "ledger_integrity_error"
    assert after == before


@pytest.mark.anyio
async def test_replay_refuses_a_reservation_bound_to_another_ledger_epoch(
    clean_ledger: str,
) -> None:
    import psycopg

    policy = fixture_policy()
    request = reservation_for(distinct_excerpt(1)).model_copy(
        update={"evaluation_root_id": "integrity-replay", "run_id": "replay-run"}
    )
    idempotency_key = "integrity-replay-key"
    book = _ledger(clean_ledger, policy)
    reservation = await book.check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key=idempotency_key,
    )
    alternate_ledger_id = str(uuid.uuid4())
    alternate_usage = CorpusUsage(
        corpus_manifest_id=request.version.corpus_manifest_id,
        policy_hash=policy.policy_hash,
    )
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "INSERT INTO egress_corpus_ledger ("
            "corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
            "unique_excerpts, unique_tokens, unique_bytes"
            ") VALUES (%s, %s, %s, %s, 0, 0, 0)",
            (
                alternate_ledger_id,
                request.version.corpus_manifest_id,
                policy.policy_hash,
                alternate_usage.model_dump_json(),
            ),
        )
        await connection.execute(
            "UPDATE egress_reservation SET corpus_ledger_id = %s "
            "WHERE reservation_id = %s",
            (alternate_ledger_id, reservation.reservation_id),
        )
        await connection.commit()

    before = await _ledger_audit_state(clean_ledger)
    with pytest.raises(LedgerError) as caught:
        await book.check_and_reserve(
            request,
            FixtureTokenCounter(),
            idempotency_key=idempotency_key,
        )
    after = await _ledger_audit_state(clean_ledger)

    assert caught.value.code == "ledger_integrity_error"
    assert after == before


@pytest.mark.anyio
async def test_identical_rebind_retry_returns_the_same_successor(
    clean_ledger: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    request = reservation_for(distinct_excerpt(1))
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key="retry-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, request.version.corpus_manifest_id
    )
    book = _ledger(clean_ledger, new_policy)

    first = await book.rebind_policy(
        request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )
    retry = await book.rebind_policy(
        request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        count = await (
            await connection.execute("SELECT count(*) FROM egress_corpus_ledger")
        ).fetchone()

    assert count is not None and count[0] == 2
    assert first.rebound is True
    assert retry.rebound is False
    assert retry.predecessor_ledger_id == first.predecessor_ledger_id
    assert retry.successor_ledger_id == first.successor_ledger_id


@pytest.mark.anyio
async def test_concurrent_identical_rebinds_name_one_successor_epoch(
    clean_ledger: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    request = reservation_for(distinct_excerpt(1))
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key="concurrent-identical-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, request.version.corpus_manifest_id
    )
    # Both contenders must reach the held head row. Without this committed
    # snapshot, the second one can wait on the first one's same-hash insert
    # instead, which would not prove successor serialization at the head.
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
            "VALUES (%s, %s) ON CONFLICT (policy_hash) DO NOTHING",
            (new_policy.policy_hash, new_policy.schema_version),
        )
        await connection.commit()
    prefix = f"task11-identical-{uuid.uuid4().hex}"
    first_book = _ledger(_named_dsn(clean_ledger, f"{prefix}-first"), new_policy)
    second_book = _ledger(_named_dsn(clean_ledger, f"{prefix}-second"), new_policy)

    first, second = await _force_head_schedule(
        clean_ledger,
        request.version.corpus_manifest_id,
        f"{prefix}-first",
        lambda: first_book.rebind_policy(
            request.version.corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash=old_policy.policy_hash,
        ),
        f"{prefix}-second",
        lambda: second_book.rebind_policy(
            request.version.corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash=old_policy.policy_hash,
        ),
    )
    assert not isinstance(first, BaseException), first
    assert not isinstance(second, BaseException), second

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        rows = await (
            await connection.execute(
                "SELECT predecessor_ledger_id, corpus_usage, unique_excerpts, "
                "unique_tokens, unique_bytes FROM egress_corpus_ledger "
                "WHERE corpus_manifest_id = %s",
                (request.version.corpus_manifest_id,),
            )
        ).fetchall()

    assert len(rows) == 2
    assert first.rebound is True
    assert second.rebound is False
    assert first.predecessor_ledger_id == second.predecessor_ledger_id
    assert first.successor_ledger_id == second.successor_ledger_id
    assert sum(row[0] is not None for row in rows) == 1
    for row in rows:
        usage = row[1]
        assert usage["document_usage"][0]["disclosure_ids"] == [
            request.disclosures[0].disclosure_id
        ]
        assert row[2:] == (1, 2, 16)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "schedule", ["reservation_before_rebind", "rebind_before_reservation"]
)
async def test_forced_reservation_rebind_schedule_preserves_accounting(
    clean_ledger: str,
    schedule: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    first_request = reservation_for(distinct_excerpt(1))
    second_request = reservation_for(distinct_excerpt(2)).model_copy(
        update={"evaluation_root_id": "concurrent-case-2", "run_id": "run-2"}
    )
    first_id = first_request.disclosures[0].disclosure_id
    second_id = second_request.disclosures[0].disclosure_id
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        first_request,
        FixtureTokenCounter(),
        idempotency_key=f"{schedule}-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, first_request.version.corpus_manifest_id
    )
    schedule_code = (
        "reserve-first" if schedule == "reservation_before_rebind" else "rebind-first"
    )
    prefix = f"t11-{schedule_code}-{uuid.uuid4().hex[:12]}"
    reservation_name = f"{prefix}-reservation"
    rebind_name = f"{prefix}-rebind"
    reservation_book = _ledger(
        _named_dsn(clean_ledger, reservation_name), old_policy
    )
    rebind_book = _ledger(_named_dsn(clean_ledger, rebind_name), new_policy)

    def reservation() -> Awaitable[Any]:
        return reservation_book.check_and_reserve(
            second_request,
            FixtureTokenCounter(),
            idempotency_key=f"{schedule}-racer",
        )

    def rebind() -> Awaitable[Any]:
        return rebind_book.rebind_policy(
            first_request.version.corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash=old_policy.policy_hash,
        )

    if schedule == "reservation_before_rebind":
        reservation_or_error, rebind_or_error = await _force_head_schedule(
            clean_ledger,
            first_request.version.corpus_manifest_id,
            reservation_name,
            reservation,
            rebind_name,
            rebind,
        )
    else:
        rebind_or_error, reservation_or_error = await _force_head_schedule(
            clean_ledger,
            first_request.version.corpus_manifest_id,
            rebind_name,
            rebind,
            reservation_name,
            reservation,
        )

    assert not isinstance(rebind_or_error, BaseException), rebind_or_error
    result = rebind_or_error
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        predecessor = await (
            await connection.execute(
                "SELECT corpus_usage, unique_excerpts, unique_tokens, unique_bytes "
                "FROM egress_corpus_ledger "
                "WHERE corpus_ledger_id = %s",
                (result.predecessor_ledger_id,),
            )
        ).fetchone()
        successor = await (
            await connection.execute(
                "SELECT corpus_usage, unique_excerpts, unique_tokens, unique_bytes "
                "FROM egress_corpus_ledger "
                "WHERE corpus_ledger_id = %s",
                (result.successor_ledger_id,),
            )
        ).fetchone()
        reservation_rows = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_reservation "
                "WHERE idempotency_key = %s",
                (f"{schedule}-racer",),
            )
        ).fetchall()
        lineage = await (
            await connection.execute(
                "SELECT corpus_ledger_id, predecessor_ledger_id "
                "FROM egress_corpus_ledger WHERE corpus_manifest_id = %s",
                (first_request.version.corpus_manifest_id,),
            )
        ).fetchall()
        head = await (
            await connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
                "WHERE corpus_manifest_id = %s",
                (first_request.version.corpus_manifest_id,),
            )
        ).fetchone()

    assert predecessor is not None and successor is not None
    if schedule == "rebind_before_reservation":
        assert isinstance(reservation_or_error, EgressPolicyViolation)
        assert reservation_or_error.code == "policy_snapshot_mismatch"
        assert reservation_rows == []
        expected_ids = [first_id]
        expected_totals = (1, 2, 16)
    else:
        assert not isinstance(reservation_or_error, BaseException)
        assert len(reservation_rows) == 1
        assert str(reservation_rows[0][0]) == result.predecessor_ledger_id
        expected_ids = [first_id, second_id]
        expected_totals = (2, 4, 32)

    expected_document_usage = [
        {
            "document_id": FIXTURE_DOCUMENT,
            "disclosure_ids": expected_ids,
            "unique_tokens": expected_totals[1],
            "unique_bytes": expected_totals[2],
        }
    ]
    predecessor_usage = predecessor[0]
    successor_usage = successor[0]
    assert predecessor_usage["corpus_manifest_id"] == (
        first_request.version.corpus_manifest_id
    )
    assert successor_usage["corpus_manifest_id"] == (
        first_request.version.corpus_manifest_id
    )
    assert predecessor_usage["policy_hash"] == old_policy.policy_hash
    assert successor_usage["policy_hash"] == new_policy.policy_hash
    assert predecessor_usage["disclosure_ids"] == expected_ids
    assert successor_usage["disclosure_ids"] == expected_ids
    assert predecessor_usage["unique_tokens"] == expected_totals[1]
    assert successor_usage["unique_tokens"] == expected_totals[1]
    assert predecessor_usage["unique_bytes"] == expected_totals[2]
    assert successor_usage["unique_bytes"] == expected_totals[2]
    assert predecessor_usage["document_usage"] == expected_document_usage
    assert successor_usage["document_usage"] == expected_document_usage
    assert predecessor[1:] == expected_totals
    assert successor[1:] == expected_totals
    assert (
        result.inherited_unique_excerpts,
        result.inherited_unique_tokens,
        result.inherited_unique_bytes,
    ) == expected_totals
    assert len(lineage) == 2
    assert sum(row[1] is not None for row in lineage) == 1
    assert head is not None and str(head[0]) == result.successor_ledger_id


@pytest.mark.anyio
async def test_concurrent_different_rebinds_allow_one_successor_per_predecessor(
    clean_ledger: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    first_new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    second_new_policy = old_policy.model_copy(update={"toc_per_run": 23})
    request = reservation_for(distinct_excerpt(1))
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key="concurrent-different-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, request.version.corpus_manifest_id
    )
    prefix = f"task11-different-{uuid.uuid4().hex}"
    first_name = f"{prefix}-first"
    second_name = f"{prefix}-second"
    first_book = _ledger(_named_dsn(clean_ledger, first_name), first_new_policy)
    second_book = _ledger(_named_dsn(clean_ledger, second_name), second_new_policy)

    first, second = await _force_head_schedule(
        clean_ledger,
        request.version.corpus_manifest_id,
        first_name,
        lambda: first_book.rebind_policy(
            request.version.corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash=old_policy.policy_hash,
        ),
        second_name,
        lambda: second_book.rebind_policy(
            request.version.corpus_manifest_id,
            expected_ledger_id=expected_ledger_id,
            expected_policy_hash=old_policy.policy_hash,
        ),
    )
    assert not isinstance(first, BaseException), first
    assert isinstance(second, PolicyRebindConflict)

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        duplicate_successors = await (
            await connection.execute(
                "SELECT predecessor_ledger_id FROM egress_corpus_ledger "
                "WHERE predecessor_ledger_id IS NOT NULL "
                "GROUP BY predecessor_ledger_id HAVING count(*) > 1"
            )
        ).fetchall()
        rows = await (
            await connection.execute(
                "SELECT policy_hash, corpus_usage, unique_excerpts, unique_tokens, "
                "unique_bytes FROM egress_corpus_ledger "
                "WHERE corpus_manifest_id = %s",
                (request.version.corpus_manifest_id,),
            )
        ).fetchall()

    assert duplicate_successors == []
    assert len(rows) == 2
    assert {row[0] for row in rows} == {
        old_policy.policy_hash,
        first_new_policy.policy_hash,
    }
    for row in rows:
        assert row[1]["document_usage"][0]["disclosure_ids"] == [
            request.disclosures[0].disclosure_id
        ]
        assert row[2:] == (1, 2, 16)


@pytest.mark.anyio
async def test_retry_after_successor_usage_reports_original_inherited_totals(
    clean_ledger: str,
) -> None:
    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    first_request = reservation_for(distinct_excerpt(1))
    second_request = reservation_for(distinct_excerpt(2)).model_copy(
        update={"evaluation_root_id": "retry-case-2", "run_id": "retry-run-2"}
    )
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        first_request,
        FixtureTokenCounter(),
        idempotency_key="retry-after-usage-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, first_request.version.corpus_manifest_id
    )
    book = _ledger(clean_ledger, new_policy)
    first = await book.rebind_policy(
        first_request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )
    successor_reservation = await book.check_and_reserve(
        second_request,
        FixtureTokenCounter(),
        idempotency_key="retry-after-usage-successor",
    )

    retry = await book.rebind_policy(
        first_request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )

    assert len(successor_reservation.corpus_usage.disclosure_ids) == 2
    assert (
        first.inherited_unique_excerpts,
        first.inherited_unique_tokens,
        first.inherited_unique_bytes,
    ) == (1, 2, 16)
    assert (
        retry.inherited_unique_excerpts,
        retry.inherited_unique_tokens,
        retry.inherited_unique_bytes,
    ) == (1, 2, 16)


@pytest.mark.anyio
async def test_rebind_to_the_active_policy_returns_the_active_epoch_unchanged(
    clean_ledger: str,
) -> None:
    import psycopg

    policy = fixture_policy()
    request = reservation_for(distinct_excerpt(1))
    book = _ledger(clean_ledger, policy)
    await book.check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key="active-policy-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, request.version.corpus_manifest_id
    )

    result = await book.rebind_policy(
        request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=policy.policy_hash,
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        count = await (
            await connection.execute("SELECT count(*) FROM egress_corpus_ledger")
        ).fetchone()

    assert count is not None and count[0] == 1
    assert result.rebound is False
    assert result.predecessor_ledger_id == result.successor_ledger_id
    assert result.old_policy_hash == result.new_policy_hash == policy.policy_hash


@pytest.mark.anyio
async def test_rebind_accepts_inherited_usage_above_the_new_document_cap(
    clean_ledger: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    below_inherited = {"excerpts": 0, "tokens": 524288, "bytes": 8388608}
    new_policy = fixture_policy(**{FIXTURE_DOCUMENT: below_inherited})
    first_request = reservation_for(distinct_excerpt(1))
    second_request = reservation_for(distinct_excerpt(2)).model_copy(
        update={"evaluation_root_id": "tight-case-2", "run_id": "tight-run-2"}
    )
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        first_request,
        FixtureTokenCounter(),
        idempotency_key="tight-policy-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, first_request.version.corpus_manifest_id
    )
    new_book = _ledger(clean_ledger, new_policy)

    result = await new_book.rebind_policy(
        first_request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        stored = await (
            await connection.execute(
                "SELECT corpus_usage, unique_excerpts, unique_tokens, unique_bytes "
                "FROM egress_corpus_ledger WHERE corpus_ledger_id = %s",
                (result.successor_ledger_id,),
            )
        ).fetchone()

    assert stored is not None
    stored_usage = stored[0]
    assert result.rebound is True
    assert result.inherited_unique_excerpts == 1
    assert stored[1:] == (1, 2, 16)
    assert stored_usage["policy_hash"] == new_policy.policy_hash
    assert stored_usage["disclosure_ids"] == [
        first_request.disclosures[0].disclosure_id
    ]
    assert stored_usage["document_usage"][0]["disclosure_ids"] == [
        first_request.disclosures[0].disclosure_id
    ]
    with pytest.raises(EgressPolicyViolation) as caught:
        await new_book.check_and_reserve(
            second_request,
            FixtureTokenCounter(),
            idempotency_key="tight-policy-after-rebind",
        )
    assert caught.value.code == "corpus_document_unique_excerpts_exceeded"


@pytest.mark.anyio
async def test_old_evaluation_root_cannot_cross_the_rebind_boundary(
    clean_ledger: str,
) -> None:
    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    first_request = reservation_for(distinct_excerpt(1))
    old_root_request = reservation_for(distinct_excerpt(2))
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        first_request,
        FixtureTokenCounter(),
        idempotency_key="old-root-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, first_request.version.corpus_manifest_id
    )
    new_book = _ledger(clean_ledger, new_policy)
    await new_book.rebind_policy(
        first_request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        await new_book.check_and_reserve(
            old_root_request,
            FixtureTokenCounter(),
            idempotency_key="old-root-after-rebind",
        )

    assert caught.value.code == "policy_snapshot_mismatch"


@pytest.mark.anyio
async def test_rebind_preserves_route_disclosures_without_duplication(
    clean_ledger: str,
) -> None:
    import psycopg

    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    request = reservation_for(distinct_excerpt(1))
    await _ledger(clean_ledger, old_policy).check_and_reserve(
        request,
        FixtureTokenCounter(),
        idempotency_key="route-row-seed",
    )
    expected_ledger_id = await _active_ledger_id(
        clean_ledger, request.version.corpus_manifest_id
    )

    await _ledger(clean_ledger, new_policy).rebind_policy(
        request.version.corpus_manifest_id,
        expected_ledger_id=expected_ledger_id,
        expected_policy_hash=old_policy.policy_hash,
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        route_rows = await (
            await connection.execute(
                "SELECT disclosure_id FROM egress_route_disclosure "
                "WHERE corpus_manifest_id = %s",
                (request.version.corpus_manifest_id,),
            )
        ).fetchall()

    assert route_rows == [(request.disclosures[0].disclosure_id,)]


@pytest.mark.anyio
async def test_repeated_policy_epoch_uses_ledger_identity_and_inherits_all_usage(
    clean_ledger: str,
) -> None:
    import psycopg

    policy_a = fixture_policy()
    policy_b = policy_a.model_copy(update={"toc_per_run": 25})
    requests = [
        reservation_for(distinct_excerpt(index)).model_copy(
            update={
                "evaluation_root_id": f"repeat-policy-case-{index}",
                "run_id": f"repeat-policy-run-{index}",
            }
        )
        for index in (1, 2, 3)
    ]
    corpus_manifest_id = requests[0].version.corpus_manifest_id
    ledger_a = _ledger(clean_ledger, policy_a)
    ledger_b = _ledger(clean_ledger, policy_b)

    first_reservation = await ledger_a.check_and_reserve(
        requests[0],
        FixtureTokenCounter(),
        idempotency_key="repeat-policy-a1",
    )
    epoch_a1 = await _active_ledger_id(clean_ledger, corpus_manifest_id)
    to_b = await ledger_b.rebind_policy(
        corpus_manifest_id,
        expected_ledger_id=epoch_a1,
        expected_policy_hash=policy_a.policy_hash,
    )
    second_reservation = await ledger_b.check_and_reserve(
        requests[1],
        FixtureTokenCounter(),
        idempotency_key="repeat-policy-b",
    )
    to_a2 = await ledger_a.rebind_policy(
        corpus_manifest_id,
        expected_ledger_id=to_b.successor_ledger_id,
        expected_policy_hash=policy_b.policy_hash,
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        before_stale = await (
            await connection.execute(
                "SELECT head.corpus_ledger_id, "
                "count(DISTINCT ledger.corpus_ledger_id), "
                "count(DISTINCT reservation.reservation_id) "
                "FROM egress_corpus_ledger_head AS head "
                "JOIN egress_corpus_ledger AS ledger "
                "ON ledger.corpus_manifest_id = head.corpus_manifest_id "
                "LEFT JOIN egress_reservation AS reservation "
                "ON reservation.corpus_manifest_id = head.corpus_manifest_id "
                "WHERE head.corpus_manifest_id = %s "
                "GROUP BY head.corpus_ledger_id",
                (corpus_manifest_id,),
            )
        ).fetchone()

    with pytest.raises(PolicyRebindConflict):
        await ledger_b.rebind_policy(
            corpus_manifest_id,
            expected_ledger_id=epoch_a1,
            expected_policy_hash=policy_a.policy_hash,
        )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        after_stale = await (
            await connection.execute(
                "SELECT head.corpus_ledger_id, "
                "count(DISTINCT ledger.corpus_ledger_id), "
                "count(DISTINCT reservation.reservation_id) "
                "FROM egress_corpus_ledger_head AS head "
                "JOIN egress_corpus_ledger AS ledger "
                "ON ledger.corpus_manifest_id = head.corpus_manifest_id "
                "LEFT JOIN egress_reservation AS reservation "
                "ON reservation.corpus_manifest_id = head.corpus_manifest_id "
                "WHERE head.corpus_manifest_id = %s "
                "GROUP BY head.corpus_ledger_id",
                (corpus_manifest_id,),
            )
        ).fetchone()

    assert before_stale == after_stale
    third_reservation = await ledger_a.check_and_reserve(
        requests[2],
        FixtureTokenCounter(),
        idempotency_key="repeat-policy-a2",
    )

    epoch_ids = (epoch_a1, to_b.successor_ledger_id, to_a2.successor_ledger_id)
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        rows = await (
            await connection.execute(
                "SELECT corpus_ledger_id, policy_hash, predecessor_ledger_id, "
                "corpus_usage, unique_excerpts, unique_tokens, unique_bytes "
                "FROM egress_corpus_ledger WHERE corpus_manifest_id = %s",
                (corpus_manifest_id,),
            )
        ).fetchall()
        reservation_rows = await (
            await connection.execute(
                "SELECT reservation_id, corpus_ledger_id FROM egress_reservation "
                "WHERE corpus_manifest_id = %s",
                (corpus_manifest_id,),
            )
        ).fetchall()

    by_id = {str(row[0]): row for row in rows}
    reservation_epochs = {str(row[0]): str(row[1]) for row in reservation_rows}
    disclosure_ids = [request.disclosures[0].disclosure_id for request in requests]

    assert len(set(epoch_ids)) == 3
    assert [str(by_id[epoch_id][1]) for epoch_id in epoch_ids] == [
        policy_a.policy_hash,
        policy_b.policy_hash,
        policy_a.policy_hash,
    ]
    assert by_id[epoch_a1][2] is None
    assert str(by_id[to_b.successor_ledger_id][2]) == epoch_a1
    assert str(by_id[to_a2.successor_ledger_id][2]) == to_b.successor_ledger_id
    for epoch_id, expected_count in zip(epoch_ids, (1, 2, 3), strict=True):
        row = by_id[epoch_id]
        usage = row[3]
        assert usage["disclosure_ids"] == disclosure_ids[:expected_count]
        assert usage["unique_tokens"] == 2 * expected_count
        assert usage["unique_bytes"] == 16 * expected_count
        assert usage["document_usage"] == [
            {
                "document_id": FIXTURE_DOCUMENT,
                "disclosure_ids": disclosure_ids[:expected_count],
                "unique_tokens": 2 * expected_count,
                "unique_bytes": 16 * expected_count,
            }
        ]
        assert row[4:] == (expected_count, 2 * expected_count, 16 * expected_count)
    assert reservation_epochs == {
        first_reservation.reservation_id: epoch_a1,
        second_reservation.reservation_id: to_b.successor_ledger_id,
        third_reservation.reservation_id: to_a2.successor_ledger_id,
    }
