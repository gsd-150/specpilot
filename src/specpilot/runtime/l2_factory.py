"""Production-facing construction of L2 jobs without an HTTP dependency.

Task 7 will call this adapter from create/resume routes.  Keeping it in the
runtime now ensures a worker always receives a fully bound L2 context instead
of a hand-built test-only object, and that an acquired resume lease is never
claimed a second time by the worker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from specpilot.agents.planner import PlannerContext
from specpilot.checkpoints.contracts import CheckpointStage, RunCheckpoint
from specpilot.contracts.egress import TaskLevel
from specpilot.runs.contracts import RunRecord
from specpilot.runtime.l2 import L2RunContext
from specpilot.runtime.worker import RunJob


class L2CheckpointStore:
    """The exact runtime subset of the PostgreSQL checkpoint store."""

    def new_checkpoint(
        self, run: RunRecord, *, stage: CheckpointStage
    ) -> RunCheckpoint:
        raise NotImplementedError

    async def read(self, run_id: object) -> RunCheckpoint | None:
        raise NotImplementedError

    async def write(
        self, previous_version: int | None, checkpoint: RunCheckpoint
    ) -> RunCheckpoint:
        raise NotImplementedError


type ContextBuilder = Callable[
    [RunRecord, str, RunCheckpoint | None, Callable[[], RunCheckpoint]], L2RunContext
]
type L1JobBuilder = Callable[[RunRecord, str], RunJob]


@dataclass(frozen=True, slots=True)
class L2JobFactory:
    """Translate durable run/checkpoint state into one ephemeral worker job."""

    checkpoint_store: L2CheckpointStore
    context_builder: ContextBuilder

    async def new_job(self, run: RunRecord, question: str) -> RunJob:
        """Build a queued L2 job. The first checkpoint is persisted by L2."""
        _require_l2(run)

        def first() -> RunCheckpoint:
            return self.checkpoint_store.new_checkpoint(
                run, stage=CheckpointStage.PLANNED
            )

        context = self.context_builder(run, question, None, first)
        context = replace(
            context,
            checkpoint=None,
            checkpoint_factory=first,
            checkpoint_writer=self.checkpoint_store.write,
        )
        return _job(run, question, context, lease_acquired=False, attempt=1)

    async def job_for_delivery(
        self, run: RunRecord, question: str, *, acquired_attempt: int | None = None
    ) -> RunJob:
        """The runtime service entry point used before any HTTP route exists."""
        if acquired_attempt is None:
            return await self.new_job(run, question)
        return await self.resumed_job(run, question, attempt=acquired_attempt)

    async def resumed_job(
        self, run: RunRecord, question: str, *, attempt: int
    ) -> RunJob:
        """Build a job after ``begin_resume`` acquired this exact run lease."""
        _require_l2(run)
        if attempt < 2:
            raise ValueError("resume_attempt_invalid")
        checkpoint = await self.checkpoint_store.read(run.run_id)
        if checkpoint is None or checkpoint.attempt != attempt:
            raise ValueError("resume_checkpoint_unavailable")
        _require_bindings(run, checkpoint)

        def first() -> RunCheckpoint:
            return self.checkpoint_store.new_checkpoint(
                run, stage=CheckpointStage.PLANNED
            )

        context = self.context_builder(run, question, checkpoint, first)
        context = replace(
            context,
            checkpoint=checkpoint,
            checkpoint_factory=first,
            checkpoint_writer=self.checkpoint_store.write,
        )
        return _job(run, question, context, lease_acquired=True, attempt=attempt)


@dataclass(frozen=True, slots=True)
class RuntimeJobBuilder:
    """Production delivery seam selecting the durable task-level executor.

    Queue/HTTP code can remain unaware of checkpoint mechanics: a newly
    queued L2 run and a ``begin_resume``-acquired L2 run both pass here, while
    legacy L1 construction stays injected and deliberately separate.
    """

    l1_builder: L1JobBuilder
    l2_factory: L2JobFactory

    async def build(
        self, run: RunRecord, question: str, *, acquired_attempt: int | None = None
    ) -> RunJob:
        if run.task_level == "L2":
            return await self.l2_factory.job_for_delivery(
                run, question, acquired_attempt=acquired_attempt
            )
        if acquired_attempt is not None:
            raise ValueError("l1_resume_delivery_not_supported")
        return self.l1_builder(run, question)


def _job(
    run: RunRecord,
    question: str,
    context: L2RunContext,
    *,
    lease_acquired: bool,
    attempt: int,
) -> RunJob:
    return RunJob(
        run_id=run.run_id,
        question=question,
        planner_context=PlannerContext(
            source_manifest=context.planner_context.source_manifest,
            corpus_manifest_id=run.corpus_manifest_id,
            evaluation_root_id=run.evaluation_root_id or "invalid-root",
            run_id=str(run.run_id),
            model_id=run.model_id,
            idempotency_key=f"{run.run_id}-planning",
            task_level=TaskLevel.L2,
        ),
        corpus_manifest_id=run.corpus_manifest_id,
        answer_context={},
        task_level="L2",
        l2_context=context,
        lease_acquired=lease_acquired,
        attempt=attempt,
    )


def _require_l2(run: RunRecord) -> None:
    if run.task_level != "L2" or run.evaluation_root_id is None:
        raise ValueError("l2_run_binding_invalid")


def _require_bindings(run: RunRecord, checkpoint: RunCheckpoint) -> None:
    expected = (
        ("run_id", run.run_id),
        ("query_hash", run.query_hash),
        ("evaluation_root_id", run.evaluation_root_id),
        ("corpus_manifest_id", run.corpus_manifest_id),
        ("policy_hash", run.policy_hash),
        ("configuration_hash", run.configuration_hash),
        ("provider_id", run.provider_id),
        ("model_id", run.model_id),
    )
    if any(getattr(checkpoint, key) != value for key, value in expected):
        raise ValueError("resume_checkpoint_binding_mismatch")


__all__ = ["L2CheckpointStore", "L2JobFactory", "RuntimeJobBuilder"]
