from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from specpilot.answer.run import AnswerOutcome
from specpilot.contracts.answer import AnswerVerdict, RefusalReason, VerifiedAnswer
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.providers.transport import TransportReplayError
from specpilot.runs.contracts import (
    AgentStepEvent,
    AgentStepPhase,
    AnswerOutcomeEvent,
    EgressSummaryEvent,
    EvidenceSummaryEvent,
    PlanSummaryEvent,
    RunEvent,
    RunStatus,
    TerminalEvent,
    ToolFinishedEvent,
)
from specpilot.runtime.worker import (
    RunJob,
    RunWorker,
    WorkerQueueFull,
    WorkerUnavailable,
)

pytestmark = pytest.mark.anyio


@dataclass
class FakeStore:
    claim_result: bool = True
    heartbeat_results: list[bool] = field(default_factory=lambda: [True])
    calls: list[str] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)
    terminals: list[TerminalEvent] = field(default_factory=list)
    claimed: asyncio.Event = field(default_factory=asyncio.Event)
    heartbeat_error: Exception | None = None

    async def claim(
        self, run_id: UUID, lease_owner: str, *, lease_seconds: int
    ) -> bool:
        self.calls.append("claim")
        self.claimed.set()
        return self.claim_result

    async def heartbeat(
        self, run_id: UUID, lease_owner: str, *, lease_seconds: int
    ) -> bool:
        self.calls.append("heartbeat")
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        if self.heartbeat_results:
            return self.heartbeat_results.pop(0)
        return True

    async def append(
        self, run_id: UUID, lease_owner: str, event: RunEvent
    ) -> RunEvent:
        allocated = event.model_copy(update={"sequence": len(self.events) + 3})
        self.events.append(allocated)
        self.calls.append(f"append:{event.kind.value}")
        return allocated

    async def complete(
        self, run_id: UUID, lease_owner: str, event: TerminalEvent
    ) -> bool:
        self.calls.append("complete")
        self.terminals.append(event)
        return True


@dataclass
class FakePlan:
    plan_id: str = "plan-1"
    steps: tuple[Any, ...] = (object(),)


@dataclass
class FakePlanner:
    calls: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    block: asyncio.Event | None = None
    error: BaseException | None = None

    async def plan(self, question: str, context: object) -> FakePlan:
        self.calls += 1
        self.entered.set()
        if self.block is not None:
            await self.block.wait()
        if self.error is not None:
            raise self.error
        return FakePlan()


@dataclass
class FakeEvidenceResult:
    evidence: tuple[Any, ...] = ()
    calls: tuple[Any, ...] = ()


@dataclass
class FakeEvidenceAgent:
    calls: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    block: asyncio.Event | None = None

    async def collect(
        self, plan: object, corpus_manifest_id: str
    ) -> FakeEvidenceResult:
        self.calls += 1
        self.entered.set()
        if self.block is not None:
            await self.block.wait()
        return FakeEvidenceResult()


@dataclass
class FakeAnswerer:
    outcome: AnswerOutcome
    calls: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    block: asyncio.Event | None = None
    transports: list[object] = field(default_factory=list)

    async def __call__(
        self, question: str, evidence: object, **kwargs: object
    ) -> AnswerOutcome:
        self.calls += 1
        self.transports.append(kwargs["transport"])
        self.entered.set()
        if self.block is not None:
            await self.block.wait()
        return self.outcome


def refused_outcome(
    *, provider_error: str | None = None
) -> AnswerOutcome:
    return AnswerOutcome(
        verified=VerifiedAnswer(
            verdict=AnswerVerdict.REFUSED,
            refusal_reason=RefusalReason.EVIDENCE_INSUFFICIENT,
        ),
        reservation_id="00000000-0000-0000-0000-000000000001",
        replayed=False,
        request_size=None,
        provider_error=provider_error,
        parse_fault=None,
    )


def job(question: str = "raw private question") -> RunJob:
    return RunJob(
        run_id=uuid4(),
        question=question,
        planner_context=object(),
        corpus_manifest_id="c" * 64,
        answer_context={
            "model_id": "fixture-model",
            "source_manifest": object(),
            "corpus_manifest_id": "c" * 64,
            "evaluation_root_id": "root-1",
            "run_id": "run-1",
            "idempotency_key": "answer-1",
        },
    )


def job_with_transport_override(transport: object) -> RunJob:
    made = job()
    return RunJob(
        run_id=made.run_id,
        question=made.question,
        planner_context=made.planner_context,
        corpus_manifest_id=made.corpus_manifest_id,
        answer_context={**made.answer_context, "transport": transport},
    )


def worker(
    *,
    store: FakeStore | None = None,
    planner: FakePlanner | None = None,
    evidence: FakeEvidenceAgent | None = None,
    answerer: FakeAnswerer | None = None,
    queue_capacity: int = 2,
    heartbeat_interval_seconds: float = 0.01,
    answer_transport: object | None = None,
) -> tuple[RunWorker, FakeStore, FakePlanner, FakeEvidenceAgent, FakeAnswerer]:
    made_store = store or FakeStore()
    made_planner = planner or FakePlanner()
    made_evidence = evidence or FakeEvidenceAgent()
    made_answerer = answerer or FakeAnswerer(refused_outcome())
    made = RunWorker(
        store=made_store,
        planner=made_planner,
        evidence_agent=made_evidence,
        answer_transport=object() if answer_transport is None else answer_transport,
        answer_runner=made_answerer,
        worker_id="worker-1",
        queue_capacity=queue_capacity,
        lease_seconds=1,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    return made, made_store, made_planner, made_evidence, made_answerer


async def wait_terminal(store: FakeStore) -> TerminalEvent:
    for _ in range(100):
        if store.terminals:
            return store.terminals[0]
        await asyncio.sleep(0.001)
    raise AssertionError("worker did not finish")


async def test_worker_claims_then_runs_each_stage_once_and_writes_typed_trace() -> None:
    made, store, planner, evidence, answerer = worker()
    submitted = job()

    await made.start()
    await made.submit(submitted)
    terminal = await wait_terminal(store)
    await made.aclose()

    assert store.calls[0] == "claim"
    assert planner.calls == evidence.calls == answerer.calls == 1
    assert terminal.status is RunStatus.REFUSED
    assert terminal.reason == "evidence_insufficient"
    assert any(isinstance(event, PlanSummaryEvent) for event in store.events)
    assert [
        (event.agent.value, event.phase)
        for event in store.events
        if isinstance(event, AgentStepEvent)
    ] == [
        ("orchestrator", AgentStepPhase.STARTED),
        ("orchestrator", AgentStepPhase.FINISHED),
        ("evidence_agent", AgentStepPhase.STARTED),
        ("evidence_agent", AgentStepPhase.FINISHED),
        ("answer", AgentStepPhase.STARTED),
        ("answer", AgentStepPhase.FINISHED),
    ]
    assert any(isinstance(event, EvidenceSummaryEvent) for event in store.events)
    assert any(isinstance(event, AnswerOutcomeEvent) for event in store.events)
    assert not any(isinstance(event, ToolFinishedEvent) for event in store.events)
    assert submitted.question not in repr(submitted)
    assert all(
        submitted.question not in event.model_dump_json() for event in store.events
    )


async def test_submit_has_explicit_lifecycle_and_sanitized_backpressure() -> None:
    planner = FakePlanner(block=asyncio.Event())
    made, store, _, _, _ = worker(
        planner=planner,
        queue_capacity=1,
        heartbeat_interval_seconds=0.1,
    )
    private = "question-must-not-leak"

    with pytest.raises(WorkerUnavailable, match="worker_not_started") as before:
        await made.submit(job(private))
    assert private not in repr(before.value)

    await made.start()
    await made.submit(job(private))
    await planner.entered.wait()
    await made.submit(job(private))
    with pytest.raises(WorkerQueueFull, match="worker_queue_full") as full:
        await made.submit(job(private))
    assert private not in repr(full.value)

    await made.aclose()
    await made.aclose()
    with pytest.raises(WorkerUnavailable, match="worker_closed") as closed:
        await made.submit(job(private))
    assert private not in repr(closed.value)
    assert store.terminals == []
    assert store.calls.count("claim") == 1


async def test_claim_loser_makes_no_planner_or_provider_call() -> None:
    made, store, planner, evidence, answerer = worker(
        store=FakeStore(claim_result=False)
    )

    await made.start()
    await made.submit(job())
    await store.claimed.wait()
    await asyncio.sleep(0)
    await made.aclose()

    assert planner.calls == evidence.calls == answerer.calls == 0
    assert store.events == []
    assert store.terminals == []


async def test_provider_error_wins_over_refused_answer_in_worker() -> None:
    made, store, _, _, _ = worker(
        answerer=FakeAnswerer(refused_outcome(provider_error="provider_timeout"))
    )

    await made.start()
    await made.submit(job())
    terminal = await wait_terminal(store)
    await made.aclose()

    assert terminal.status is RunStatus.FAILED
    assert terminal.reason == "provider_timeout"


async def test_job_cannot_replace_worker_owned_answer_transport() -> None:
    worker_transport = object()
    job_transport = object()
    answerer = FakeAnswerer(refused_outcome())
    made, store, _, _, _ = worker(
        answerer=answerer,
        answer_transport=worker_transport,
    )

    await made.start()
    await made.submit(job_with_transport_override(job_transport))
    await wait_terminal(store)
    await made.aclose()

    assert answerer.transports == [worker_transport]


async def test_gate_error_is_blocked_but_transport_replay_is_not() -> None:
    gate = FakePlanner(
        error=EgressPolicyViolation(
            "root_unique_excerpts_exceeded", "raw private question"
        )
    )
    made, store, _, evidence, answerer = worker(planner=gate)

    await made.start()
    await made.submit(job())
    terminal = await wait_terminal(store)
    await made.aclose()

    assert terminal.status is RunStatus.EGRESS_BLOCKED
    assert terminal.reason == "root_unique_excerpts_exceeded"
    blocked = [event for event in store.events if isinstance(event, EgressSummaryEvent)]
    assert len(blocked) == 1
    assert blocked[0].admitted is False
    assert blocked[0].error_code == "root_unique_excerpts_exceeded"
    assert "raw private question" not in blocked[0].model_dump_json()
    assert evidence.calls == answerer.calls == 0

    replay_store = FakeStore()
    replay = FakePlanner(error=TransportReplayError("reservation-1"))
    replay_worker, _, _, replay_evidence, replay_answerer = worker(
        store=replay_store, planner=replay
    )
    await replay_worker.start()
    await replay_worker.submit(job())
    await replay.entered.wait()
    await asyncio.sleep(0)
    await replay_worker.aclose()

    assert replay_store.terminals == []
    assert replay_evidence.calls == replay_answerer.calls == 0


async def test_lost_heartbeat_stops_before_next_stage_and_never_completes() -> None:
    release = asyncio.Event()
    planner = FakePlanner(block=release)
    store = FakeStore(heartbeat_results=[False])
    made, _, _, evidence, answerer = worker(store=store, planner=planner)

    await made.start()
    await made.submit(job())
    await planner.entered.wait()
    for _ in range(100):
        if "heartbeat" in store.calls:
            break
        await asyncio.sleep(0.001)
    release.set()
    await asyncio.sleep(0.01)
    await made.aclose()

    assert "heartbeat" in store.calls
    assert evidence.calls == answerer.calls == 0
    assert len(store.events) == 1
    assert isinstance(store.events[0], AgentStepEvent)
    assert store.events[0].phase is AgentStepPhase.STARTED
    assert store.terminals == []


@pytest.mark.parametrize("stage", ["plan", "mcp", "answer"])
async def test_shutdown_cancels_each_stage_without_terminal_completion(
    stage: str,
) -> None:
    planner = FakePlanner(block=asyncio.Event() if stage == "plan" else None)
    evidence = FakeEvidenceAgent(
        block=asyncio.Event() if stage == "mcp" else None
    )
    answerer = FakeAnswerer(
        refused_outcome(),
        block=asyncio.Event() if stage == "answer" else None,
    )
    made, store, _, _, _ = worker(
        planner=planner,
        evidence=evidence,
        answerer=answerer,
    )

    await made.start()
    await made.submit(job())
    entered = {
        "plan": planner.entered,
        "mcp": evidence.entered,
        "answer": answerer.entered,
    }[stage]
    await entered.wait()
    await asyncio.wait_for(made.aclose(), timeout=0.2)

    assert store.terminals == []
    assert planner.calls <= 1
    assert evidence.calls <= 1
    assert answerer.calls <= 1


async def test_local_crash_is_left_for_lease_expiry_without_raw_trace() -> None:
    private = "raw private question"
    planner = FakePlanner(error=RuntimeError(private))
    made, store, _, evidence, answerer = worker(planner=planner)

    await made.start()
    await made.submit(job(private))
    await planner.entered.wait()
    await asyncio.sleep(0)
    await made.aclose()

    assert store.terminals == []
    assert len(store.events) == 1
    assert private not in store.events[0].model_dump_json()
    assert evidence.calls == answerer.calls == 0
    assert private not in repr(made)


async def test_invalid_plan_is_refused_without_mcp_or_answer_call() -> None:
    from specpilot.agents.planner import InvalidToolPlan

    planner = FakePlanner(error=InvalidToolPlan())
    made, store, _, evidence, answerer = worker(planner=planner)

    await made.start()
    await made.submit(job())
    terminal = await wait_terminal(store)
    await made.aclose()

    assert terminal == TerminalEvent(
        sequence=1,
        status=RunStatus.REFUSED,
        reason="invalid_tool_plan",
    )
    assert evidence.calls == answerer.calls == 0


async def test_heartbeat_error_abandons_lease_without_next_stage() -> None:
    private = "database connection detail"
    release = asyncio.Event()
    planner = FakePlanner(block=release)
    store = FakeStore(heartbeat_error=RuntimeError(private))
    made, _, _, evidence, answerer = worker(store=store, planner=planner)

    await made.start()
    await made.submit(job())
    await planner.entered.wait()
    for _ in range(100):
        if "heartbeat" in store.calls:
            break
        await asyncio.sleep(0.001)
    release.set()
    await asyncio.sleep(0.01)
    await made.aclose()

    assert evidence.calls == answerer.calls == 0
    assert store.terminals == []
    assert all(private not in event.model_dump_json() for event in store.events)
