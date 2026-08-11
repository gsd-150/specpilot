from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import PolicyRebindConflict
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


def test_migration_upgrades_a_populated_ledger_without_losing_audit_rows(
    ledger_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    schema_name = f"test_policy_successor_{uuid.uuid4().hex}"
    corpus_manifest_id = "a" * 64
    policy_hash = "b" * 64
    disclosure_id = "c" * 64
    reservation_id = str(uuid.uuid4())
    original_usage = {
        "disclosure_ids": [disclosure_id],
        "unique_tokens": 7,
        "unique_bytes": 41,
        "document_usage": [],
    }

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
                (policy_hash, "1"),
            )
            connection.execute(
                """
                INSERT INTO egress_corpus_ledger (
                    corpus_manifest_id, policy_hash, corpus_usage,
                    unique_excerpts, unique_tokens, unique_bytes
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (corpus_manifest_id, policy_hash, Jsonb(original_usage), 1, 7, 41),
            )
            connection.execute(
                """
                INSERT INTO egress_evaluation_root (
                    evaluation_root_id, policy_hash, task_level,
                    corpus_manifest_id, usage_snapshot
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                ("root-1", policy_hash, "L1", corpus_manifest_id, Jsonb({})),
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
                    "key-1",
                    "root-1",
                    "run-1",
                    policy_hash,
                    corpus_manifest_id,
                    "evidence",
                    "provider-1",
                    "main",
                    "online_main",
                    "model-1",
                    "reserved",
                ),
            )
            connection.execute(
                """
                INSERT INTO egress_reservation_disclosure (
                    reservation_id, disclosure_id, token_count, byte_count
                ) VALUES (%s, %s, %s, %s)
                """,
                (reservation_id, disclosure_id, 7, 41),
            )
            connection.execute(
                """
                INSERT INTO egress_route_disclosure (
                    corpus_manifest_id, provider_id, endpoint_purpose, disclosure_id
                ) VALUES (%s, %s, %s, %s)
                """,
                (corpus_manifest_id, "provider-1", "main", disclosure_id),
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
            migrated_head = connection.execute(
                "SELECT corpus_ledger_id FROM egress_corpus_ledger_head"
            ).fetchone()
            migrated_reservation = connection.execute(
                "SELECT corpus_ledger_id FROM egress_reservation"
            ).fetchone()
            migrated_root = connection.execute(
                "SELECT corpus_ledger_id FROM egress_evaluation_root"
            ).fetchone()
            migrated_route_count = connection.execute(
                "SELECT count(*) AS count FROM egress_route_disclosure"
            ).fetchone()

            assert migrated_ledger is not None
            assert migrated_head is not None
            assert migrated_reservation is not None
            assert migrated_root is not None
            assert migrated_route_count is not None
            assert migrated_ledger["predecessor_ledger_id"] is None
            assert (
                migrated_head["corpus_ledger_id"] == migrated_ledger["corpus_ledger_id"]
            )
            assert (
                migrated_reservation["corpus_ledger_id"]
                == migrated_ledger["corpus_ledger_id"]
            )
            assert (
                migrated_root["corpus_ledger_id"] == migrated_ledger["corpus_ledger_id"]
            )
            assert migrated_ledger["corpus_usage"] == original_usage
            assert migrated_route_count["count"] == 1
        finally:
            connection.rollback()
            connection.execute("SET search_path TO public")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
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

    result = await new_book.rebind_policy(
        first_request.version.corpus_manifest_id,
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
    book = _ledger(clean_ledger, new_policy)

    first = await book.rebind_policy(
        request.version.corpus_manifest_id,
        expected_policy_hash=old_policy.policy_hash,
    )
    retry = await book.rebind_policy(
        request.version.corpus_manifest_id,
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

    result = await book.rebind_policy(
        request.version.corpus_manifest_id,
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
    new_book = _ledger(clean_ledger, new_policy)

    result = await new_book.rebind_policy(
        first_request.version.corpus_manifest_id,
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
    new_book = _ledger(clean_ledger, new_policy)
    await new_book.rebind_policy(
        first_request.version.corpus_manifest_id,
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

    await _ledger(clean_ledger, new_policy).rebind_policy(
        request.version.corpus_manifest_id,
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
