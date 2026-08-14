from __future__ import annotations

import json

import pytest

from specpilot.demo.scenarios import (
    DemoScenarioId,
    fixture_question_for,
    public_demo_scenarios,
    scenario_for,
)
from specpilot.providers.fake import FakeProvider
from specpilot.runs.contracts import RunEventKind, RunStatus
from tests.unit.egress.test_policy_projection import l1_payload
from tests.unit.providers.test_http_adapter import (
    l2_atomic_claim_payload,
    l2_design_payload,
)


def test_registry_has_exactly_four_versioned_sanitized_scenarios() -> None:
    expected = {
        "l1_answered": ("L1", RunStatus.ANSWERED, RunEventKind.ANSWER_OUTCOME),
        "l2_answered": ("L2", RunStatus.ANSWERED, RunEventKind.SEMANTIC_SUMMARY),
        "evidence_refused": ("L1", RunStatus.REFUSED, RunEventKind.ANSWER_OUTCOME),
        "verifier_recovered": (
            "L2",
            RunStatus.ANSWERED,
            RunEventKind.RECOVERY_SUMMARY,
        ),
    }

    assert {item.scenario_id for item in public_demo_scenarios()} == set(expected)
    for scenario_id, (task_level, terminal, required_kind) in expected.items():
        scenario = scenario_for(DemoScenarioId(scenario_id))
        assert scenario.task_level == task_level
        assert scenario.script_version.startswith("fixture-demo/")
        assert scenario.terminal_status is terminal
        assert required_kind in scenario.required_event_kinds
        assert scenario.label
        assert scenario.description
        assert scenario.engineering_limitation


def test_public_metadata_omits_private_question_and_provider_script() -> None:
    public = public_demo_scenarios()
    encoded = " ".join(item.model_dump_json() for item in public)

    for item in public:
        private_question = fixture_question_for(item.scenario_id)
        scenario = scenario_for(item.scenario_id)
        assert private_question not in encoded
        assert scenario.script_version not in encoded
        assert set(item.model_dump()) == {
            "scenario_id",
            "label",
            "description",
            "task_level",
            "engineering_limitation",
        }


@pytest.mark.anyio
async def test_evidence_refused_script_returns_a_normal_closed_refusal() -> None:
    provider = FakeProvider()
    provider.register_demo_script(
        "run-refused", scenario_for(DemoScenarioId.EVIDENCE_REFUSED).script_version
    )

    response = await provider.send_for_run(l1_payload(), run_id="run-refused")

    assert json.loads(response.content) == {
        "answer": None,
        "citations": [],
        "sufficient": False,
    }


@pytest.mark.anyio
async def test_verifier_recovered_script_fails_once_then_returns_to_default() -> None:
    provider = FakeProvider()
    provider.register_demo_script(
        "run-recovery",
        scenario_for(DemoScenarioId.VERIFIER_RECOVERED).script_version,
    )

    response = await provider.send_for_run(
        l2_design_payload(), run_id="run-recovery"
    )
    compliance = json.loads(response.content)
    first_id = compliance["candidates"][0]["evidence_ids"][0]
    first = json.loads(
        (
            await provider.send_for_run(
                l2_atomic_claim_payload(), run_id="run-recovery"
            )
        ).content
    )
    second = json.loads(
        (
            await provider.send_for_run(
                l2_atomic_claim_payload(), run_id="run-recovery"
            )
        ).content
    )

    assert first_id == l2_design_payload().evidence_excerpts[0].content_hash
    assert first["supports_verdict"] is False
    assert first["reason"] == "exception_missing"
    assert second["supports_verdict"] is True
