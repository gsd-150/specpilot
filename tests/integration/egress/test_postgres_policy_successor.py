from __future__ import annotations

import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


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
