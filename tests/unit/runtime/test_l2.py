"""Cross-gate RED coverage for the L2 orchestration boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from specpilot.agents.compliance import ComplianceContext, ComplianceOutcome
from specpilot.agents.evidence import EvidenceResult
from specpilot.agents.planner import PlannerContext, PlannerResult
from specpilot.answer.evidence import Evidence, build_evidence_from_unit
from specpilot.contracts.answer import Citation
from specpilot.contracts.verdict import (
    ComplianceBatch,
    ComplianceCandidate,
    ComplianceVerdict,
    IdentifiedCandidate,
    SemanticDecision,
    SemanticEvidenceDecision,
)
from specpilot.corpus.indexable import IndexUnit
from specpilot.egress.ledger import RequestSize
from specpilot.verifier.deterministic import (
    DeterministicCheck,
    DeterministicFault,
    DeterministicResult,
)
from specpilot.verifier.recovery import RecoveryOutcome
from specpilot.verifier.semantic import SemanticContext, SemanticOutcome
from tests.unit.egress.test_policy_projection import egress_request

pytestmark = pytest.mark.anyio


def evidence() -> Evidence:
    unit = IndexUnit(
        unit_id="a" * 64,
        kind="clause",
        document_id="RFC9110",
        document_version="RFC9110-2022",
        section_number="1",
        section_path="Fixture > 1",
        ordinal=1,
        text="A sender MUST preserve the fixture.",
        indexed="fixture",
    )
    return build_evidence_from_unit(unit, corpus_manifest_id="c" * 64)


def candidate(item: Evidence) -> IdentifiedCandidate:
    proposed = ComplianceCandidate(
        claim="The design preserves the fixture.",
        proposed_verdict=ComplianceVerdict.COMPLIANT,
        evidence_ids=(item.excerpt.content_hash,),
        rationale="fixture rationale",
    )
    return IdentifiedCandidate(
        claim_id=hashlib.sha256(proposed.claim.encode()).hexdigest(), candidate=proposed
    )


def passed(item: Evidence) -> DeterministicResult:
    return DeterministicResult(
        checks=(DeterministicCheck(item.excerpt.content_hash, None),),
        citations=(
            Citation(
                clause_id=item.disclosed.clause_id,
                corpus_manifest_id=item.disclosed.corpus_manifest_id,
                document_id=item.disclosed.document_id,
                document_version=item.disclosed.document_version,
                section_number=item.disclosed.section_number,
                content_hash=item.excerpt.content_hash,
            ),
        ),
    )


@dataclass
class Planner:
    calls: int = 0

    async def plan(self, question: str, context: object) -> PlannerResult:
        self.calls += 1
        return PlannerResult(
            plan=FakePlan(),  # type: ignore[arg-type]
            reservation_id="00000000-0000-0000-0000-000000000001",
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
        )


@dataclass
class FakePlan:
    plan_id: str = "plan-1"

    def model_dump_json(self) -> str:
        return '{"plan_id":"plan-1"}'


@dataclass
class EvidenceAgent:
    item: Evidence
    calls: int = 0

    async def collect(
        self, plan: object, corpus_manifest_id: str, **kwargs: object
    ) -> EvidenceResult:
        self.calls += 1
        return EvidenceResult((self.item,), (), int(kwargs.get("attempts_used", 0)))


@dataclass
class Compliance:
    candidate: IdentifiedCandidate
    calls: int = 0

    async def evaluate(
        self, description: str, evidence: tuple[Evidence, ...], context: object
    ) -> ComplianceOutcome:
        self.calls += 1
        return ComplianceOutcome(
            batch=ComplianceBatch(candidates=(self.candidate.candidate,)),
            candidates=(self.candidate,),
            reservation_id="00000000-0000-0000-0000-000000000002",
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
        )


@dataclass
class Semantic:
    replies: list[bool]
    calls: int = 0

    async def verify(
        self,
        candidate: IdentifiedCandidate,
        evidence: tuple[Evidence, ...],
        deterministic: DeterministicResult,
        context: object,
    ) -> SemanticOutcome:
        self.calls += 1
        supports = self.replies.pop(0)
        return SemanticOutcome(
            decision=SemanticDecision(
                supports_verdict=supports,
                evidence=(
                    SemanticEvidenceDecision(
                        evidence_id=candidate.candidate.evidence_ids[0],
                        supports=supports,
                    ),
                ),
                reason="supported" if supports else "unsupported",
                rationale="ephemeral fixture rationale",
            ),
            reservation_id=f"00000000-0000-0000-0000-00000000000{self.calls + 2}",
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
        )


def context(*, deterministic: object, semantic: Semantic):
    from specpilot.runtime.l2 import L2RunContext

    item = evidence()
    identified = candidate(item)
    planner = Planner()
    request = egress_request()
    planner_context = PlannerContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id="root-1",
        run_id="run-1",
        model_id=request.model_id,
        idempotency_key="plan",
        task_level="L2",
    )
    return L2RunContext(
        run_id="run-1",
        question="private design description",
        planner=planner,
        planner_context=planner_context,
        evidence_agent=EvidenceAgent(item),
        compliance_agent=Compliance(identified),
        compliance_context=ComplianceContext(
            source_manifest=request.source_manifest,
            corpus_manifest_id=request.version.corpus_manifest_id,
            evaluation_root_id="root-1",
            run_id="run-1",
            model_id=request.model_id,
            idempotency_key="compliance",
            reconstruction_generation=0,
        ),
        semantic_verifier=semantic,
        semantic_context=SemanticContext(
            source_manifest=request.source_manifest,
            corpus_manifest_id=request.version.corpus_manifest_id,
            evaluation_root_id="root-1",
            run_id="run-1",
            model_id=request.model_id,
            idempotency_key="semantic",
            reconstruction_generation=0,
        ),
        deterministic_verifier=deterministic,  # type: ignore[arg-type]
        recovery_runner=recovery(item),
    )


def recovery(item: Evidence):
    async def run(*args: object) -> RecoveryOutcome:
        return RecoveryOutcome((item,), (), 1)

    return run


def test_l2_module_is_available() -> None:
    """The state machine has a dedicated module, not an L1 branch."""
    from specpilot.runtime.l2 import logical_stage_key

    assert logical_stage_key("run", "compliance", None, False, 0) == (
        "run-compliance-initial-g0"
    )


async def test_l2_runs_both_gates_and_returns_a_verified_metadata_result() -> None:
    item = evidence()
    semantic = Semantic([True])
    made = context(deterministic=lambda *_: passed(item), semantic=semantic)

    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.results[0].verification_status.value == "verified"
    assert semantic.calls == 1
    assert outcome.recovery_attempted is False


async def test_deterministic_failure_recovers_once_then_reruns_every_gate() -> None:
    item = evidence()
    checks = [False, True]
    calls = 0

    def deterministic(*_: object) -> DeterministicResult:
        nonlocal calls
        calls += 1
        if checks.pop(0):
            return passed(item)
        return DeterministicResult(
            checks=(
                DeterministicCheck(
                    item.excerpt.content_hash, DeterministicFault.NOT_DISCLOSED
                ),
            ),
            citations=(),
        )

    semantic = Semantic([True])
    made = context(deterministic=deterministic, semantic=semantic)
    recovery_calls = 0

    async def recover(*args: object) -> RecoveryOutcome:
        nonlocal recovery_calls
        recovery_calls += 1
        return RecoveryOutcome((item,), (), 1)

    made = dataclass_replace(made, recovery_runner=recover)
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert calls == 2
    assert semantic.calls == 1
    assert recovery_calls == 1
    assert outcome.recovery_attempted
    assert outcome.results[0].verification_status.value == "verified"


def dataclass_replace(value: object, **changes: object):
    from dataclasses import replace

    return replace(value, **changes)
