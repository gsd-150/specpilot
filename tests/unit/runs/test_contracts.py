from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError


def _contracts():  # type: ignore[no-untyped-def]
    return importlib.import_module("specpilot.runs.contracts")


def _tool_event(**updates: object) -> dict[str, object]:
    event: dict[str, object] = {
        "kind": "tool_finished",
        "sequence": 1,
        "step_id": "fetch-clause",
        "tool": "get_clause",
        "argument_keys": ("corpus_manifest_id", "document_id", "clauses"),
        "result_count": 1,
        "duration_ms": 2,
        "retry_count": 0,
        "error_code": None,
    }
    event.update(updates)
    return event


def test_run_status_is_the_exact_closed_state_machine_vocabulary() -> None:
    contracts = _contracts()

    assert {status.value for status in contracts.RunStatus} == {
        "queued",
        "running",
        "answered",
        "refused",
        "egress_blocked",
        "failed",
        "interrupted",
    }


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "query",
        "question",
        "excerpt",
        "candidate_body",
        "provider_response",
        "credential",
        "secret",
        "local_path",
        "Query",
        "providerResponse",
        "input_text",
    ],
)
def test_tool_event_rejects_each_plaintext_or_disguised_extra_field(
    forbidden_key: str,
) -> None:
    contracts = _contracts()

    with pytest.raises(ValidationError, match=forbidden_key):
        contracts.ToolFinishedEvent.model_validate(
            _tool_event(**{forbidden_key: "must-not-persist"})
        )


def test_tool_event_keeps_argument_names_but_rejects_argument_values() -> None:
    contracts = _contracts()

    event = contracts.ToolFinishedEvent.model_validate(
        _tool_event(argument_keys=("query", "corpus_manifest_id"))
    )
    assert event.argument_keys == ("query", "corpus_manifest_id")

    with pytest.raises(ValidationError, match="argument_values"):
        contracts.ToolFinishedEvent.model_validate(
            _tool_event(argument_values={"query": "plaintext"})
        )


def test_run_event_union_is_discriminated_and_rejects_unknown_kinds() -> None:
    contracts = _contracts()
    adapter = TypeAdapter(contracts.RunEvent)

    parsed = adapter.validate_python(_tool_event())
    assert isinstance(parsed, contracts.ToolFinishedEvent)
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_python({"kind": "debug_dump", "sequence": 2})


def test_run_event_kind_allowlist_is_exact() -> None:
    contracts = _contracts()

    assert {kind.value for kind in contracts.RunEventKind} == {
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


@pytest.mark.parametrize(
    ("model_name", "payload", "field"),
    [
        (
            "AgentStepEvent",
            {
                "kind": "agent_step",
                "sequence": 1,
                "agent": None,
                "step_id": "step-1",
                "phase": "started",
                "duration_ms": None,
                "error_code": None,
            },
            "agent",
        ),
        (
            "AgentStepEvent",
            {
                "kind": "agent_step",
                "sequence": 1,
                "agent": "orchestrator",
                "step_id": "step-1",
                "phase": None,
                "duration_ms": None,
                "error_code": None,
            },
            "phase",
        ),
        (
            "ToolFinishedEvent",
            _tool_event(tool=None),
            "tool",
        ),
    ],
)
def test_required_enum_null_rejection_matches_sql_boundary(
    model_name: str, payload: dict[str, object], field: str
) -> None:
    contracts = _contracts()

    with pytest.raises(ValidationError, match=field):
        getattr(contracts, model_name).model_validate(payload)


def test_trace_counts_durations_retries_and_sequences_are_bounded() -> None:
    contracts = _contracts()

    for field, invalid in (
        ("sequence", 0),
        ("result_count", -1),
        ("duration_ms", 3_600_001),
        ("retry_count", 2),
    ):
        with pytest.raises(ValidationError, match=field):
            contracts.ToolFinishedEvent.model_validate(_tool_event(**{field: invalid}))


def test_answer_outcome_keeps_provider_failure_distinct_from_verifier_refusal() -> None:
    contracts = _contracts()

    event = contracts.AnswerOutcomeEvent(
        kind="answer_outcome",
        sequence=8,
        verdict="refused",
        refusal_reason="evidence_insufficient",
        provider_error="provider_timeout",
        reservation_id=uuid.UUID("00000000-0000-0000-0000-000000000008"),
        replayed=False,
        parse_fault_code=None,
    )

    assert event.verdict.value == "refused"
    assert event.refusal_reason == "evidence_insufficient"
    assert event.provider_error == "provider_timeout"


def test_egress_cost_is_explicitly_unknown_instead_of_fabricated_zero() -> None:
    contracts = _contracts()

    event = contracts.EgressSummaryEvent(
        sequence=7,
        stage="planning",
        reservation_id=uuid.UUID("00000000-0000-0000-0000-000000000007"),
        ledger_id=None,
        admitted=True,
        request_tokens=10,
        request_bytes=100,
        cost_microunits=None,
        replayed=False,
        error_code=None,
    )

    assert event.cost_microunits is None

    payload = event.model_dump(mode="json")
    assert "cost_microunits" in payload
    del payload["cost_microunits"]
    with pytest.raises(ValidationError, match="cost_microunits"):
        contracts.EgressSummaryEvent.model_validate(payload)

    for invalid in ("unknown", 1.5, True, -1, 1_000_000_001):
        with pytest.raises(ValidationError, match="cost_microunits"):
            contracts.EgressSummaryEvent.model_validate(
                {**event.model_dump(mode="json"), "cost_microunits": invalid}
            )


def test_blocked_egress_requires_explicitly_unavailable_attempt_metadata() -> None:
    contracts = _contracts()

    blocked = contracts.EgressSummaryEvent(
        sequence=7,
        stage="planning",
        reservation_id=None,
        ledger_id=None,
        admitted=False,
        replayed=False,
        request_tokens=None,
        request_bytes=None,
        cost_microunits=None,
        error_code="root_unique_excerpts_exceeded",
    )
    assert blocked.request_tokens is None
    assert blocked.request_bytes is None

    base = blocked.model_dump(mode="json")
    for update in (
        {"reservation_id": "00000000-0000-0000-0000-000000000007"},
        {"replayed": True},
        {"request_tokens": 0},
        {"request_bytes": 0},
        {"cost_microunits": 0},
    ):
        with pytest.raises(ValidationError):
            contracts.EgressSummaryEvent.model_validate({**base, **update})

    del base["replayed"]
    with pytest.raises(ValidationError, match="replayed"):
        contracts.EgressSummaryEvent.model_validate(base)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("egress_blocked", "root_unique_excerpts_exceeded"),
        ("egress_blocked", "excerpt_bytes_exceeded"),
        ("egress_blocked", "policy_snapshot_mismatch"),
        ("failed", "provider_timeout"),
        ("refused", "evidence_insufficient"),
        ("interrupted", "lease_expired"),
    ],
)
def test_terminal_event_preserves_stable_status_reason_pairs(
    status: str, reason: str
) -> None:
    contracts = _contracts()

    event = contracts.TerminalEvent(
        kind="terminal", sequence=9, status=status, reason=reason
    )
    assert event.status.value == status
    assert event.reason == reason


def test_answered_terminal_event_has_no_failure_or_refusal_reason() -> None:
    contracts = _contracts()

    event = contracts.TerminalEvent(
        kind="terminal", sequence=9, status="answered", reason=None
    )
    assert event.reason is None


def test_run_models_are_frozen_and_hide_owner_query_and_lease_from_view() -> None:
    contracts = _contracts()
    created = datetime(2026, 8, 12, 1, 2, tzinfo=UTC)
    record = contracts.RunRecord(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        request_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        session_id="session-a",
        task_level="L1",
        profile="fixture",
        source_manifest_id="a" * 64,
        corpus_manifest_id="b" * 64,
        policy_hash="c" * 64,
        configuration_hash="d" * 64,
        prompt_id="l1-answer-v1",
        prompt_hash="e" * 64,
        provider_id="provider-a",
        model_id="model-a",
        query_hash="f" * 64,
        status="queued",
        terminal_reason=None,
        created_at=created,
        started_at=None,
        completed_at=None,
        lease_owner="queue-delivery",
        lease_expires_at=created + timedelta(seconds=30),
        last_heartbeat_at=None,
    )
    view = contracts.RunView(
        run_id=record.run_id,
        request_id=record.request_id,
        task_level=record.task_level,
        profile=record.profile,
        corpus_manifest_id=record.corpus_manifest_id,
        status=record.status,
        reason=record.terminal_reason,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        events=(),
    )

    with pytest.raises(ValidationError, match="frozen"):
        record.status = contracts.RunStatus.RUNNING
    dumped = view.model_dump(mode="json")
    assert set(dumped) == {
        "run_id",
        "request_id",
        "task_level",
        "profile",
        "corpus_manifest_id",
        "status",
        "reason",
        "created_at",
        "started_at",
        "completed_at",
        "events",
    }
    assert "session_id" not in dumped
    assert "query_hash" not in dumped
    assert "lease_owner" not in dumped


def test_run_record_rejects_incoherent_terminal_and_lease_timestamps() -> None:
    contracts = _contracts()
    created = datetime(2026, 8, 12, 1, 2, tzinfo=UTC)
    common = {
        "run_id": uuid.uuid4(),
        "request_id": uuid.uuid4(),
        "session_id": "session-a",
        "task_level": "L1",
        "profile": "fixture",
        "source_manifest_id": "a" * 64,
        "corpus_manifest_id": "b" * 64,
        "policy_hash": "c" * 64,
        "configuration_hash": "d" * 64,
        "prompt_id": "l1-answer-v1",
        "prompt_hash": "e" * 64,
        "provider_id": "provider-a",
        "model_id": "model-a",
        "query_hash": "f" * 64,
        "created_at": created,
        "started_at": None,
        "last_heartbeat_at": None,
    }

    with pytest.raises(ValidationError, match="terminal"):
        contracts.RunRecord(
            **common,
            status="failed",
            terminal_reason=None,
            completed_at=created,
            lease_owner=None,
            lease_expires_at=None,
        )
    with pytest.raises(ValidationError, match="lease"):
        contracts.RunRecord(
            **common,
            status="queued",
            terminal_reason=None,
            completed_at=None,
            lease_owner=None,
            lease_expires_at=None,
        )


def test_candidate_score_accepts_raw_bm25_values_above_one() -> None:
    contracts = _contracts()

    summary = contracts.CandidateScoreSummary(candidate_id="candidate-a", score=42.75)
    assert summary.score == 42.75


@pytest.mark.parametrize("score", [1_000_000_000_001.0, float("inf"), float("nan")])
def test_candidate_score_rejects_nonfinite_or_implausible_raw_values(
    score: float,
) -> None:
    contracts = _contracts()

    with pytest.raises(ValidationError, match="score"):
        contracts.CandidateScoreSummary(candidate_id="candidate-a", score=score)


def _run_view(
    contracts: object,
    *,
    status: str,
    reason: str | None,
    started_at: datetime | None,
    completed_at: datetime | None,
    events: tuple[object, ...] = (),
):  # type: ignore[no-untyped-def]
    return contracts.RunView(  # type: ignore[attr-defined,no-any-return]
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        request_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        task_level="L1",
        profile="fixture",
        corpus_manifest_id="b" * 64,
        status=status,
        reason=reason,
        created_at=datetime(2026, 8, 12, 1, 0, tzinfo=UTC),
        started_at=started_at,
        completed_at=completed_at,
        events=events,
    )


@pytest.mark.parametrize("status", ["answered", "failed"])
def test_run_view_rejects_completed_status_without_completion_time(status: str) -> None:
    contracts = _contracts()
    reason = None if status == "answered" else "provider_timeout"

    with pytest.raises(ValidationError, match="completion"):
        _run_view(
            contracts,
            status=status,
            reason=reason,
            started_at=datetime(2026, 8, 12, 1, 1, tzinfo=UTC),
            completed_at=None,
        )


def test_run_view_rejects_queued_status_with_completion_time() -> None:
    contracts = _contracts()

    with pytest.raises(ValidationError, match="completed"):
        _run_view(
            contracts,
            status="queued",
            reason=None,
            started_at=None,
            completed_at=datetime(2026, 8, 12, 1, 2, tzinfo=UTC),
        )


def test_run_view_allows_read_derived_interrupted_without_completion_time() -> None:
    contracts = _contracts()

    view = _run_view(
        contracts,
        status="interrupted",
        reason="lease_expired",
        started_at=None,
        completed_at=None,
    )
    assert view.status is contracts.RunStatus.INTERRUPTED
    assert view.completed_at is None


@pytest.mark.parametrize("sequences", [(2, 1), (1, 1)])
def test_run_view_rejects_out_of_order_or_duplicate_event_sequences(
    sequences: tuple[int, int],
) -> None:
    contracts = _contracts()
    events = tuple(
        contracts.PlanSummaryEvent(
            kind="plan_summary",
            sequence=sequence,
            plan_id=f"plan-{index}",
            step_count=1,
            max_tool_calls=1,
        )
        for index, sequence in enumerate(sequences)
    )

    with pytest.raises(ValidationError, match="strictly increasing"):
        _run_view(
            contracts,
            status="running",
            reason=None,
            started_at=datetime(2026, 8, 12, 1, 1, tzinfo=UTC),
            completed_at=None,
            events=events,
        )
