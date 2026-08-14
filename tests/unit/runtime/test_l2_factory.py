"""The production L2 job adapter owns checkpoint/CAS and acquired lease joins."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from specpilot.checkpoints.contracts import RunCheckpoint
from specpilot.runtime.l2_factory import L2JobFactory, RuntimeJobBuilder
from tests.unit.checkpoints.test_contracts import _checkpoint
from tests.unit.runtime.test_l2 import Semantic, context, passed
from tests.unit.runtime.test_l2 import evidence as fixture_evidence


@dataclass
class Store:
    checkpoint: RunCheckpoint | None = None
    writes: list[tuple[int | None, RunCheckpoint]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.writes = []

    def new_checkpoint(self, run, *, stage):  # type: ignore[no-untyped-def]
        return _checkpoint(run_id=run.run_id, stage=stage, plan_id=None, plan_hash=None)

    async def read(self, run_id):  # type: ignore[no-untyped-def]
        return self.checkpoint

    async def write(self, previous_version, checkpoint):  # type: ignore[no-untyped-def]
        self.writes.append((previous_version, checkpoint))
        self.checkpoint = checkpoint
        return checkpoint


def builder(run, question, checkpoint, first):  # type: ignore[no-untyped-def]
    item = fixture_evidence()
    return context(deterministic=lambda *_: passed(item), semantic=Semantic([True]))


def fixture_run():  # type: ignore[no-untyped-def]
    from specpilot.runs.contracts import RunRecord

    now = datetime(2026, 8, 14, tzinfo=UTC)
    return RunRecord(
        run_id=uuid4(),
        request_id=uuid4(),
        session_id="owner",
        task_level="L2",
        evaluation_root_id="root-1",
        profile="fixture",
        source_manifest_id="a" * 64,
        corpus_manifest_id="b" * 64,
        policy_hash="c" * 64,
        configuration_hash="d" * 64,
        prompt_id="l2",
        prompt_hash="e" * 64,
        compliance_prompt_hash="1" * 64,
        verifier_prompt_hash="2" * 64,
        provider_id="provider",
        model_id="model",
        query_hash="f" * 64,
        status="queued",
        terminal_reason=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        lease_owner="delivery",
        lease_expires_at=now + timedelta(seconds=30),
        last_heartbeat_at=None,
    )


@pytest.mark.anyio
async def test_new_l2_job_injects_real_none_cas_writer() -> None:
    run = fixture_run()
    store = Store()
    job = await L2JobFactory(store, builder).new_job(run, "private")

    assert job.task_level == "L2"
    assert job.lease_acquired is False
    assert job.l2_context is not None
    assert job.l2_context.checkpoint_writer is not None


@pytest.mark.anyio
async def test_resumed_l2_job_requires_current_attempt_checkpoint_and_skips_claim() -> (
    None
):
    run = fixture_run()
    saved = _checkpoint(
        run_id=run.run_id,
        attempt=2,
        stage="planned",
        plan_id="plan-1",
        plan_hash="1" * 64,
        query_hash=run.query_hash,
        evaluation_root_id=run.evaluation_root_id,
        corpus_manifest_id=run.corpus_manifest_id,
        policy_hash=run.policy_hash,
        configuration_hash=run.configuration_hash,
        provider_id=run.provider_id,
        model_id=run.model_id,
    )
    store = Store(checkpoint=saved)

    job = await L2JobFactory(store, builder).resumed_job(run, "private", attempt=2)

    assert job.lease_acquired is True
    assert job.attempt == 2
    assert job.l2_context is not None
    assert job.l2_context.checkpoint is saved


@pytest.mark.anyio
async def test_runtime_delivery_builder_calls_l2_factory_for_new_and_resume() -> None:
    run = fixture_run()
    store = Store()
    factory = L2JobFactory(store, builder)
    delivery = RuntimeJobBuilder(
        lambda *_: (_ for _ in ()).throw(AssertionError()), factory
    )

    fresh = await delivery.build(run, "private")
    assert fresh.task_level == "L2"
    assert fresh.lease_acquired is False

    saved = _checkpoint(
        run_id=run.run_id, attempt=2, stage="planned", plan_id="plan-1",
        plan_hash="1" * 64, query_hash=run.query_hash,
        evaluation_root_id=run.evaluation_root_id,
        corpus_manifest_id=run.corpus_manifest_id,
        policy_hash=run.policy_hash, configuration_hash=run.configuration_hash,
        provider_id=run.provider_id, model_id=run.model_id,
    )
    store.checkpoint = saved
    resumed = await delivery.build(run, "private", acquired_attempt=2)

    assert resumed.lease_acquired is True
    assert resumed.attempt == 2
