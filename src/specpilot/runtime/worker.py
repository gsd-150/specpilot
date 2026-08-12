"""Bounded, leased execution of one in-memory L1 question per queued run."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import anyio

from specpilot.agents.contracts import ToolCallSummary, ToolPlan
from specpilot.agents.evidence import EvidenceResult
from specpilot.agents.planner import InvalidToolPlan, PlannerContext, PlannerResult
from specpilot.answer.run import AnswerOutcome, run_answer
from specpilot.contracts.egress import EgressStage
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import RequestSize
from specpilot.providers.transport import ProviderAttemptError
from specpilot.runs.contracts import (
    AgentName,
    AgentStepEvent,
    AgentStepPhase,
    AnswerOutcomeEvent,
    EgressSummaryEvent,
    EvidenceRefSummary,
    EvidenceSummaryEvent,
    PlanSummaryEvent,
    RunEvent,
    RunStatus,
    TerminalEvent,
    ToolFinishedEvent,
    VerifierCheckSummary,
    VerifierSummaryEvent,
)
from specpilot.runs.outcomes import Terminal, project_answer_outcome, project_gate_error


class RunStore(Protocol):
    async def claim(
        self, run_id: UUID, lease_owner: str, *, lease_seconds: int
    ) -> bool: ...

    async def heartbeat(
        self, run_id: UUID, lease_owner: str, *, lease_seconds: int
    ) -> bool: ...

    async def append(
        self, run_id: UUID, lease_owner: str, event: RunEvent
    ) -> RunEvent: ...

    async def complete(
        self, run_id: UUID, lease_owner: str, event: TerminalEvent
    ) -> bool: ...


class RunPlanner(Protocol):
    async def plan(self, question: str, context: PlannerContext) -> PlannerResult: ...


class RunEvidenceAgent(Protocol):
    async def collect(
        self, plan: ToolPlan, corpus_manifest_id: str
    ) -> EvidenceResult: ...


type AnswerRunner = Callable[..., Awaitable[AnswerOutcome]]


class WorkerError(RuntimeError):
    """Stable, detail-free queue lifecycle error."""


class WorkerUnavailable(WorkerError):
    pass


class WorkerQueueFull(WorkerError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class RunJob:
    """Ephemeral work: the question exists here and nowhere durable."""

    run_id: UUID
    question: str = field(repr=False)
    planner_context: PlannerContext
    corpus_manifest_id: str
    answer_context: Mapping[str, object] = field(repr=False)


class RunWorker:
    """Own one bounded AnyIO queue and one consumer task group explicitly."""

    def __init__(
        self,
        *,
        store: RunStore,
        planner: RunPlanner,
        evidence_agent: RunEvidenceAgent,
        answer_transport: object,
        worker_id: str,
        queue_capacity: int,
        lease_seconds: int,
        heartbeat_interval_seconds: float,
        answer_runner: AnswerRunner = run_answer,
    ) -> None:
        if queue_capacity <= 0 or lease_seconds <= 0:
            raise ValueError("invalid_worker_bound")
        if not 0 < heartbeat_interval_seconds < lease_seconds:
            raise ValueError("invalid_heartbeat_interval")
        self._store = store
        self._planner = planner
        self._evidence_agent = evidence_agent
        self._answer_transport = answer_transport
        self._answer_runner = answer_runner
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._queue_capacity = queue_capacity
        self._send, self._receive = anyio.create_memory_object_stream[RunJob](
            queue_capacity
        )
        self._supervisor_task: asyncio.Task[None] | None = None
        self._supervisor_ready = asyncio.Event()
        self._started = False
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                raise WorkerUnavailable("worker_closed")
            if self._started:
                return
            supervisor = asyncio.create_task(self._supervise())
            self._supervisor_task = supervisor
            try:
                await self._supervisor_ready.wait()
            except BaseException:
                supervisor.cancel()
                with suppress(asyncio.CancelledError):
                    await supervisor
                if self._supervisor_task is supervisor:
                    self._supervisor_task = None
                self._send, self._receive = anyio.create_memory_object_stream[RunJob](
                    self._queue_capacity
                )
                self._supervisor_ready = asyncio.Event()
                raise
            self._started = True

    async def submit(self, job: RunJob) -> None:
        if self._closed:
            raise WorkerUnavailable("worker_closed")
        if not self._started:
            raise WorkerUnavailable("worker_not_started")
        try:
            self._send.send_nowait(job)
        except anyio.WouldBlock:
            raise WorkerQueueFull("worker_queue_full") from None
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            raise WorkerUnavailable("worker_closed") from None

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            await self._send.aclose()
            supervisor = self._supervisor_task
            if supervisor is not None:
                supervisor.cancel()
                with suppress(asyncio.CancelledError):
                    await supervisor
            self._supervisor_task = None
            self._started = False

    async def _supervise(self) -> None:
        """Own the receive stream from entry through exit in one task."""
        async with self._receive:
            self._supervisor_ready.set()
            await self._consume()

    async def _consume(self) -> None:
        async for job in self._receive:
            await self._run_safely(job)

    async def _run_safely(self, job: RunJob) -> None:
        try:
            await self._run(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A local crash has no authorized terminal state. The lease expires
            # and the owner-scoped read projects it as interrupted; it is never
            # silently retried and never mislabeled as a provider failure.
            return

    async def _run(self, job: RunJob) -> None:
        claimed = await self._store.claim(
            job.run_id,
            self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        if not claimed:
            return

        lease_lost = asyncio.Event()
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(job.run_id, lease_lost, stop_heartbeat)
        )
        terminal: Terminal | None = None
        active_stage: tuple[AgentName, str, float] | None = None
        egress_stage = EgressStage.PLANNING
        try:
            planning_started = time.monotonic()
            active_stage = (AgentName.ORCHESTRATOR, "planning", planning_started)
            await self._append(
                job.run_id,
                _agent_event(
                    AgentName.ORCHESTRATOR,
                    "planning",
                    AgentStepPhase.STARTED,
                ),
            )
            if lease_lost.is_set():
                return
            planning = await self._planner.plan(job.question, job.planner_context)
            if lease_lost.is_set():
                return
            await self._append(job.run_id, _planning_egress_event(planning))
            if lease_lost.is_set():
                return
            plan = planning.plan
            await self._append(
                job.run_id,
                PlanSummaryEvent(
                    sequence=1,
                    plan_id=plan.plan_id,
                    step_count=len(plan.steps),
                    max_tool_calls=6,
                ),
            )
            if lease_lost.is_set():
                return
            await self._append(
                job.run_id,
                _agent_event(
                    AgentName.ORCHESTRATOR,
                    "planning",
                    AgentStepPhase.FINISHED,
                    started=planning_started,
                ),
            )
            active_stage = None
            if lease_lost.is_set():
                return

            evidence_started = time.monotonic()
            active_stage = (AgentName.EVIDENCE_AGENT, "evidence", evidence_started)
            await self._append(
                job.run_id,
                _agent_event(
                    AgentName.EVIDENCE_AGENT,
                    "evidence",
                    AgentStepPhase.STARTED,
                ),
            )
            if lease_lost.is_set():
                return
            result = await self._evidence_agent.collect(plan, job.corpus_manifest_id)
            if lease_lost.is_set():
                return
            await self._append_tool_summaries(job.run_id, result.calls)
            if lease_lost.is_set():
                return
            selected_evidence = tuple(result.evidence[:5])
            await self._append(
                job.run_id,
                EvidenceSummaryEvent(
                    sequence=1,
                    evidence=tuple(
                        EvidenceRefSummary(
                            evidence_id=item.disclosed.content_hash,
                            content_hash=item.disclosed.content_hash,
                        )
                        for item in selected_evidence
                    ),
                ),
            )
            if lease_lost.is_set():
                return
            await self._append(
                job.run_id,
                _agent_event(
                    AgentName.EVIDENCE_AGENT,
                    "evidence",
                    AgentStepPhase.FINISHED,
                    started=evidence_started,
                ),
            )
            active_stage = None
            if lease_lost.is_set():
                return

            answer_context = dict(job.answer_context)
            # The worker owns the one outward boundary. An in-memory job may
            # carry call metadata, but it may not replace that transport.
            answer_context["transport"] = self._answer_transport
            answer_started = time.monotonic()
            egress_stage = EgressStage.EVIDENCE
            active_stage = (AgentName.ANSWER, "answer", answer_started)
            await self._append(
                job.run_id,
                _agent_event(AgentName.ANSWER, "answer", AgentStepPhase.STARTED),
            )
            if lease_lost.is_set():
                return
            outcome = await self._answer_runner(
                job.question,
                selected_evidence,
                **answer_context,
            )
            if lease_lost.is_set():
                return
            answer_egress = _answer_egress_event(outcome)
            if answer_egress is not None:
                await self._append(job.run_id, answer_egress)
                if lease_lost.is_set():
                    return
            await self._append(
                job.run_id, _verifier_event(outcome, started=answer_started)
            )
            if lease_lost.is_set():
                return
            await self._append(job.run_id, _answer_event(outcome))
            if lease_lost.is_set():
                return
            await self._append(
                job.run_id,
                _agent_event(
                    AgentName.ANSWER,
                    "answer",
                    AgentStepPhase.FINISHED,
                    started=answer_started,
                    error_code=outcome.provider_error,
                ),
            )
            active_stage = None
            terminal = project_answer_outcome(outcome)
        except ProviderAttemptError as error:
            if lease_lost.is_set():
                return
            await self._append(
                job.run_id,
                _admitted_egress_event(
                    EgressStage.PLANNING,
                    reservation_id=error.reservation_id,
                    replayed=error.replayed,
                    request_size=error.request_size,
                ),
            )
            if lease_lost.is_set():
                return
            await self._finish_error_stage(
                job.run_id, active_stage, error.public_error_code
            )
            active_stage = None
            terminal = Terminal(RunStatus.FAILED, error.public_error_code)
        except InvalidToolPlan as error:
            if lease_lost.is_set():
                return
            await self._append(
                job.run_id,
                _admitted_egress_event(
                    EgressStage.PLANNING,
                    reservation_id=error.reservation_id,
                    replayed=error.replayed,
                    request_size=error.request_size,
                ),
            )
            if lease_lost.is_set():
                return
            await self._finish_error_stage(job.run_id, active_stage, str(error))
            active_stage = None
            terminal = Terminal(RunStatus.REFUSED, "invalid_tool_plan")
        except EgressPolicyViolation as error:
            terminal = project_gate_error(error)
            if lease_lost.is_set():
                return
            await self._append(
                job.run_id, _blocked_egress_event(egress_stage, error.code)
            )
            if lease_lost.is_set():
                return
            await self._finish_error_stage(job.run_id, active_stage, error.code)
            active_stage = None
        finally:
            stop_heartbeat.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

        if terminal is None or lease_lost.is_set():
            return
        await self._store.complete(
            job.run_id,
            self._worker_id,
            TerminalEvent(
                sequence=1,
                status=terminal.status,
                reason=terminal.reason,
            ),
        )

    async def _finish_error_stage(
        self,
        run_id: UUID,
        stage: tuple[AgentName, str, float] | None,
        error_code: str,
    ) -> None:
        if stage is None:
            return
        agent, step_id, started = stage
        await self._append(
            run_id,
            _agent_event(
                agent,
                step_id,
                AgentStepPhase.FINISHED,
                started=started,
                error_code=error_code,
            ),
        )

    async def _heartbeat(
        self,
        run_id: UUID,
        lease_lost: asyncio.Event,
        stop: asyncio.Event,
    ) -> None:
        try:
            while not stop.is_set():
                await asyncio.sleep(self._heartbeat_interval)
                if stop.is_set():
                    return
                owned = await self._store.heartbeat(
                    run_id,
                    self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
                if not owned:
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            lease_lost.set()

    async def _append(self, run_id: UUID, event: RunEvent) -> None:
        await self._store.append(run_id, self._worker_id, event)

    async def _append_tool_summaries(
        self, run_id: UUID, calls: Sequence[ToolCallSummary]
    ) -> None:
        for call in calls:
            await self._append(
                run_id,
                ToolFinishedEvent(sequence=1, **call.model_dump()),
            )


def _answer_event(outcome: AnswerOutcome) -> AnswerOutcomeEvent:
    return AnswerOutcomeEvent(
        sequence=1,
        verdict=outcome.verified.verdict,
        refusal_reason=(
            outcome.verified.refusal_reason.value
            if outcome.verified.refusal_reason is not None
            else None
        ),
        provider_error=outcome.provider_error,
        reservation_id=(
            UUID(outcome.reservation_id) if outcome.reservation_id is not None else None
        ),
        replayed=outcome.replayed,
        parse_fault_code=outcome.parse_fault,
    )


def _agent_event(
    agent: AgentName,
    step_id: str,
    phase: AgentStepPhase,
    *,
    started: float | None = None,
    error_code: str | None = None,
) -> AgentStepEvent:
    return AgentStepEvent(
        sequence=1,
        agent=agent,
        step_id=step_id,
        phase=phase,
        duration_ms=(
            None
            if started is None
            else max(int((time.monotonic() - started) * 1000), 0)
        ),
        error_code=error_code,
    )


def _planning_egress_event(result: PlannerResult) -> EgressSummaryEvent:
    return _admitted_egress_event(
        EgressStage.PLANNING,
        reservation_id=result.reservation_id,
        replayed=result.replayed,
        request_size=result.request_size,
    )


def _admitted_egress_event(
    stage: EgressStage,
    *,
    reservation_id: str,
    replayed: bool,
    request_size: RequestSize | None,
) -> EgressSummaryEvent:
    tokens = request_size.request_tokens if request_size is not None else None
    byte_count = request_size.request_bytes if request_size is not None else None
    return EgressSummaryEvent(
        sequence=1,
        stage=stage,
        reservation_id=UUID(reservation_id),
        ledger_id=None,
        admitted=True,
        replayed=replayed,
        request_tokens=tokens,
        request_bytes=byte_count,
        cost_microunits=None,
        error_code=None,
    )


def _blocked_egress_event(stage: EgressStage, error_code: str) -> EgressSummaryEvent:
    return EgressSummaryEvent(
        sequence=1,
        stage=stage,
        reservation_id=None,
        ledger_id=None,
        admitted=False,
        replayed=False,
        request_tokens=None,
        request_bytes=None,
        cost_microunits=None,
        error_code=error_code,
    )


def _answer_egress_event(outcome: AnswerOutcome) -> EgressSummaryEvent | None:
    if outcome.reservation_id is None:
        return None
    return _admitted_egress_event(
        EgressStage.EVIDENCE,
        reservation_id=outcome.reservation_id,
        replayed=outcome.replayed,
        request_size=outcome.request_size,
    )


def _verifier_event(outcome: AnswerOutcome, *, started: float) -> VerifierSummaryEvent:
    if outcome.verified.citations:
        checks = tuple(
            VerifierCheckSummary(
                evidence_id=citation.content_hash,
                passed=True,
                fault_code=None,
            )
            for citation in outcome.verified.citations
        )
    else:
        faults = outcome.verified.citation_faults
        if not faults and outcome.parse_fault is not None:
            faults = (outcome.parse_fault,)
        checks = tuple(
            VerifierCheckSummary(
                evidence_id=None,
                passed=False,
                fault_code=fault.rsplit(":", 1)[-1],
            )
            for fault in faults
        )
    return VerifierSummaryEvent(
        sequence=1,
        checks=checks,
        duration_ms=max(int((time.monotonic() - started) * 1000), 0),
    )


__all__ = [
    "RunJob",
    "RunWorker",
    "WorkerError",
    "WorkerQueueFull",
    "WorkerUnavailable",
]
