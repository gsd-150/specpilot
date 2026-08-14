from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from specpilot.runs.contracts import RunStatus
from specpilot.runs.postgres import PostgresRunStore, RunStoreValidationError
from tests.integration.runs.test_postgres_store import Clock, _new_run, _seed_bindings

pytestmark = pytest.mark.integration


def _l2_run(clock: Clock):  # type: ignore[no-untyped-def]
    return _new_run(clock).model_copy(
        update={
            "task_level": "L2",
            "evaluation_root_id": "l2-root-1",
            "prompt_id": "compliance-v1",
            "compliance_prompt_hash": "1" * 64,
            "verifier_prompt_hash": "2" * 64,
        }
    )


@pytest.mark.anyio
async def test_checkpoint_write_is_compare_and_set_and_appends_summary_atomically(
    clean_ledger: str,
) -> None:
    from specpilot.checkpoints.contracts import CheckpointStage
    from specpilot.checkpoints.postgres import PostgresCheckpointStore

    await _seed_bindings(clean_ledger)
    clock = Clock()
    run = _l2_run(clock)
    runs = PostgresRunStore(clean_ledger, clock=clock)
    created = await runs.create(run)
    store = PostgresCheckpointStore(clean_ledger, clock=clock)
    first = store.new_checkpoint(created, stage=CheckpointStage.PLANNED)

    written = await store.write(None, first)
    assert written.checkpoint_version == 1
    with pytest.raises(RunStoreValidationError, match="invalid_run_data"):
        await store.write(None, first)

    illegal_initial = first.model_copy(
        update={"stage": CheckpointStage.EVIDENCE_COLLECTED}
    )
    with pytest.raises(RunStoreValidationError, match="invalid_run_data"):
        await store.write(None, illegal_initial)

    read = await store.read(created.run_id)
    assert read is not None
    assert read.stage is CheckpointStage.PLANNED
    view = await runs.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert view.events[-1].kind.value == "checkpoint_summary"


@pytest.mark.anyio
async def test_resume_refuses_non_lease_expired_interruption_without_mutation(
    clean_ledger: str,
) -> None:
    from specpilot.checkpoints.contracts import CheckpointStage
    from specpilot.checkpoints.postgres import PostgresCheckpointStore
    from specpilot.runs.contracts import ResumeDisposition, TerminalEvent

    await _seed_bindings(clean_ledger)
    clock = Clock()
    runs = PostgresRunStore(clean_ledger, clock=clock)
    created = await runs.create(_l2_run(clock))
    assert await runs.fail_delivery(
        created.run_id,
        TerminalEvent(
            sequence=1,
            status=RunStatus.INTERRUPTED,
            reason="queue_delivery_failed",
        ),
    )
    store = PostgresCheckpointStore(clean_ledger, clock=clock)
    await store.write(
        None, store.new_checkpoint(created, stage=CheckpointStage.PLANNED)
    )
    result = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "new-key",
        lease_owner="resume-worker",
        lease_seconds=30,
    )
    assert result.disposition is ResumeDisposition.NOT_INTERRUPTED


@pytest.mark.anyio
async def test_resume_claim_is_owner_bound_idempotent_and_leased(
    clean_ledger: str,
) -> None:
    from specpilot.checkpoints.contracts import CheckpointStage
    from specpilot.checkpoints.postgres import PostgresCheckpointStore
    from specpilot.runs.contracts import ResumeDisposition

    await _seed_bindings(clean_ledger)
    clock = Clock()
    runs = PostgresRunStore(clean_ledger, clock=clock)
    created = await runs.create(_l2_run(clock))
    clock.advance(seconds=31)
    assert await runs.reconcile_expired(clock()) == 1
    store = PostgresCheckpointStore(clean_ledger, clock=clock)
    checkpoint = store.new_checkpoint(created, stage=CheckpointStage.PLANNED)
    await store.write(None, checkpoint)

    acquired = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "resume-key-a",
        lease_owner="resume-worker-a",
        lease_seconds=30,
    )
    assert acquired.disposition is ResumeDisposition.ACQUIRED
    assert acquired.attempt == 2
    replay = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "resume-key-a",
        lease_owner="resume-worker-b",
        lease_seconds=30,
    )
    assert replay.disposition is ResumeDisposition.REPLAY
    assert replay.attempt == 2
    leased = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "resume-key-b",
        lease_owner="resume-worker-b",
        lease_seconds=30,
    )
    assert leased.disposition is ResumeDisposition.LEASED


@pytest.mark.anyio
async def test_resume_denials_do_not_mutate_interrupted_run(clean_ledger: str) -> None:
    from specpilot.checkpoints.contracts import CheckpointStage
    from specpilot.checkpoints.postgres import PostgresCheckpointStore
    from specpilot.runs.contracts import ResumeDisposition

    await _seed_bindings(clean_ledger)
    clock = Clock()
    runs = PostgresRunStore(clean_ledger, clock=clock)
    created = await runs.create(_l2_run(clock))
    clock.advance(seconds=31)
    assert await runs.reconcile_expired(clock()) == 1
    store = PostgresCheckpointStore(clean_ledger, clock=clock)
    checkpoint = store.new_checkpoint(created, stage=CheckpointStage.PLANNED)
    await store.write(None, checkpoint)

    denied = await store.begin_resume(
        created.run_id,
        "other-owner",
        created.query_hash,
        f"resume-{uuid.uuid4().hex}",
        lease_owner="resume-worker",
        lease_seconds=30,
    )
    assert denied.disposition is ResumeDisposition.NOT_OWNER
    view = await runs.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert view.status is RunStatus.INTERRUPTED


@pytest.mark.anyio
async def test_failed_resume_delivery_closes_only_the_acquired_attempt(
    clean_ledger: str,
) -> None:
    import psycopg

    from specpilot.checkpoints.contracts import CheckpointStage
    from specpilot.checkpoints.postgres import PostgresCheckpointStore
    from specpilot.runs.contracts import ResumeDisposition

    await _seed_bindings(clean_ledger)
    clock = Clock()
    runs = PostgresRunStore(clean_ledger, clock=clock)
    created = await runs.create(_l2_run(clock))
    store = PostgresCheckpointStore(clean_ledger, clock=clock)
    await store.write(
        None, store.new_checkpoint(created, stage=CheckpointStage.PLANNED)
    )
    clock.advance(seconds=31)
    assert await runs.reconcile_expired(clock()) == 1
    acquired = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "resume-delivery-key",
        lease_owner="resume-worker",
        lease_seconds=30,
    )
    assert acquired.disposition is ResumeDisposition.ACQUIRED
    assert acquired.attempt == 2

    assert await store.fail_resume_delivery(
        created.run_id, 2, lease_owner="resume-worker"
    )
    assert not await store.fail_resume_delivery(
        created.run_id, 2, lease_owner="resume-worker"
    )
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        attempts = await (
            await connection.execute(
                "SELECT attempt, ended_at, end_reason FROM specpilot_run_attempt "
                "WHERE run_id = %s ORDER BY attempt",
                (created.run_id,),
            )
        ).fetchall()
    assert [(row[0], row[2]) for row in attempts] == [
        (1, "lease_expired"),
        (2, "queue_delivery_failed"),
    ]
    assert all(row[1] is not None for row in attempts)

    replay = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "resume-delivery-key",
        lease_owner="other-worker",
        lease_seconds=30,
    )
    assert replay.disposition is ResumeDisposition.REPLAY
    assert replay.attempt == 2


@pytest.mark.anyio
async def test_failed_resume_delivery_does_not_touch_an_expired_lease(
    clean_ledger: str,
) -> None:
    import psycopg

    from specpilot.checkpoints.contracts import CheckpointStage
    from specpilot.checkpoints.postgres import PostgresCheckpointStore
    from specpilot.runs.contracts import ResumeDisposition

    await _seed_bindings(clean_ledger)
    clock = Clock()
    runs = PostgresRunStore(clean_ledger, clock=clock)
    created = await runs.create(_l2_run(clock))
    clock.advance(seconds=31)
    assert await runs.reconcile_expired(clock()) == 1
    store = PostgresCheckpointStore(clean_ledger, clock=clock)
    await store.write(
        None, store.new_checkpoint(created, stage=CheckpointStage.PLANNED)
    )
    acquired = await store.begin_resume(
        created.run_id,
        created.session_id,
        created.query_hash,
        "expiring-delivery-key",
        lease_owner="resume-worker",
        lease_seconds=30,
    )
    assert acquired.disposition is ResumeDisposition.ACQUIRED
    clock.advance(seconds=31)

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        events_before = await (
            await connection.execute(
                "SELECT count(*) FROM specpilot_run_event WHERE run_id = %s",
                (created.run_id,),
            )
        ).fetchone()
    assert not await store.fail_resume_delivery(
        created.run_id, 2, lease_owner="resume-worker"
    )
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        attempt = await (
            await connection.execute(
                "SELECT ended_at, end_reason FROM specpilot_run_attempt "
                "WHERE run_id = %s AND attempt = 2",
                (created.run_id,),
            )
        ).fetchone()
        run = await (
            await connection.execute(
                "SELECT status, lease_owner FROM specpilot_run WHERE run_id = %s",
                (created.run_id,),
            )
        ).fetchone()
        events_after = await (
            await connection.execute(
                "SELECT count(*) FROM specpilot_run_event WHERE run_id = %s",
                (created.run_id,),
            )
        ).fetchone()
    assert attempt == (None, None)
    assert run == (RunStatus.RUNNING.value, "resume-worker")
    assert events_after == events_before


@pytest.mark.anyio
async def test_completed_checkpoint_compacts_and_ttl_deletes_only_inactive_eligible(
    clean_ledger: str,
) -> None:
    import psycopg

    from specpilot.checkpoints.contracts import (
        CheckpointStage,
        EvidenceCheckpointRef,
        StageGeneration,
    )
    from specpilot.checkpoints.postgres import PostgresCheckpointStore
    from specpilot.contracts.verdict import (
        ComplianceResult,
        ComplianceVerdict,
        VerificationStatus,
    )
    from specpilot.runs.contracts import TerminalEvent

    await _seed_bindings(clean_ledger)
    clock = Clock()
    runs = PostgresRunStore(clean_ledger, clock=clock)
    store = PostgresCheckpointStore(clean_ledger, clock=clock)

    completed_run = await runs.create(_l2_run(clock))
    checkpoint = await store.write(
        None, store.new_checkpoint(completed_run, stage=CheckpointStage.PLANNED)
    )
    evidence = EvidenceCheckpointRef(
        evidence_id="3" * 64,
        content_hash="3" * 64,
        quote_hash="4" * 64,
        clause_id="5" * 64,
        document_id="ietf-rfc-9110",
        document_version="2022-06",
        section_number="1.1",
        paragraph_start=1,
        paragraph_end=1,
        token_start=0,
        token_end=4,
    )
    result = ComplianceResult(
        claim_id="6" * 64,
        verdict=ComplianceVerdict.INSUFFICIENT_EVIDENCE,
        verification_status=VerificationStatus.INSUFFICIENT,
        reason_code="fixture_insufficient",
    )
    for stage, updates in (
        (
            CheckpointStage.EVIDENCE_COLLECTED,
            {
                "plan_id": "fixture-plan",
                "plan_hash": "7" * 64,
                "evidence": (evidence,),
                "tool_attempts_used": 2,
                "reconstruction_generations": (
                    StageGeneration(
                        stage="compliance",
                        claim_id=None,
                        recovery=False,
                        generation=0,
                    ),
                ),
            },
        ),
        (CheckpointStage.CANDIDATE_BUILT, {"candidate_count": 1}),
        (CheckpointStage.DETERMINISTIC_VERIFIED, {}),
        (
            CheckpointStage.SEMANTIC_VERIFIED,
            {
                "completed_claim_ids": (result.claim_id,),
                "completed_results": (result,),
            },
        ),
        (CheckpointStage.COMPLETED, {}),
    ):
        candidate = checkpoint.model_copy(
            update={
                "checkpoint_version": checkpoint.checkpoint_version + 1,
                "stage": stage,
                **updates,
            }
        )
        checkpoint = await store.write(checkpoint.checkpoint_version, candidate)

    assert await store.compact(completed_run.run_id)
    compacted = await store.read(completed_run.run_id)
    assert compacted is not None
    assert compacted.stage is CheckpointStage.COMPLETED
    assert compacted.completed_results == (result,)
    assert compacted.completed_claim_ids == (result.claim_id,)
    assert compacted.candidate_count == 1
    assert compacted.plan_id is None
    assert compacted.evidence == ()
    assert compacted.tool_attempts_used == 0
    assert compacted.reservation_ids == ()
    assert compacted.reconstruction_generations == ()
    assert "PRIVATE-CHECKPOINT-SENTINEL" not in compacted.model_dump_json()

    running_run = await runs.create(_l2_run(clock))
    assert await runs.claim(running_run.run_id, "running-worker", lease_seconds=3600)
    await store.write(
        None, store.new_checkpoint(running_run, stage=CheckpointStage.PLANNED)
    )
    eligible_run = await runs.create(_l2_run(clock))
    await store.write(
        None, store.new_checkpoint(eligible_run, stage=CheckpointStage.PLANNED)
    )
    assert await runs.fail_delivery(
        eligible_run.run_id,
        TerminalEvent(
            sequence=1,
            status=RunStatus.INTERRUPTED,
            reason="queue_delivery_failed",
        ),
    )

    cutoff = clock() - timedelta(days=7)
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "UPDATE specpilot_run_checkpoint SET last_accessed_at = %s, "
            "payload = jsonb_set(payload, '{last_accessed_at}', to_jsonb(%s::text)) "
            "WHERE run_id IN (%s, %s, %s)",
            (
                cutoff - timedelta(seconds=1),
                (cutoff - timedelta(seconds=1)).isoformat(),
                completed_run.run_id,
                running_run.run_id,
                eligible_run.run_id,
            ),
        )
        await connection.commit()

    assert await store.delete_expired(cutoff) == 1
    assert await store.read(eligible_run.run_id) is None
    assert await store.read(running_run.run_id) is not None
    assert await store.read(completed_run.run_id) is not None
