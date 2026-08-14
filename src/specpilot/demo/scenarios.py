"""Closed fixture scenario registry with a sanitized public projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from specpilot.runs.contracts import RunEventKind, RunStatus


class DemoScenarioId(StrEnum):
    L1_ANSWERED = "l1_answered"
    L2_ANSWERED = "l2_answered"
    EVIDENCE_REFUSED = "evidence_refused"
    VERIFIER_RECOVERED = "verifier_recovered"


class PublicDemoScenario(BaseModel):
    """Only display-safe fields returned to the fixture browser."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: DemoScenarioId
    label: str
    description: str
    task_level: Literal["L1", "L2"]
    engineering_limitation: str


@dataclass(frozen=True, slots=True)
class _DemoScenario:
    scenario_id: DemoScenarioId
    label: str
    description: str
    task_level: Literal["L1", "L2"]
    engineering_limitation: str
    fixture_question: str
    script_version: str
    terminal_status: RunStatus
    required_event_kinds: frozenset[RunEventKind]

    def public(self) -> PublicDemoScenario:
        return PublicDemoScenario(
            scenario_id=self.scenario_id,
            label=self.label,
            description=self.description,
            task_level=self.task_level,
            engineering_limitation=self.engineering_limitation,
        )


_LIMITATION = (
    "Synthetic provider output demonstrates the engineering path, not answer quality."
)
_RETRIEVAL_QUESTION = "Which retry requirement is stated?"
_REGISTRY = (
    _DemoScenario(
        DemoScenarioId.L1_ANSWERED,
        "L1 answered",
        (
            "Runs retrieval, bounded disclosure, citation checks, and an "
            "answered terminal."
        ),
        "L1",
        _LIMITATION,
        _RETRIEVAL_QUESTION,
        "fixture-demo/l1-answered/v1",
        RunStatus.ANSWERED,
        frozenset(
            {
                RunEventKind.TOOL_FINISHED,
                RunEventKind.EGRESS_SUMMARY,
                RunEventKind.VERIFIER_SUMMARY,
                RunEventKind.ANSWER_OUTCOME,
                RunEventKind.TERMINAL,
            }
        ),
    ),
    _DemoScenario(
        DemoScenarioId.L2_ANSWERED,
        "L2 answered",
        "Runs planning, Compliance, deterministic and semantic verification.",
        "L2",
        _LIMITATION,
        _RETRIEVAL_QUESTION,
        "fixture-demo/l2-answered/v1",
        RunStatus.ANSWERED,
        frozenset(
            {
                RunEventKind.TOOL_FINISHED,
                RunEventKind.EGRESS_SUMMARY,
                RunEventKind.COMPLIANCE_SUMMARY,
                RunEventKind.VERIFIER_SUMMARY,
                RunEventKind.SEMANTIC_SUMMARY,
                RunEventKind.TERMINAL,
            }
        ),
    ),
    _DemoScenario(
        DemoScenarioId.EVIDENCE_REFUSED,
        "Evidence refused",
        "Runs the evidence path and reaches a normal insufficient-evidence refusal.",
        "L1",
        _LIMITATION,
        _RETRIEVAL_QUESTION,
        "fixture-demo/evidence-refused/v1",
        RunStatus.REFUSED,
        frozenset(
            {
                RunEventKind.TOOL_FINISHED,
                RunEventKind.EGRESS_SUMMARY,
                RunEventKind.VERIFIER_SUMMARY,
                RunEventKind.ANSWER_OUTCOME,
                RunEventKind.TERMINAL,
            }
        ),
    ),
    _DemoScenario(
        DemoScenarioId.VERIFIER_RECOVERED,
        "Verifier recovered",
        "Runs one semantic rejection, one directed recovery, and re-verification.",
        "L2",
        _LIMITATION,
        _RETRIEVAL_QUESTION,
        "fixture-demo/verifier-recovered/v1",
        RunStatus.ANSWERED,
        frozenset(
            {
                RunEventKind.COMPLIANCE_SUMMARY,
                RunEventKind.VERIFIER_SUMMARY,
                RunEventKind.SEMANTIC_SUMMARY,
                RunEventKind.RECOVERY_SUMMARY,
                RunEventKind.TERMINAL,
            }
        ),
    ),
)
_BY_ID = {item.scenario_id: item for item in _REGISTRY}


def public_demo_scenarios() -> tuple[PublicDemoScenario, ...]:
    return tuple(item.public() for item in _REGISTRY)


def scenario_for(scenario_id: DemoScenarioId) -> _DemoScenario:
    return _BY_ID[scenario_id]


def fixture_question_for(scenario_id: DemoScenarioId) -> str:
    return scenario_for(scenario_id).fixture_question


__all__ = [
    "DemoScenarioId",
    "PublicDemoScenario",
    "fixture_question_for",
    "public_demo_scenarios",
    "scenario_for",
]
