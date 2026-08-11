from __future__ import annotations

import uuid
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

pytestmark = pytest.mark.integration


def test_upgrade_preserves_rows_allows_planning_and_rejects_unknown(
    ledger_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.errors import CheckViolation

    schema_name = f"test_planning_stage_{uuid.uuid4().hex}"
    policy_hash = "a" * 64
    corpus_manifest_id = "c" * 64
    evidence_reservation_id = str(uuid.uuid4())

    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
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
                (policy_hash, "egress-policy/v1"),
            )
            connection.execute(
                "INSERT INTO egress_corpus_ledger "
                "(corpus_manifest_id, policy_hash, corpus_usage, unique_excerpts, "
                "unique_tokens, unique_bytes) VALUES (%s, %s, '{}', 0, 0, 0)",
                (corpus_manifest_id, policy_hash),
            )
            connection.execute(
                "INSERT INTO egress_evaluation_root "
                "(evaluation_root_id, policy_hash, task_level, corpus_manifest_id, "
                "usage_snapshot) VALUES ('root-1', %s, 'L1', %s, '{}')",
                (policy_hash, corpus_manifest_id),
            )
            connection.execute(
                (MIGRATIONS_DIR / "003_egress_ledger_policy_successor.sql").read_text(
                    encoding="utf-8"
                )
            )
            _insert_reservation(
                connection,
                reservation_id=evidence_reservation_id,
                idempotency_key="evidence-1",
                stage="evidence",
                policy_hash=policy_hash,
                corpus_manifest_id=corpus_manifest_id,
            )

            connection.execute(
                (MIGRATIONS_DIR / "004_egress_planning_stage.sql").read_text(
                    encoding="utf-8"
                )
            )

            _insert_reservation(
                connection,
                reservation_id=str(uuid.uuid4()),
                idempotency_key="planning-1",
                stage="planning",
                policy_hash=policy_hash,
                corpus_manifest_id=corpus_manifest_id,
            )
            stages = connection.execute(
                "SELECT stage FROM egress_reservation ORDER BY stage"
            ).fetchall()
            assert stages == [("evidence",), ("planning",)]
            with pytest.raises(CheckViolation):
                _insert_reservation(
                    connection,
                    reservation_id=str(uuid.uuid4()),
                    idempotency_key="unknown-1",
                    stage="unknown",
                    policy_hash=policy_hash,
                    corpus_manifest_id=corpus_manifest_id,
                )
            constraint = connection.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'egress_reservation'::regclass "
                "AND conname = 'egress_reservation_stage_check'"
            ).fetchone()
            assert constraint is not None
            rendered = constraint[0]
            assert all(
                stage in rendered
                for stage in (
                    "planning",
                    "evidence",
                    "compliance",
                    "verifier",
                    "judge",
                )
            )
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )


def _insert_reservation(
    connection: object,
    *,
    reservation_id: str,
    idempotency_key: str,
    stage: str,
    policy_hash: str,
    corpus_manifest_id: str,
) -> None:
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO egress_reservation "
        "(reservation_id, idempotency_key, evaluation_root_id, run_id, "
        "policy_hash, corpus_manifest_id, corpus_ledger_id, stage, provider_id, "
        "endpoint_purpose, provider_use, model_id, state) "
        "SELECT %s, %s, 'root-1', 'run-1', %s, %s, corpus_ledger_id, %s, "
        "'provider-a', 'evidence-review', 'online_main', 'fixture-model-v1', "
        "'reserved' FROM egress_corpus_ledger_head WHERE corpus_manifest_id = %s",
        (
            reservation_id,
            idempotency_key,
            policy_hash,
            corpus_manifest_id,
            stage,
            corpus_manifest_id,
        ),
    )
