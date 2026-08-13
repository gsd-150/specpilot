from __future__ import annotations

import json
from typing import Any

import pytest

import specpilot.providers.transport as transport_module
from specpilot.agents.planner import InvalidToolPlan, Planner, PlannerContext
from specpilot.contracts.egress import TaskLevel
from specpilot.providers.fake import FakeProvider
from tests.unit.egress.test_planning_projection import planning_request
from tests.unit.providers.test_transport_fail_closed import (
    ReplayLedger,
    StubLedger,
    transport,
)

pytestmark = pytest.mark.anyio


class KeyLedger(StubLedger):
    def __init__(self) -> None:
        super().__init__()
        self.idempotency_keys: list[str] = []

    async def check_and_reserve(
        self, *args: Any, idempotency_key: str, **kwargs: Any
    ) -> Any:
        self.idempotency_keys.append(idempotency_key)
        return await super().check_and_reserve(
            *args, idempotency_key=idempotency_key, **kwargs
        )


async def test_invalid_content_spends_one_planning_attempt_and_executes_no_tools() -> (
    None
):
    request = planning_request(query="When may a sender retry?")
    provider = FakeProvider()
    provider.reply = "not-json"
    ledger = StubLedger()
    planner = Planner(transport(provider, ledger))
    context = PlannerContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id=request.evaluation_root_id,
        run_id=request.run_id,
        model_id=request.model_id,
        idempotency_key="plan-1",
    )

    with pytest.raises(InvalidToolPlan) as caught:
        await planner.plan("When may a sender retry?", context)

    assert str(caught.value) == "invalid_tool_plan"
    assert caught.value.reservation_id == "res-1"
    assert caught.value.replayed is False
    assert caught.value.request_size.request_bytes > 0
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert provider.call_count == 1
    assert len(ledger.reserved) == 1
    assert len(ledger.attempts) == 1


async def test_planner_reads_json_content_not_native_tool_calls() -> None:
    request = planning_request(query="When may a sender retry?")
    provider = FakeProvider()
    provider.reply = (
        '{"plan_id":"plan-1","steps":[{"step_id":"search",'
        '"tool":"search_clauses","args":{"query":"retry",'
        f'"corpus_manifest_id":"{request.version.corpus_manifest_id}",'
        f'"document_ids":["{request.version.document_id}"],'
        '"normative_levels":[],"limit":3},"depends_on":[]}]}'
    )
    planner = Planner(transport(provider, StubLedger()))
    context = PlannerContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id=request.evaluation_root_id,
        run_id=request.run_id,
        model_id=request.model_id,
        idempotency_key="plan-1",
    )

    result = await planner.plan("When may a sender retry?", context)

    assert result.plan.plan_id == "plan-1"
    assert [step.step_id for step in result.plan.steps] == ["search"]
    assert result.reservation_id == "res-1"
    assert result.replayed is False
    assert result.request_size.request_tokens == 1
    assert result.request_size.request_bytes > 0


async def test_planner_does_not_turn_closed_replay_into_an_invalid_plan() -> None:
    request = planning_request(query="When may a sender retry?")
    provider = FakeProvider()
    planner = Planner(transport(provider, ReplayLedger()))
    context = PlannerContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id=request.evaluation_root_id,
        run_id=request.run_id,
        model_id=request.model_id,
        idempotency_key="plan-1",
    )
    await planner.plan("When may a sender retry?", context)

    with pytest.raises(transport_module.TransportReplayError):
        await planner.plan("When may a sender retry?", context)

    assert provider.call_count == 1


async def test_l2_planning_admits_eight_calls_and_reconstruction_key() -> None:
    """Removing L2 context would give recovery runs the L1 six-call ceiling."""
    request = planning_request(query="When may a sender retry?")
    provider = FakeProvider()
    provider.reply = json.dumps(
        {
            "plan_id": "plan-1",
            "steps": [
                {
                    "step_id": "search",
                    "tool": "search_clauses",
                    "args": {
                        "query": "retry",
                        "corpus_manifest_id": request.version.corpus_manifest_id,
                        "document_ids": [request.version.document_id],
                        "normative_levels": [],
                        "limit": 3,
                    },
                    "depends_on": [],
                },
                {
                    "step_id": "first",
                    "tool": "get_clause",
                    "args": {
                        "corpus_manifest_id": request.version.corpus_manifest_id,
                        "document_id": request.version.document_id,
                        "clauses": {
                            "kind": "step_result",
                            "step_id": "search",
                            "take": 3,
                        },
                    },
                    "depends_on": ["search"],
                },
                {
                    "step_id": "second",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": request.version.corpus_manifest_id,
                        "document_id": request.version.document_id,
                        "clauses": {
                            "kind": "step_result",
                            "step_id": "search",
                            "take": 3,
                        },
                    },
                    "depends_on": ["search"],
                },
                {
                    "step_id": "toc",
                    "tool": "get_toc",
                    "args": {
                        "corpus_manifest_id": request.version.corpus_manifest_id,
                        "document_id": request.version.document_id,
                        "limit": 12,
                    },
                    "depends_on": [],
                },
            ],
        }
    )
    ledger = KeyLedger()
    planner = Planner(transport(provider, ledger))
    context = PlannerContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id=request.evaluation_root_id,
        run_id=request.run_id,
        model_id=request.model_id,
        idempotency_key="plan-1",
        task_level=TaskLevel.L2,
        reconstruction_generation=2,
    )

    result = await planner.plan("When may a sender retry?", context)

    assert result.plan.base_call_cost == 8
    assert ledger.reserved[0].projected_payload.max_tool_calls == 8
    assert ledger.idempotency_keys == ["plan-1-g2"]
