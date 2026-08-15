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
    "cache_summary",
    "usage_summary",
    "answer_outcome",
    "verifier_summary",
    "checkpoint_summary",
    "compliance_summary",
    "semantic_summary",
    "recovery_summary",
    "resume_summary",
    "terminal",
}


def _valid_event_payloads() -> dict[str, dict[str, object]]:
    return {
        "state_transition": {
            "kind": "state_transition",
            "sequence": 1,
            "previous_status": "queued",
            "status": "running",
            "reason": None,
        },
        "plan_summary": {
            "kind": "plan_summary",
            "sequence": 2,
            "plan_id": "plan-1",
            "step_count": 2,
            "max_tool_calls": 3,
        },
        "agent_step": {
            "kind": "agent_step",
            "sequence": 3,
            "agent": "evidence_agent",
            "step_id": "step-1",
            "phase": "finished",
            "duration_ms": 20,
            "error_code": None,
        },
        "tool_finished": {
            "kind": "tool_finished",
            "sequence": 4,
            "step_id": "step-1",
            "tool": "search_clauses",
            "argument_keys": ["query", "corpus_manifest_id"],
            "result_count": 2,
            "duration_ms": 20,
            "retry_count": 1,
            "error_code": None,
        },
        "candidate_summary": {
            "kind": "candidate_summary",
            "sequence": 5,
            "candidates": [{"candidate_id": "candidate-1", "score": 42.75}],
        },
        "evidence_summary": {
            "kind": "evidence_summary",
            "sequence": 6,
            "evidence": [{"evidence_id": "a" * 64, "content_hash": "b" * 64}],
        },
        "egress_summary": {
            "kind": "egress_summary",
            "sequence": 7,
            "stage": "evidence",
            "reservation_id": "00000000-0000-0000-0000-000000000007",
            "ledger_id": None,
            "admitted": True,
            "replayed": False,
            "request_tokens": 10,
            "request_bytes": 100,
            "cost_microunits": 3,
            "error_code": None,
        },
        "cache_summary": {
            "kind": "cache_summary",
            "sequence": 17,
            "hit": True,
            "stage": "evidence",
            "request_hash": "c" * 64,
            "record_hash": "d" * 64,
        },
        "usage_summary": {
            "kind": "usage_summary",
            "sequence": 8,
            "stage": "evidence",
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "request_bytes": 100,
            "duration_ms": 20,
            "cost_microunits": 3,
        },
        "answer_outcome": {
            "kind": "answer_outcome",
            "sequence": 9,
            "verdict": "refused",
            "refusal_reason": "evidence_insufficient",
            "provider_error": "provider_timeout",
            "reservation_id": "00000000-0000-0000-0000-000000000009",
            "replayed": False,
            "parse_fault_code": None,
        },
        "verifier_summary": {
            "kind": "verifier_summary",
            "sequence": 10,
            "checks": [{"evidence_id": "a" * 64, "passed": False, "fault_code": "x"}],
            "duration_ms": 20,
        },
        "checkpoint_summary": {
            "kind": "checkpoint_summary",
            "sequence": 11,
            "stage": "planned",
            "checkpoint_version": 1,
            "tool_attempts_used": 0,
            "recovery_attempted": False,
        },
        "compliance_summary": {
            "kind": "compliance_summary",
            "sequence": 12,
            "candidate_count": 1,
            "claim_ids": ["a" * 64],
        },
        "semantic_summary": {
            "kind": "semantic_summary",
            "sequence": 13,
            "claim_id": "a" * 64,
            "supports": True,
            "reason": "supported",
        },
        "recovery_summary": {
            "kind": "recovery_summary",
            "sequence": 14,
            "kind_name": "scoped_search",
            "reason": "not_disclosed",
            "remaining_tool_attempts": 6,
        },
        "resume_summary": {
            "kind": "resume_summary",
            "sequence": 15,
            "attempt": 2,
        },
        "terminal": {
            "kind": "terminal",
            "sequence": 16,
            "status": "failed",
            "reason": "provider_timeout",
        },
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
            "evaluation_root_id": ("text", "YES"),
            "profile": ("text", "NO"),
            "source_manifest_id": ("text", "NO"),
            "corpus_manifest_id": ("text", "NO"),
            "policy_hash": ("text", "NO"),
            "configuration_hash": ("text", "NO"),
            "prompt_id": ("text", "NO"),
            "prompt_hash": ("text", "NO"),
            "compliance_prompt_hash": ("text", "YES"),
            "verifier_prompt_hash": ("text", "YES"),
            "provider_id": ("text", "NO"),
            "model_id": ("text", "NO"),
            "query_hash": ("text", "NO"),
            "demo_scenario_id": ("text", "YES"),
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
        demo_scenario = _constraint_definition(
            connection,
            "specpilot_run",
            "specpilot_run_demo_scenario_id_check",
        )
        assert set(re.findall(r"'([^']+)'", status)) == _STATUSES
        assert set(re.findall(r"'([^']+)'", event_kind)) == _EVENT_KINDS
        assert set(re.findall(r"'([^']+)'", demo_scenario)) == {
            "l1_answered",
            "l2_answered",
            "evidence_refused",
            "verifier_recovered",
        }


def test_migration_demo_scenario_check_is_closed(clean_ledger: str) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        connection.execute(
            "UPDATE specpilot_run SET demo_scenario_id = 'verifier_recovered' "
            "WHERE run_id = %s",
            (run_id,),
        )
        with pytest.raises(CheckViolation):
            connection.execute(
                "UPDATE specpilot_run SET demo_scenario_id = 'unregistered' "
                "WHERE run_id = %s",
                (run_id,),
            )


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


def _insert_event_payload(
    connection: object,
    run_id: uuid.UUID,
    *,
    kind: str,
    sequence: int,
    payload: object,
) -> None:
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO specpilot_run_event (run_id, sequence, kind, payload) "
        "VALUES (%s, %s, %s, %s)",
        (run_id, sequence, kind, json.dumps(payload)),
    )


def test_raw_sql_accepts_every_closed_event_shape_and_raw_bm25_score(
    clean_ledger: str,
) -> None:
    import psycopg

    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        for kind, payload in _valid_event_payloads().items():
            _insert_event_payload(
                connection,
                run_id,
                kind=kind,
                sequence=int(payload["sequence"]),
                payload=payload,
            )
        assert connection.execute(
            "SELECT count(*) FROM specpilot_run_event WHERE run_id = %s", (run_id,)
        ).fetchone() == (17,)


@pytest.mark.parametrize("container", ["candidates", "evidence", "checks"])
@pytest.mark.parametrize(
    "forbidden_key",
    [
        "query",
        "excerpt",
        "candidate_body",
        "provider_response",
        "credential",
        "secret",
        "local_path",
    ],
)
def test_raw_sql_rejects_each_prohibited_key_inside_each_nested_container(
    clean_ledger: str,
    container: str,
    forbidden_key: str,
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    kind = {
        "candidates": "candidate_summary",
        "evidence": "evidence_summary",
        "checks": "verifier_summary",
    }[container]
    payload = _valid_event_payloads()[kind]
    nested = dict(payload[container][0])  # type: ignore[index,arg-type]
    nested[forbidden_key] = "must-not-persist"
    payload[container] = [nested]
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind=kind,
                sequence=int(payload["sequence"]),
                payload=payload,
            )


@pytest.mark.parametrize(
    ("kind", "field", "invalid"),
    [
        ("state_transition", "status", "unknown"),
        ("state_transition", "status", None),
        ("plan_summary", "step_count", 0),
        ("plan_summary", "step_count", "not-an-integer"),
        ("agent_step", "agent", "unknown"),
        ("agent_step", "agent", None),
        ("agent_step", "phase", None),
        ("tool_finished", "tool", "write_clause"),
        ("tool_finished", "tool", None),
        ("tool_finished", "error_code", "x" * 65),
        ("candidate_summary", "candidates", "not-an-array"),
        ("candidate_summary", "candidates", ["not-an-object"]),
        (
            "candidate_summary",
            "candidates",
            [{"candidate_id": "candidate-1", "score": 1_000_000_000_001}],
        ),
        ("evidence_summary", "evidence", {"not": "an-array"}),
        ("evidence_summary", "evidence", [7]),
        ("egress_summary", "stage", "unknown"),
        ("egress_summary", "stage", None),
        ("egress_summary", "admitted", "true"),
        ("usage_summary", "prompt_tokens", -1),
        ("usage_summary", "duration_ms", "not-an-integer"),
        ("answer_outcome", "verdict", "unknown"),
        ("answer_outcome", "verdict", None),
        ("answer_outcome", "replayed", 0),
        ("verifier_summary", "checks", False),
        ("verifier_summary", "checks", ["not-an-object"]),
        ("terminal", "status", "running"),
        ("terminal", "status", None),
    ],
)
def test_raw_sql_rejects_wrong_event_values_as_check_violations(
    clean_ledger: str,
    kind: str,
    field: str,
    invalid: object,
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()[kind]
    payload[field] = invalid
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind=kind,
                sequence=int(payload["sequence"]),
                payload=payload,
            )


def test_raw_sql_rejects_noninteger_payload_sequence_without_cast_error(
    clean_ledger: str,
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()["plan_summary"]
    payload["sequence"] = "not-an-integer"
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind="plan_summary",
                sequence=2,
                payload=payload,
            )


def test_raw_sql_accepts_uuid_text_that_pydantic_accepts(clean_ledger: str) -> None:
    import psycopg

    payload = _valid_event_payloads()["egress_summary"]
    payload["reservation_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        _insert_event_payload(
            connection,
            run_id,
            kind="egress_summary",
            sequence=7,
            payload=payload,
        )


def test_raw_sql_accepts_explicit_unknown_egress_cost(clean_ledger: str) -> None:
    import psycopg

    payload = _valid_event_payloads()["egress_summary"]
    payload["cost_microunits"] = None
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        _insert_event_payload(
            connection,
            run_id,
            kind="egress_summary",
            sequence=7,
            payload=payload,
        )


def test_raw_sql_accepts_blocked_egress_with_unavailable_attempt_metadata(
    clean_ledger: str,
) -> None:
    import psycopg

    payload = _valid_event_payloads()["egress_summary"]
    payload.update(
        admitted=False,
        reservation_id=None,
        replayed=False,
        request_tokens=None,
        request_bytes=None,
        cost_microunits=None,
        error_code="root_unique_excerpts_exceeded",
    )
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        _insert_event_payload(
            connection,
            run_id,
            kind="egress_summary",
            sequence=7,
            payload=payload,
        )


@pytest.mark.parametrize(
    "update",
    [
        {"reservation_id": "00000000-0000-0000-0000-000000000007"},
        {"ledger_id": "00000000-0000-0000-0000-000000000008"},
        {"replayed": True},
        {"request_tokens": 0},
        {"request_bytes": 0},
        {"cost_microunits": 0},
    ],
)
def test_raw_sql_rejects_blocked_egress_with_fabricated_attempt_metadata(
    clean_ledger: str, update: dict[str, object]
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()["egress_summary"]
    payload.update(
        admitted=False,
        reservation_id=None,
        replayed=False,
        request_tokens=None,
        request_bytes=None,
        cost_microunits=None,
        error_code="root_unique_excerpts_exceeded",
    )
    payload.update(update)
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind="egress_summary",
                sequence=7,
                payload=payload,
            )


@pytest.mark.parametrize("invalid", ["unknown", 1.5, True, -1, 1_000_000_001])
def test_raw_sql_rejects_wrong_egress_cost(clean_ledger: str, invalid: object) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()["egress_summary"]
    payload["cost_microunits"] = invalid
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind="egress_summary",
                sequence=7,
                payload=payload,
            )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("admitted", "true"),
        ("admitted", 1),
        ("replayed", "false"),
        ("replayed", 0),
        ("request_tokens", "10"),
        ("request_tokens", True),
        ("request_bytes", "100"),
        ("request_bytes", False),
    ],
)
def test_raw_sql_rejects_wrong_egress_primitives(
    clean_ledger: str, field: str, invalid: object
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()["egress_summary"]
    payload[field] = invalid
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind="egress_summary",
                sequence=7,
                payload=payload,
            )


def test_raw_sql_rejects_missing_egress_cost(clean_ledger: str) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()["egress_summary"]
    del payload["cost_microunits"]
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind="egress_summary",
                sequence=7,
                payload=payload,
            )


@pytest.mark.parametrize(
    ("kind", "field", "invalid"),
    [
        ("state_transition", "previous_status", 7),
        ("state_transition", "reason", 7),
        ("agent_step", "error_code", 7),
        ("tool_finished", "error_code", 7),
        ("egress_summary", "error_code", 7),
        ("answer_outcome", "refusal_reason", 7),
        ("answer_outcome", "provider_error", 7),
        ("answer_outcome", "parse_fault_code", 7),
        ("terminal", "reason", 7),
    ],
)
def test_raw_sql_optional_strings_reject_wrong_types_when_present(
    clean_ledger: str,
    kind: str,
    field: str,
    invalid: object,
) -> None:
    import psycopg
    from psycopg.errors import CheckViolation

    payload = _valid_event_payloads()[kind]
    payload[field] = invalid
    with psycopg.connect(clean_ledger) as connection:
        run_id = _insert_run(connection)
        with pytest.raises(CheckViolation):
            _insert_event_payload(
                connection,
                run_id,
                kind=kind,
                sequence=int(payload["sequence"]),
                payload=payload,
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


def _apply_migrations_through_013(connection: object) -> None:
    from tests.conftest import MIGRATIONS_DIR

    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if migration.name > "013_w4_recovery_reservation.sql":
            continue
        connection.execute(migration.read_text(encoding="utf-8"))  # type: ignore[attr-defined]


def _insert_legacy_l2_checkpoint(
    connection: object, *, stage: str
) -> tuple[uuid.UUID, dict[str, object]]:
    run_id = _insert_run(connection)
    hashes = {
        "query_hash": "f" * 64,
        "source_manifest_id": "c" * 64,
        "corpus_manifest_id": "b" * 64,
        "policy_hash": "a" * 64,
        "configuration_hash": "d" * 64,
        "compliance_prompt_hash": "1" * 64,
        "verifier_prompt_hash": "2" * 64,
    }
    connection.execute(  # type: ignore[attr-defined]
        "UPDATE specpilot_run SET task_level = 'L2', evaluation_root_id = 'root-1', "
        "compliance_prompt_hash = %s, verifier_prompt_hash = %s WHERE run_id = %s",
        (hashes["compliance_prompt_hash"], hashes["verifier_prompt_hash"], run_id),
    )
    payload: dict[str, object] = {
        "schema_version": "run-checkpoint/v1",
        "run_id": str(run_id),
        "attempt": 1,
        "checkpoint_version": 1,
        "stage": stage,
        "task_level": "L2",
        **hashes,
        "evaluation_root_id": "root-1",
        "provider_id": "provider-a",
        "model_id": "model-a",
        "plan_id": None,
        "plan_hash": None,
        "evidence": [],
        "tool_attempts_used": 0,
        "reservation_ids": [],
        "reconstruction_generations": [],
        "recovery_attempted": stage == "recovery_reserved",
        "recovery_reason": (
            "exception_missing" if stage == "recovery_reserved" else None
        ),
        "candidate_count": 0,
        "completed_claim_ids": [],
        "completed_results": [],
        "last_accessed_at": "2026-08-14T00:00:00+00:00",
    }
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO specpilot_run_checkpoint "
        "(run_id, checkpoint_version, stage, payload, last_accessed_at) "
        "VALUES (%s, 1, %s, %s::jsonb, %s::timestamptz)",
        (run_id, stage, json.dumps(payload), payload["last_accessed_at"]),
    )
    return run_id, payload


def test_migration_014_backfills_legacy_nonreserved_checkpoint(ledger_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    from tests.conftest import MIGRATIONS_DIR

    schema = f"test_w4_014_backfill_{uuid.uuid4().hex}"
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            _apply_migrations_through_013(connection)
            run_id, _ = _insert_legacy_l2_checkpoint(connection, stage="planned")

            connection.execute(
                (MIGRATIONS_DIR / "014_w4_recovery_claim_binding.sql").read_text(
                    encoding="utf-8"
                )
            )

            assert connection.execute(
                "SELECT payload -> 'recovery_claim_id', "
                "specpilot_valid_checkpoint(payload) "
                "FROM specpilot_run_checkpoint WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (None, True)
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_migration_014_refuses_legacy_reserved_checkpoint_without_mutation(
    ledger_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.errors import RaiseException

    from tests.conftest import MIGRATIONS_DIR

    schema = f"test_w4_014_reserved_{uuid.uuid4().hex}"
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            _apply_migrations_through_013(connection)
            run_id, _ = _insert_legacy_l2_checkpoint(
                connection, stage="recovery_reserved"
            )
            payload_before = connection.execute(
                "SELECT payload::text FROM specpilot_run_checkpoint WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            constraint_before = _constraint_definition(
                connection,
                "specpilot_run_checkpoint",
                "specpilot_run_checkpoint_payload_check",
            )

            with pytest.raises(RaiseException, match="W4_014_RECOVERY_RESERVED"):
                connection.execute(
                    (MIGRATIONS_DIR / "014_w4_recovery_claim_binding.sql").read_text(
                        encoding="utf-8"
                    )
                )

            assert connection.execute(
                "SELECT payload::text FROM specpilot_run_checkpoint WHERE run_id = %s",
                (run_id,),
            ).fetchone() == payload_before
            assert _constraint_definition(
                connection,
                "specpilot_run_checkpoint",
                "specpilot_run_checkpoint_payload_check",
            ) == constraint_before
            assert connection.execute(
                "SELECT specpilot_valid_checkpoint(payload) "
                "FROM specpilot_run_checkpoint WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (True,)
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_migration_015_keeps_preexisting_rows_without_demo_identity(
    ledger_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.errors import CheckViolation

    from tests.conftest import MIGRATIONS_DIR

    schema = f"test_w5_015_existing_rows_{uuid.uuid4().hex}"
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            _apply_migrations_through_013(connection)
            run_id, _ = _insert_legacy_l2_checkpoint(connection, stage="planned")
            connection.execute(
                (MIGRATIONS_DIR / "014_w4_recovery_claim_binding.sql").read_text(
                    encoding="utf-8"
                )
            )
            connection.execute(
                (MIGRATIONS_DIR / "015_w5_demo_scenario_identity.sql").read_text(
                    encoding="utf-8"
                )
            )
            assert connection.execute(
                "SELECT demo_scenario_id FROM specpilot_run WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (None,)
            connection.execute(
                "UPDATE specpilot_run "
                "SET demo_scenario_id = 'verifier_recovered' WHERE run_id = %s",
                (run_id,),
            )
            with pytest.raises(CheckViolation):
                connection.execute(
                    "UPDATE specpilot_run "
                    "SET demo_scenario_id = 'unregistered' WHERE run_id = %s",
                    (run_id,),
                )
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_migration_016_upgrades_015_and_admits_only_closed_cache_summary(
    ledger_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.errors import CheckViolation

    from tests.conftest import MIGRATIONS_DIR

    schema = f"test_w5_016_cache_{uuid.uuid4().hex}"
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if migration.name >= "016_w5_cache_trace.sql":
                    continue
                connection.execute(migration.read_text(encoding="utf-8"))
            run_id = _insert_run(connection)
            connection.execute(
                (MIGRATIONS_DIR / "016_w5_cache_trace.sql").read_text(
                    encoding="utf-8"
                )
            )
            payload = _valid_event_payloads()["cache_summary"]
            _insert_event_payload(
                connection,
                run_id,
                kind="cache_summary",
                sequence=17,
                payload=payload,
            )
            with pytest.raises(CheckViolation):
                _insert_event_payload(
                    connection,
                    run_id,
                    kind="cache_summary",
                    sequence=18,
                    payload={
                        **payload,
                        "sequence": 18,
                        "response": "source prose",
                    },
                )
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
