from __future__ import annotations

import json
import re
import uuid

import pytest

pytestmark = pytest.mark.integration

_STATUSES = {
    "queued",
    "running",
    "answered",
    "refused",
    "egress_blocked",
    "failed",
    "interrupted",
}
_EVENT_KINDS = {
    "state_transition",
    "plan_summary",
    "agent_step",
    "tool_finished",
    "candidate_summary",
    "evidence_summary",
    "egress_summary",
    "usage_summary",
    "answer_outcome",
    "verifier_summary",
    "terminal",
}


def _constraint_definition(connection: object, table: str, name: str) -> str:
    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = %s::regclass AND conname = %s",
        (table, name),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _key_columns(connection: object, table: str, contype: str) -> tuple[str, ...]:
    rows = connection.execute(  # type: ignore[attr-defined]
        "SELECT a.attname FROM pg_constraint c "
        "JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.conrelid = %s::regclass AND c.contype = %s ORDER BY k.ord",
        (table, contype),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _insert_run(connection: object, *, status: str = "queued") -> uuid.UUID:
    policy_hash = "a" * 64
    corpus_manifest_id = "b" * 64
    corpus_ledger_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    run_id = uuid.uuid4()
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
        "VALUES (%s, 'egress-policy/v1') ON CONFLICT DO NOTHING",
        (policy_hash,),
    )
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO egress_corpus_ledger "
        "(corpus_manifest_id, policy_hash, corpus_usage, unique_excerpts, "
        "unique_tokens, unique_bytes, corpus_ledger_id) "
        "VALUES (%s, %s, '{}', 0, 0, 0, %s) "
        "ON CONFLICT DO NOTHING",
        (corpus_manifest_id, policy_hash, corpus_ledger_id),
    )
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO egress_corpus_ledger_head "
        "(corpus_manifest_id, corpus_ledger_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (corpus_manifest_id, corpus_ledger_id),
    )
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO specpilot_run "
        "(run_id, request_id, session_id, task_level, profile, source_manifest_id, "
        "corpus_manifest_id, policy_hash, configuration_hash, prompt_id, "
        "prompt_hash, provider_id, model_id, query_hash, status, lease_owner, "
        "lease_expires_at) VALUES "
        "(%s, %s, 'session-a', 'L1', 'fixture', %s, %s, %s, %s, "
        "'l1-answer-v1', %s, 'provider-a', 'model-a', %s, %s, "
        "'queue-delivery', now() + interval '30 seconds')",
        (
            run_id,
            uuid.uuid4(),
            "c" * 64,
            corpus_manifest_id,
            policy_hash,
            "d" * 64,
            "e" * 64,
            "f" * 64,
            status,
        ),
    )
    return run_id


def test_migration_creates_exact_keys_foreign_keys_and_safe_columns(
    migrated_dsn: str,
) -> None:
    import psycopg

    with psycopg.connect(migrated_dsn) as connection:
        assert _key_columns(connection, "specpilot_run", "p") == ("run_id",)
        assert _key_columns(connection, "specpilot_run_event", "p") == (
            "run_id",
            "sequence",
        )
        assert _key_columns(connection, "specpilot_run_event", "f") == ("run_id",)

        columns = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'specpilot_run'"
            ).fetchall()
        }
        assert columns == {
            "run_id": ("uuid", "NO"),
            "request_id": ("uuid", "NO"),
            "session_id": ("text", "NO"),
            "task_level": ("text", "NO"),
            "profile": ("text", "NO"),
            "source_manifest_id": ("text", "NO"),
            "corpus_manifest_id": ("text", "NO"),
            "policy_hash": ("text", "NO"),
            "configuration_hash": ("text", "NO"),
            "prompt_id": ("text", "NO"),
            "prompt_hash": ("text", "NO"),
            "provider_id": ("text", "NO"),
            "model_id": ("text", "NO"),
            "query_hash": ("text", "NO"),
            "status": ("text", "NO"),
            "terminal_reason": ("text", "YES"),
            "created_at": ("timestamp with time zone", "NO"),
            "started_at": ("timestamp with time zone", "YES"),
            "completed_at": ("timestamp with time zone", "YES"),
            "lease_owner": ("text", "YES"),
            "lease_expires_at": ("timestamp with time zone", "YES"),
            "last_heartbeat_at": ("timestamp with time zone", "YES"),
        }
        forbidden_fragments = {
            "query_text",
            "question",
            "excerpt",
            "candidate_body",
            "provider_response",
            "credential",
            "secret",
            "local_path",
        }
        assert forbidden_fragments.isdisjoint(columns)


def test_migration_has_exact_status_and_event_kind_checks(migrated_dsn: str) -> None:
    import psycopg

    with psycopg.connect(migrated_dsn) as connection:
        status = _constraint_definition(
            connection, "specpilot_run", "specpilot_run_status_check"
        )
        event_kind = _constraint_definition(
            connection, "specpilot_run_event", "specpilot_run_event_kind_check"
        )
        assert set(re.findall(r"'([^']+)'", status)) == _STATUSES
        assert set(re.findall(r"'([^']+)'", event_kind)) == _EVENT_KINDS


def test_event_payload_check_rejects_plaintext_extra_and_mismatched_metadata(
    clean_ledger: str,
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        valid = {
            "kind": "tool_finished",
            "sequence": 1,
            "step_id": "fetch-clause",
            "tool": "get_clause",
            "argument_keys": ["document_id", "clauses"],
            "result_count": 1,
            "duration_ms": 2,
            "retry_count": 0,
            "error_code": None,
        }
        connection.execute(
            "INSERT INTO specpilot_run_event (run_id, sequence, kind, payload) "
            "VALUES (%s, 1, 'tool_finished', %s)",
            (run_id, json.dumps(valid)),
        )
        for sequence, kind, payload in (
            (2, "tool_finished", {**valid, "sequence": 2, "query": "hidden"}),
            (3, "tool_finished", {**valid, "sequence": 4}),
            (4, "terminal", {**valid, "sequence": 4}),
        ):
            with pytest.raises(CheckViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO specpilot_run_event "
                    "(run_id, sequence, kind, payload) VALUES (%s, %s, %s, %s)",
                    (run_id, sequence, kind, json.dumps(payload)),
                )


def test_run_state_timestamp_and_lease_constraints_fail_closed(
    clean_ledger: str,
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    with psycopg.connect(clean_ledger) as connection:
        for mutation in (
            "UPDATE specpilot_run SET status = 'running'",
            "UPDATE specpilot_run SET status = 'failed'",
            "UPDATE specpilot_run SET lease_expires_at = "
            "created_at - interval '1 second'",
        ):
            run_id = _insert_run(connection)
            with pytest.raises(CheckViolation), connection.transaction():
                connection.execute(mutation + " WHERE run_id = %s", (run_id,))


def test_answered_row_requires_completion_but_not_a_failure_reason(
    clean_ledger: str,
) -> None:
    import psycopg

    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        connection.execute(
            "UPDATE specpilot_run SET status = 'answered', completed_at = now(), "
            "lease_owner = NULL, lease_expires_at = NULL WHERE run_id = %s",
            (run_id,),
        )
        assert connection.execute(
            "SELECT status, terminal_reason FROM specpilot_run WHERE run_id = %s",
            (run_id,),
        ).fetchone() == ("answered", None)


def test_migration_is_upgrade_safe_after_001_through_004(ledger_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    from tests.conftest import MIGRATIONS_DIR

    schema = f"test_run_trace_{uuid.uuid4().hex}"
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            for migration in sorted(MIGRATIONS_DIR.glob("00[1-4]_*.sql")):
                connection.execute(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
                "VALUES (%s, 'egress-policy/v1')",
                ("a" * 64,),
            )
            connection.execute(
                (MIGRATIONS_DIR / "005_run_trace.sql").read_text(encoding="utf-8")
            )
            assert connection.execute(
                "SELECT schema_version FROM egress_policy_snapshot"
            ).fetchone() == ("egress-policy/v1",)
            assert connection.execute(
                "SELECT to_regclass('specpilot_run_event')"
            ).fetchone() == ("specpilot_run_event",)
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
