from __future__ import annotations

import uuid

import pytest

from specpilot.runs.contracts import RunStatus
from specpilot.runs.postgres import PostgresRunStore
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
    with pytest.raises(ValueError, match="checkpoint"):
        await store.write(None, first)

    read = await store.read(created.run_id)
    assert read is not None
    assert read.stage is CheckpointStage.PLANNED
    view = await runs.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert view.events[-1].kind.value == "checkpoint_summary"


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
