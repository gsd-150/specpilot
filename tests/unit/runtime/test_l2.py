"""Cross-gate RED coverage for the L2 orchestration boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from specpilot.agents.compliance import ComplianceContext, ComplianceOutcome
from specpilot.agents.evidence import EvidenceResult
from specpilot.agents.planner import PlannerContext, PlannerResult
from specpilot.answer.evidence import Evidence, build_evidence_from_unit
from specpilot.contracts.answer import Citation
from specpilot.contracts.verdict import (
    ComplianceBatch,
    ComplianceCandidate,
    ComplianceResult,
    ComplianceVerdict,
    IdentifiedCandidate,
    SemanticDecision,
    SemanticEvidenceDecision,
)
from specpilot.corpus.indexable import IndexUnit
from specpilot.egress.ledger import RequestSize
from specpilot.providers.transport import ProviderAttemptError
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


def candidate(
    item: Evidence, claim: str = "The design preserves the fixture."
) -> IdentifiedCandidate:
    proposed = ComplianceCandidate(
        claim=claim,
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
    contexts: list[object] = None  # type: ignore[assignment]
    cache_hit: bool = False

    def __post_init__(self) -> None:
        self.contexts = []

    async def plan(self, question: str, context: object) -> PlannerResult:
        self.calls += 1
        self.contexts.append(context)
        return PlannerResult(
            plan=FakePlan(),  # type: ignore[arg-type]
            reservation_id=(
                None
                if self.cache_hit
                else "00000000-0000-0000-0000-000000000001"
            ),
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
            cache_hit=self.cache_hit,
            cache_request_hash="1" * 64 if self.cache_hit else None,
            cache_record_hash="a" * 64 if self.cache_hit else None,
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
    cache_hit: bool = False

    async def evaluate(
        self, description: str, evidence: tuple[Evidence, ...], context: object
    ) -> ComplianceOutcome:
        self.calls += 1
        return ComplianceOutcome(
            batch=ComplianceBatch(candidates=(self.candidate.candidate,)),
            candidates=(self.candidate,),
            reservation_id=(
                None
                if self.cache_hit
                else "00000000-0000-0000-0000-000000000002"
            ),
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
            cache_hit=self.cache_hit,
            cache_request_hash="2" * 64 if self.cache_hit else None,
            cache_record_hash="b" * 64 if self.cache_hit else None,
        )


@dataclass
class BatchCompliance:
    candidates: tuple[IdentifiedCandidate, ...]
    calls: int = 0

    async def evaluate(
        self, description: str, evidence: tuple[Evidence, ...], context: object
    ) -> ComplianceOutcome:
        self.calls += 1
        return ComplianceOutcome(
            batch=ComplianceBatch(
                candidates=tuple(item.candidate for item in self.candidates)
            ),
            candidates=self.candidates,
            reservation_id="00000000-0000-0000-0000-000000000002",
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
        )


@dataclass
class Semantic:
    replies: list[bool]
    calls: int = 0
    claim_ids: list[str] = None  # type: ignore[assignment]
    cache_hit: bool = False

    def __post_init__(self) -> None:
        self.claim_ids = []

    async def verify(
        self,
        candidate: IdentifiedCandidate,
        evidence: tuple[Evidence, ...],
        deterministic: DeterministicResult,
        context: object,
    ) -> SemanticOutcome:
        self.calls += 1
        self.claim_ids.append(candidate.claim_id)
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
            reservation_id=(
                None
                if self.cache_hit
                else f"00000000-0000-0000-0000-00000000000{self.calls + 2}"
            ),
            replayed=False,
            request_size=RequestSize(request_tokens=1, request_bytes=1),
            cache_hit=self.cache_hit,
            cache_request_hash="3" * 64 if self.cache_hit else None,
            cache_record_hash="c" * 64 if self.cache_hit else None,
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


def checkpoint(stage: str = "planned", *, items: tuple[Evidence, ...] = ()):
    from specpilot.checkpoints.contracts import EvidenceCheckpointRef, RunCheckpoint

    return RunCheckpoint(
        run_id=uuid4(),
        attempt=1,
        checkpoint_version=1,
        stage=stage,
        task_level="L2",
        query_hash="a" * 64,
        evaluation_root_id="root-1",
        source_manifest_id="b" * 64,
        corpus_manifest_id="c" * 64,
        policy_hash="d" * 64,
        configuration_hash="e" * 64,
        compliance_prompt_hash="f" * 64,
        verifier_prompt_hash="0" * 64,
        provider_id="provider",
        model_id="model",
        plan_id="plan-1",
        plan_hash=hashlib.sha256(b'{"plan_id":"plan-1"}').hexdigest(),
        evidence=tuple(
            EvidenceCheckpointRef(
                evidence_id=item.excerpt.content_hash,
                content_hash=item.excerpt.content_hash,
                quote_hash=item.excerpt.quote_hash,
                clause_id=item.disclosed.clause_id,
                document_id=item.disclosed.document_id,
                document_version=item.disclosed.document_version,
                section_number=item.disclosed.section_number,
                paragraph_start=item.excerpt.span.paragraph_start,
                paragraph_end=item.excerpt.span.paragraph_end,
                token_start=item.excerpt.span.token_start,
                token_end=item.excerpt.span.token_end,
            )
            for item in items
        ),
        tool_attempts_used=0,
        reservation_ids=(),
        reconstruction_generations=(),
        recovery_attempted=stage in {"recovery_reserved", "recovery_completed"},
        recovery_reason=None,
        recovery_claim_id=(
            candidate(items[0] if items else evidence()).claim_id
            if stage == "recovery_reserved"
            else None
        ),
        candidate_count=0,
        completed_claim_ids=(),
        completed_results=(),
        last_accessed_at=datetime(2026, 8, 14, tzinfo=UTC),
    )


@dataclass
class StatefulWriter:
    current: object | None = None
    writes: int = 0

    async def __call__(self, previous_version: int | None, current: object):
        from specpilot.checkpoints.contracts import validate_transition

        prior = self.current
        assert previous_version == (None if prior is None else prior.checkpoint_version)
        if prior is not None:
            validate_transition(prior, current)
        self.current = current
        self.writes += 1
        return current


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
    assert made.planner.contexts[0].idempotency_key == "run-1-planning-initial-g0"  # type: ignore[attr-defined]
    assert made.planner.contexts[0].reconstruction_generation == 0  # type: ignore[attr-defined]


async def test_l2_cache_hits_never_enter_checkpoint_reservation_ids() -> None:
    item = evidence()
    writer = StatefulWriter()
    semantic = Semantic([True], cache_hit=True)
    made = context(deterministic=lambda *_: passed(item), semantic=semantic)
    made = dataclass_replace(
        made,
        planner=Planner(cache_hit=True),
        compliance_agent=Compliance(candidate(item), cache_hit=True),
        checkpoint_factory=checkpoint,
        checkpoint_writer=writer,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.results[0].verification_status.value == "verified"
    assert outcome.reservation_ids == ()
    assert writer.current is not None
    assert writer.current.reservation_ids == ()


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


async def test_l2_is_one_run_scoped_recovery_across_three_candidates() -> None:
    item = evidence()
    candidates = tuple(
        IdentifiedCandidate(
            claim_id=hashlib.sha256(f"claim {index}".encode()).hexdigest(),
            candidate=ComplianceCandidate(
                claim=f"claim {index}",
                proposed_verdict="compliant",
                evidence_ids=(item.excerpt.content_hash,),
                rationale="fixture",
            ),
        )
        for index in range(3)
    )
    made = context(
        deterministic=lambda *_: passed(item),
        semantic=Semantic([False, False, True, True]),
    )

    # Exact shaped batch fake makes the three candidates visible to the same
    # run state rather than resetting recovery in a per-claim helper.
    class BatchCompliance(Compliance):
        async def evaluate(self, *args: object) -> ComplianceOutcome:
            base = await super().evaluate(*args)
            return ComplianceOutcome(
                batch=ComplianceBatch(
                    candidates=tuple(item.candidate for item in candidates)
                ),
                candidates=candidates,
                reservation_id=base.reservation_id,
                replayed=False,
                request_size=base.request_size,
            )

    recovery_calls = 0

    async def recover(*args: object) -> RecoveryOutcome:
        nonlocal recovery_calls
        recovery_calls += 1
        return RecoveryOutcome((item,), (), 1)

    made = dataclass_replace(
        made,
        compliance_agent=BatchCompliance(candidates[0]),
        recovery_runner=recover,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert len(outcome.results) == 3
    assert recovery_calls == 1
    assert outcome.results[0].verification_status.value == "semantic_failed"
    assert all(
        result.verification_status.value == "verified" for result in outcome.results[1:]
    )


async def test_eight_call_budget_prevents_recovery_send() -> None:
    item = evidence()
    made = context(
        deterministic=lambda *_: DeterministicResult(
            checks=(
                DeterministicCheck(
                    item.excerpt.content_hash, DeterministicFault.NOT_DISCLOSED
                ),
            ),
            citations=(),
        ),
        semantic=Semantic([True]),
    )
    made.evidence_agent.calls = 0

    async def exhausted(
        self: object, plan: object, corpus_manifest_id: str, **kwargs: object
    ) -> EvidenceResult:
        return EvidenceResult((item,), (), 8)

    made = dataclass_replace(
        made, evidence_agent=type("E", (), {"collect": exhausted})()
    )
    recovery_calls = 0

    async def recover(*args: object) -> RecoveryOutcome:
        nonlocal recovery_calls
        recovery_calls += 1
        return RecoveryOutcome((item,), (), 9)

    made = dataclass_replace(made, recovery_runner=recover)
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert recovery_calls == 0
    assert outcome.tool_attempts_used == 8
    assert outcome.results[0].verification_status.value == "deterministic_failed"


async def test_first_checkpoint_and_every_followup_obey_real_cas_transition() -> None:
    item = evidence()
    writer = StatefulWriter()
    seed = checkpoint()
    made = context(deterministic=lambda *_: passed(item), semantic=Semantic([True]))
    made = dataclass_replace(
        made,
        checkpoint_factory=lambda: seed,
        checkpoint_writer=writer,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.results[0].verification_status.value == "verified"
    assert writer.current is not None
    assert writer.current.stage.value == "completed"
    assert writer.current.checkpoint_version == writer.writes


async def test_lease_loss_after_checkpoint_fences_compliance_send() -> None:
    item = evidence()
    writer = StatefulWriter()
    live = True

    async def stop_after_first(previous_version: int | None, current: object):
        nonlocal live
        result = await writer(previous_version, current)
        live = False
        return result

    made = context(deterministic=lambda *_: passed(item), semantic=Semantic([True]))
    made = dataclass_replace(
        made,
        checkpoint_factory=lambda: checkpoint(),
        checkpoint_writer=stop_after_first,
        lease_is_live=lambda: live,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault == "lease_lost"
    assert made.compliance_agent.calls == 0


@pytest.mark.parametrize(
    "stage",
    [
        "evidence_collected",
        "candidate_built",
        "deterministic_verified",
        "recovery_completed",
        "semantic_verified",
    ],
)
async def test_resume_each_legal_stage_uses_only_validated_local_restorers(
    stage: str,
) -> None:
    item = evidence()
    seed = checkpoint(stage, items=(item,))
    if stage == "recovery_completed":
        seed = seed.model_copy(update={"recovery_attempted": True})
    writer = StatefulWriter(current=seed, writes=seed.checkpoint_version)
    made = context(deterministic=lambda *_: passed(item), semantic=Semantic([True]))
    made = dataclass_replace(
        made,
        checkpoint=seed,
        checkpoint_writer=writer,
        evidence_restorer=lambda refs: (item,),
        plan_restorer=lambda plan_id, plan_hash: FakePlan(),
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault not in {
        "checkpoint_plan_unavailable",
        "checkpoint_evidence_unavailable",
    }
    assert made.planner.calls == 0


async def test_resume_after_evidence_does_not_require_durable_plan_prose() -> None:
    item = evidence()
    seed = checkpoint("evidence_collected", items=(item,))
    writer = StatefulWriter(current=seed, writes=seed.checkpoint_version)
    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=Semantic([True])),
        checkpoint=seed,
        checkpoint_writer=writer,
        evidence_restorer=lambda refs: (item,),
        plan_restorer=None,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault != "checkpoint_plan_unavailable"
    assert outcome.results[0].verification_status.value == "verified"
    assert made.planner.calls == 0


async def test_reserved_recovery_resume_does_not_repeat_tool_call() -> None:
    item = evidence()
    seed = checkpoint("recovery_reserved", items=(item,)).model_copy(
        update={
            "tool_attempts_used": 8,
            "recovery_attempted": True,
            "recovery_reason": "exception_missing",
            "recovery_claim_id": candidate(item).claim_id,
        }
    )
    recovery_calls = 0

    async def forbidden_recovery(*args: object) -> RecoveryOutcome:
        nonlocal recovery_calls
        recovery_calls += 1
        return RecoveryOutcome((item,), (), 8)

    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=Semantic([True])),
        checkpoint=seed,
        checkpoint_writer=StatefulWriter(current=seed, writes=1),
        evidence_restorer=lambda refs: (item,),
        recovery_runner=forbidden_recovery,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert recovery_calls == 0
    assert outcome.tool_attempts_used == 8
    assert outcome.recovery_attempted is True
    assert outcome.results[0].verification_status.value == "insufficient"
    assert outcome.results[0].reason_code == "recovery_result_lost"


async def test_recovery_reserved_resume_closes_bound_claim_before_reordered_peers(
) -> None:
    item = evidence()
    claim_a = candidate(item, "Claim A")
    claim_b = candidate(item, "Claim B")
    claim_c = candidate(item, "Claim C")
    completed_a = ComplianceResult(
        claim_id=claim_a.claim_id,
        verdict="compliant",
        verification_status="verified",
        citations=passed(item).citations,
    )
    seed = checkpoint("recovery_reserved", items=(item,)).model_copy(
        update={
            "tool_attempts_used": 2,
            "recovery_attempted": True,
            "recovery_reason": "exception_missing",
            "recovery_claim_id": claim_b.claim_id,
            "candidate_count": 3,
            "completed_claim_ids": (claim_a.claim_id,),
            "completed_results": (completed_a,),
        }
    )
    semantic = Semantic([True])
    recovery_calls = 0

    async def forbidden_recovery(*args: object) -> RecoveryOutcome:
        nonlocal recovery_calls
        recovery_calls += 1
        return RecoveryOutcome((item,), (), 2)

    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=semantic),
        checkpoint=seed,
        checkpoint_writer=StatefulWriter(current=seed, writes=1),
        evidence_restorer=lambda refs: (item,),
        compliance_agent=BatchCompliance((claim_a, claim_c, claim_b)),
        recovery_runner=forbidden_recovery,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert recovery_calls == 0
    assert outcome.results[0] == completed_a
    assert outcome.results[1].claim_id == claim_b.claim_id
    assert outcome.results[1].reason_code == "recovery_result_lost"
    assert semantic.claim_ids == [claim_c.claim_id]


async def test_recovery_reserved_resume_rejects_missing_bound_claim_before_more_sends(
) -> None:
    item = evidence()
    claim_a = candidate(item, "Claim A")
    claim_b = candidate(item, "Claim B")
    claim_c = candidate(item, "Claim C")
    claim_d = candidate(item, "Claim D")
    completed_a = ComplianceResult(
        claim_id=claim_a.claim_id,
        verdict="compliant",
        verification_status="verified",
        citations=passed(item).citations,
    )
    seed = checkpoint("recovery_reserved", items=(item,)).model_copy(
        update={
            "tool_attempts_used": 2,
            "recovery_attempted": True,
            "recovery_reason": "exception_missing",
            "recovery_claim_id": claim_b.claim_id,
            "candidate_count": 3,
            "completed_claim_ids": (claim_a.claim_id,),
            "completed_results": (completed_a,),
        }
    )
    semantic = Semantic([True])
    recovery_calls = 0

    async def forbidden_recovery(*args: object) -> RecoveryOutcome:
        nonlocal recovery_calls
        recovery_calls += 1
        return RecoveryOutcome((item,), (), 2)

    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=semantic),
        checkpoint=seed,
        checkpoint_writer=StatefulWriter(current=seed, writes=1),
        evidence_restorer=lambda refs: (item,),
        compliance_agent=BatchCompliance((claim_a, claim_c, claim_d)),
        recovery_runner=forbidden_recovery,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault == "checkpoint_recovery_claim_integrity"
    assert semantic.calls == 0
    assert recovery_calls == 0


async def test_completed_checkpoint_fast_path_needs_no_restorer_or_provider() -> None:
    item = evidence()
    result = ComplianceResult(
        claim_id=candidate(item).claim_id,
        verdict="compliant",
        verification_status="verified",
        citations=passed(item).citations,
    )
    seed = checkpoint("completed").model_copy(
        update={
            "candidate_count": 1,
            "completed_claim_ids": (result.claim_id,),
            "completed_results": (result,),
        }
    )
    made = context(deterministic=lambda *_: passed(item), semantic=Semantic([True]))
    made = dataclass_replace(made, checkpoint=seed)
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.results == (result,)
    assert made.planner.calls == 0
    assert made.evidence_agent.calls == 0
    assert made.compliance_agent.calls == 0


async def test_mismatched_restored_evidence_fails_closed_before_compliance() -> None:
    item = evidence()
    seed = checkpoint("candidate_built", items=(item,))
    wrong = build_evidence_from_unit(
        IndexUnit(
            unit_id="b" * 64, kind="clause", document_id="RFC9110",
            document_version="RFC9110-2022", section_number="1", section_path="x",
            ordinal=2, text="Different local clause.", indexed="Different",
        ), corpus_manifest_id="c" * 64,
    )
    made = context(deterministic=lambda *_: passed(item), semantic=Semantic([True]))
    made = dataclass_replace(
        made,
        checkpoint=seed,
        plan_restorer=lambda *_: FakePlan(),
        evidence_restorer=lambda refs: (wrong,),
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault == "checkpoint_evidence_unavailable"
    assert made.compliance_agent.calls == 0


@pytest.mark.parametrize("restored", [(), (evidence(), evidence())])
async def test_evidence_restorer_rejects_missing_or_extra_refs_before_send(
    restored: tuple[Evidence, ...],
) -> None:
    item = evidence()
    seed = checkpoint("candidate_built", items=(item,))
    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=Semantic([True])),
        checkpoint=seed,
        plan_restorer=lambda *_: FakePlan(),
        evidence_restorer=lambda _: restored,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault == "checkpoint_evidence_unavailable"
    assert made.compliance_agent.calls == 0


async def test_semantic_cursor_survives_crash_between_three_claims() -> None:
    item = evidence()
    candidates = tuple(
        IdentifiedCandidate(
            claim_id=hashlib.sha256(f"cursor {index}".encode()).hexdigest(),
            candidate=ComplianceCandidate(
                claim=f"cursor {index}", proposed_verdict="compliant",
                evidence_ids=(item.excerpt.content_hash,), rationale="fixture",
            ),
        )
        for index in range(3)
    )

    class BatchCompliance(Compliance):
        async def evaluate(self, *args: object) -> ComplianceOutcome:
            base = await super().evaluate(*args)
            return ComplianceOutcome(
                batch=ComplianceBatch(
                    candidates=tuple(value.candidate for value in candidates)
                ),
                candidates=candidates, reservation_id=base.reservation_id,
                replayed=False, request_size=base.request_size,
            )

    writer = StatefulWriter()
    live = True

    async def stop_after_cursor(previous_version: int | None, current: object):
        nonlocal live
        saved = await writer(previous_version, current)
        if (
            saved.stage.value == "semantic_verified"
            and len(saved.completed_results) == 1
        ):
            live = False
        return saved

    first = dataclass_replace(
        context(
            deterministic=lambda *_: passed(item), semantic=Semantic([True, True, True])
        ),
        compliance_agent=BatchCompliance(candidates[0]), checkpoint_factory=checkpoint,
        checkpoint_writer=stop_after_cursor, lease_is_live=lambda: live,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    interrupted = await run_l2_attempt(first)

    assert interrupted.parse_fault == "lease_lost"
    assert writer.current is not None
    assert writer.current.completed_claim_ids == (candidates[0].claim_id,)
    resumed = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=Semantic([True, True])),
        checkpoint=writer.current, checkpoint_writer=writer,
        compliance_agent=BatchCompliance(candidates[0]),
        plan_restorer=lambda *_: FakePlan(), evidence_restorer=lambda _: (item,),
    )
    outcome = await run_l2_attempt(resumed)

    assert tuple(value.claim_id for value in outcome.results) == tuple(
        value.claim_id for value in candidates
    )
    assert resumed.semantic_verifier.calls == 2
    assert writer.current.completed_claim_ids == tuple(
        value.claim_id for value in candidates
    )


async def test_provider_failure_reservation_is_durable_before_return() -> None:
    item = evidence()
    writer = StatefulWriter()

    class FailingCompliance:
        async def evaluate(self, *args: object) -> ComplianceOutcome:
            raise ProviderAttemptError("provider_timeout", str(uuid4()), False, None)

    seed = checkpoint("evidence_collected", items=(item,))
    writer.current = seed
    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=Semantic([True])),
        checkpoint=seed, checkpoint_writer=writer, compliance_agent=FailingCompliance(),
        plan_restorer=lambda *_: FakePlan(), evidence_restorer=lambda _: (item,),
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.provider_error == "provider_timeout"
    assert writer.current is not None
    assert len(writer.current.reservation_ids) == 1


async def test_first_semantic_receipt_is_cas_written_before_recovery_await() -> None:
    item = evidence()
    writer = StatefulWriter()
    observed: list[object] = []

    async def recover(*args: object) -> RecoveryOutcome:
        assert writer.current is not None
        observed.append(writer.current)
        assert len(writer.current.reservation_ids) >= 2  # compliance + semantic
        return RecoveryOutcome((item,), (), 1)

    made = dataclass_replace(
        context(
            deterministic=lambda *_: passed(item), semantic=Semantic([False, True])
        ),
        checkpoint_factory=checkpoint,
        checkpoint_writer=writer,
        recovery_runner=recover,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert observed
    assert outcome.recovery_attempted
    assert len(outcome.reservation_ids) >= 3


async def test_semantic_receipt_seals_before_post_response_lease_exit() -> None:
    item = evidence()
    writer = StatefulWriter()
    live = True

    class LeaseDroppingSemantic(Semantic):
        async def verify(self, *args: object) -> SemanticOutcome:
            nonlocal live
            result = await super().verify(*args)
            live = False
            return result

    semantic = LeaseDroppingSemantic([False])
    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=semantic),
        checkpoint_factory=checkpoint,
        checkpoint_writer=writer,
        lease_is_live=lambda: live,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    outcome = await run_l2_attempt(made)

    assert outcome.parse_fault == "lease_lost"
    assert writer.current is not None
    # Planning, Compliance, then the sealed Semantic receipt.
    assert len(writer.current.reservation_ids) == 3
    assert semantic.calls == 1


async def test_audit_write_failure_fails_closed_before_the_next_stage() -> None:
    item = evidence()
    writer = StatefulWriter()

    async def fail_audit(event: object) -> None:
        raise RuntimeError("sanitized_audit_unavailable")

    made = dataclass_replace(
        context(deterministic=lambda *_: passed(item), semantic=Semantic([True])),
        checkpoint_factory=checkpoint,
        checkpoint_writer=writer,
        audit_sink=fail_audit,
    )
    from specpilot.runtime.l2 import run_l2_attempt

    with pytest.raises(RuntimeError, match="sanitized_audit_unavailable"):
        await run_l2_attempt(made)

    assert made.planner.calls == 1
    assert made.evidence_agent.calls == 0
    assert made.compliance_agent.calls == 0


def dataclass_replace(value: object, **changes: object):
    from dataclasses import replace

    return replace(value, **changes)
