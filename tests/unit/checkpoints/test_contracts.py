from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError


def _checkpoint(**changes: object):  # type: ignore[no-untyped-def]
    from specpilot.checkpoints.contracts import RunCheckpoint

    values: dict[str, object] = {
        "run_id": uuid.uuid4(),
        "attempt": 1,
        "checkpoint_version": 1,
        "stage": "planned",
        "task_level": "L2",
        "query_hash": "a" * 64,
        "evaluation_root_id": "root-1",
        "source_manifest_id": "b" * 64,
        "corpus_manifest_id": "c" * 64,
        "policy_hash": "d" * 64,
        "configuration_hash": "e" * 64,
        "compliance_prompt_hash": "f" * 64,
        "verifier_prompt_hash": "0" * 64,
        "provider_id": "provider-1",
        "model_id": "model-1",
        "plan_id": "plan-1",
        "plan_hash": "1" * 64,
        "evidence": (),
        "tool_attempts_used": 0,
        "reservation_ids": (),
        "reconstruction_generations": (),
        "recovery_attempted": False,
        "recovery_reason": None,
        "candidate_count": 0,
        "completed_claim_ids": (),
        "completed_results": (),
        "last_accessed_at": datetime(2026, 8, 14, tzinfo=UTC),
    }
    values.update(changes)
    return RunCheckpoint.model_validate(values)


def test_checkpoint_is_a_prose_free_closed_envelope() -> None:
    checkpoint = _checkpoint()
    encoded = checkpoint.model_dump(mode="json")
    rendered = repr(encoded)

    assert "PRIVATE-QUESTION-SENTINEL" not in rendered
    assert not {
        "question", "claim", "rationale", "query", "excerpt",
        "provider_response", "path", "exception",
    }.intersection(encoded)
    with pytest.raises(ValidationError, match="question"):
        _checkpoint(question="PRIVATE-QUESTION-SENTINEL")


def test_transition_rejects_binding_rollback_and_attempt_drift() -> None:
    from specpilot.checkpoints.contracts import validate_transition

    previous = _checkpoint(
        stage="candidate_built", tool_attempts_used=3, recovery_attempted=False
    )
    rollback = _checkpoint(
        run_id=previous.run_id,
        stage="deterministic_verified",
        checkpoint_version=2,
        tool_attempts_used=2,
    )
    with pytest.raises(ValueError, match="tool"):
        validate_transition(previous, rollback)
    changed_binding = rollback.model_copy(update={"policy_hash": "9" * 64})
    with pytest.raises(ValueError, match="binding"):
        validate_transition(previous, changed_binding)


@pytest.mark.parametrize(
    ("stage", "next_stage"),
    [
        ("planned", "evidence_collected"),
        ("evidence_collected", "candidate_built"),
        ("candidate_built", "deterministic_verified"),
        ("candidate_built", "recovery_completed"),
        ("deterministic_verified", "semantic_verified"),
        ("deterministic_verified", "recovery_completed"),
        ("recovery_completed", "deterministic_verified"),
        ("semantic_verified", "completed"),
    ],
)
def test_legal_checkpoint_transitions(stage: str, next_stage: str) -> None:
    from specpilot.checkpoints.contracts import validate_transition

    previous = _checkpoint(
        stage=stage, recovery_attempted=stage == "recovery_completed"
    )
    current = _checkpoint(
        run_id=previous.run_id,
        stage=next_stage,
        checkpoint_version=2,
        recovery_attempted=(
            stage == "recovery_completed" or next_stage == "recovery_completed"
        ),
    )
    assert validate_transition(previous, current) is None


def test_recovery_completed_requires_monotonic_single_recovery() -> None:
    with pytest.raises(ValidationError, match="recovery"):
        _checkpoint(stage="recovery_completed")

    from specpilot.checkpoints.contracts import validate_transition

    previous = _checkpoint(stage="deterministic_verified", recovery_attempted=True)
    current = _checkpoint(
        run_id=previous.run_id,
        stage="recovery_completed", checkpoint_version=2, recovery_attempted=True
    )
    # A same-run recovery checkpoint may be compacted or receive a generation
    # reservation update after a process loss; the run-scoped boolean itself
    # remains monotonic and cannot create a second recovery action.
    assert validate_transition(previous, current) is None


def test_attempt_count_and_completed_ids_are_bounded_and_opaque() -> None:
    with pytest.raises(ValidationError, match="tool_attempts_used"):
        _checkpoint(tool_attempts_used=9)
    with pytest.raises(ValidationError, match="completed_claim_ids"):
        _checkpoint(completed_claim_ids=("not-an-opaque-hash",))
    with pytest.raises(ValidationError, match="candidate_count"):
        _checkpoint(candidate_count=4)


def test_l2_run_requires_real_root_and_distinct_stage_prompt_hashes() -> None:
    from specpilot.runs.contracts import RunRecord, RunStatus

    data = {
        "run_id": uuid.uuid4(), "request_id": uuid.uuid4(), "session_id": "owner-1",
        "task_level": "L2", "profile": "fixture", "source_manifest_id": "a" * 64,
        "corpus_manifest_id": "b" * 64, "policy_hash": "c" * 64,
        "configuration_hash": "d" * 64, "prompt_id": "l2-v1", "prompt_hash": "e" * 64,
        "provider_id": "provider-1", "model_id": "model-1", "query_hash": "f" * 64,
        "status": RunStatus.QUEUED, "terminal_reason": None,
        "created_at": datetime(2026, 8, 14, tzinfo=UTC), "started_at": None,
        "completed_at": None, "lease_owner": "queue-delivery",
        "lease_expires_at": datetime(2026, 8, 14, 0, 1, tzinfo=UTC),
        "last_heartbeat_at": None,
    }
    with pytest.raises(ValidationError, match="evaluation_root"):
        RunRecord.model_validate(data)

    data.update(
        evaluation_root_id="root-1",
        compliance_prompt_hash="1" * 64,
        verifier_prompt_hash="1" * 64,
    )
    with pytest.raises(ValidationError, match="distinct"):
        RunRecord.model_validate(data)


def test_checkpoint_requires_distinct_l2_stage_prompt_hashes() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        _checkpoint(compliance_prompt_hash="1" * 64, verifier_prompt_hash="1" * 64)


def test_generation_identity_includes_generation_number() -> None:
    generation = {
        "stage": "compliance", "claim_id": "2" * 64,
        "recovery": False, "generation": 0,
    }
    checkpoint = _checkpoint(
        reconstruction_generations=(
            generation,
            {**generation, "generation": 1},
        )
    )
    assert len(checkpoint.reconstruction_generations) == 2
