"""Closed, bounded L2 orchestration with prose-free checkpoint state.

The orchestrator is intentionally a small state machine rather than a generic
workflow framework.  It can reconstruct only local evidence from a checkpoint;
all lost provider prose is deliberately sent again under an explicitly newer
generation and is charged by the existing transport/ledger.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol
from uuid import UUID

from specpilot.agents.compliance import (
    ComplianceContext,
    ComplianceOutcome,
    InvalidComplianceReply,
)
from specpilot.agents.contracts import ToolPlan
from specpilot.agents.evidence import EvidenceResult
from specpilot.agents.planner import PlannerContext, PlannerResult
from specpilot.answer.evidence import Evidence
from specpilot.checkpoints.contracts import (
    CheckpointStage,
    EvidenceCheckpointRef,
    RunCheckpoint,
    StageGeneration,
)
from specpilot.contracts.verdict import (
    ComplianceCandidate,
    ComplianceResult,
    ComplianceVerdict,
    IdentifiedCandidate,
    VerificationStatus,
)
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.providers.transport import ProviderAttemptError
from specpilot.verifier.deterministic import DeterministicResult
from specpilot.verifier.recovery import RecoveryOutcome
from specpilot.verifier.semantic import (
    InvalidSemanticReply,
    SemanticContext,
    SemanticOutcome,
)

_L2_TOOL_BUDGET = 8
_MAX_EVIDENCE = 12
_MAX_CANDIDATE_EVIDENCE = 4


class Planner(Protocol):
    async def plan(self, question: str, context: PlannerContext) -> PlannerResult: ...


class EvidenceAgent(Protocol):
    async def collect(
        self,
        plan: ToolPlan,
        corpus_manifest_id: str,
        *,
        attempt_budget: int = 8,
        attempts_used: int = 0,
    ) -> EvidenceResult: ...


class ComplianceAgent(Protocol):
    async def evaluate(
        self,
        description: str,
        evidence: tuple[Evidence, ...],
        context: ComplianceContext,
    ) -> ComplianceOutcome: ...


class SemanticVerifier(Protocol):
    async def verify(
        self,
        candidate: IdentifiedCandidate,
        evidence: tuple[Evidence, ...],
        deterministic: DeterministicResult,
        context: SemanticContext,
    ) -> SemanticOutcome: ...


type DeterministicVerifier = Callable[
    [ComplianceCandidate, tuple[Evidence, ...]], DeterministicResult
]
type RecoveryRunner = Callable[
    [IdentifiedCandidate, tuple[Evidence, ...], tuple[str, ...], int],
    Awaitable[RecoveryOutcome],
]
type CheckpointWriter = Callable[[RunCheckpoint], Awaitable[RunCheckpoint]]
type EvidenceRestorer = Callable[
    [tuple[EvidenceCheckpointRef, ...]], tuple[Evidence, ...]
]
type LeaseIsLive = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class L2RunContext:
    run_id: str
    question: str
    planner: Planner
    planner_context: PlannerContext
    evidence_agent: EvidenceAgent
    compliance_agent: ComplianceAgent
    compliance_context: ComplianceContext
    semantic_verifier: SemanticVerifier
    semantic_context: SemanticContext
    deterministic_verifier: DeterministicVerifier
    recovery_runner: RecoveryRunner
    checkpoint: RunCheckpoint | None = None
    checkpoint_factory: Callable[[], RunCheckpoint] | None = None
    checkpoint_writer: CheckpointWriter | None = None
    evidence_restorer: EvidenceRestorer | None = None
    lease_is_live: LeaseIsLive = lambda: True


@dataclass(frozen=True, slots=True)
class L2Outcome:
    results: tuple[ComplianceResult, ...]
    reservation_ids: tuple[str, ...]
    tool_attempts_used: int
    recovery_attempted: bool
    provider_error: str | None
    parse_fault: str | None
    egress_error: str | None = None
    compliance: ComplianceOutcome | None = None
    semantic_outcomes: tuple[tuple[str, SemanticOutcome], ...] = ()
    recovery_outcomes: tuple[RecoveryOutcome, ...] = ()
    evidence_calls: tuple[object, ...] = ()
    checkpoints: tuple[RunCheckpoint, ...] = ()


def logical_stage_key(
    run_id: str,
    stage: str,
    claim_id: str | None,
    recovery: bool,
    reconstruction_generation: int,
) -> str:
    if claim_id is None:
        return f"{run_id}-{stage}-initial-g{reconstruction_generation}"
    branch = "recovery" if recovery else "initial"
    return f"{run_id}-{stage}-{claim_id}-{branch}-g{reconstruction_generation}"


async def run_l2_attempt(context: L2RunContext) -> L2Outcome:
    """Execute exactly the missing portion of the closed checkpoint graph."""
    checkpoint = context.checkpoint
    if checkpoint is None and context.checkpoint_factory is not None:
        checkpoint = context.checkpoint_factory()
    reservations = [
        str(item) for item in (checkpoint.reservation_ids if checkpoint else ())
    ]
    attempts = checkpoint.tool_attempts_used if checkpoint else 0
    recovered = bool(checkpoint and checkpoint.recovery_attempted)
    written: list[RunCheckpoint] = []
    semantic_outcomes: list[tuple[str, SemanticOutcome]] = []
    recovery_outcomes: list[RecoveryOutcome] = []
    evidence_calls: tuple[object, ...] = ()
    evidence = _restore(context, checkpoint)

    async def write(stage: CheckpointStage, **updates: Any) -> None:
        nonlocal checkpoint
        checkpoint = await _advance(context, checkpoint, stage, **updates)
        if checkpoint is not None:
            written.append(checkpoint)

    try:
        # No checkpoint means no state was durably accepted yet. Planning is
        # the first external operation and must be followed by PLANNED.
        if checkpoint is None:
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            planning = await context.planner.plan(
                context.question,
                replace(context.planner_context, reconstruction_generation=0),
            )
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            reservations.append(planning.reservation_id)
            await write(
                CheckpointStage.PLANNED,
                plan_id=planning.plan.plan_id,
                plan_hash=_plan_hash(planning.plan),
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                generation=StageGeneration(
                    stage="planning", claim_id=None, recovery=False, generation=0
                ),
            )
            plan = planning.plan
        elif checkpoint.stage is CheckpointStage.PLANNED:
            # The plan's text-bearing tool arguments are deliberately absent;
            # a process loss sends planning at a newly persisted generation.
            checkpoint = await _prepare_generation(
                context, checkpoint, "planning", None, False, written
            )
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            generation = _latest_generation(checkpoint, "planning", None, False)
            planning = await context.planner.plan(
                context.question,
                replace(context.planner_context, reconstruction_generation=generation),
            )
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            reservations.append(planning.reservation_id)
            await write(
                CheckpointStage.PLANNED,
                plan_id=planning.plan.plan_id,
                plan_hash=_plan_hash(planning.plan),
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
            )
            plan = planning.plan
        else:
            # A plan is not safely reconstructible from its hash. The caller
            # must supply a resume-created context with current plan or begin
            # from PLANNED. Refuse before any new external operation.
            return _fault(
                "checkpoint_plan_unavailable",
                attempts,
                reservations,
                recovered,
                written,
            )

        if not context.lease_is_live():
            return _abandoned(attempts, reservations, recovered, written)
        collected = await context.evidence_agent.collect(
            plan,
            context.planner_context.corpus_manifest_id,
            attempt_budget=_L2_TOOL_BUDGET,
            attempts_used=attempts,
        )
        if not context.lease_is_live():
            return _abandoned(attempts, reservations, recovered, written)
        attempts = collected.attempts_used
        evidence = _bounded_evidence(collected.evidence)
        evidence_calls = tuple(collected.calls)
        await write(
            CheckpointStage.EVIDENCE_COLLECTED,
            evidence=evidence,
            reservation_ids=reservations,
            attempts=attempts,
            recovered=recovered,
        )

        checkpoint = await _prepare_generation(
            context, checkpoint, "compliance", None, False, written
        )
        if not context.lease_is_live():
            return _abandoned(attempts, reservations, recovered, written)
        generation = _latest_generation(checkpoint, "compliance", None, False)
        compliance = await context.compliance_agent.evaluate(
            context.question,
            evidence,
            replace(
                context.compliance_context,
                idempotency_key="initial",
                reconstruction_generation=generation,
            ),
        )
        if not context.lease_is_live():
            return _abandoned(attempts, reservations, recovered, written)
        reservations.append(compliance.reservation_id)
        await write(
            CheckpointStage.CANDIDATE_BUILT,
            reservation_ids=reservations,
            attempts=attempts,
            recovered=recovered,
        )

        results: list[ComplianceResult] = []
        for candidate in compliance.candidates:
            (
                result,
                evidence,
                attempts,
                recovered,
                semantic,
                recovery,
            ) = await _one_claim(
                context, checkpoint, candidate, evidence, attempts, recovered, written
            )
            if semantic is not None:
                reservations.append(semantic.reservation_id)
                semantic_outcomes.append((candidate.claim_id, semantic))
            if recovery is not None:
                recovery_outcomes.append(recovery)
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            results.append(result)

        if checkpoint is not None:
            await write(
                CheckpointStage.COMPLETED,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                completed_results=tuple(results),
            )
        return L2Outcome(
            tuple(results),
            tuple(reservations),
            attempts,
            recovered,
            None,
            None,
            compliance=compliance,
            semantic_outcomes=tuple(semantic_outcomes),
            recovery_outcomes=tuple(recovery_outcomes),
            evidence_calls=evidence_calls,
            checkpoints=tuple(written),
        )
    except EgressPolicyViolation as error:
        return _fault(
            error.code, attempts, reservations, recovered, written, egress=True
        )
    except ProviderAttemptError as error:
        return L2Outcome(
            (),
            tuple(reservations),
            attempts,
            recovered,
            error.public_error_code,
            None,
            checkpoints=tuple(written),
        )
    except InvalidComplianceReply as error:
        reservations.append(error.reservation_id)
        return _fault(
            "invalid_compliance_reply", attempts, reservations, recovered, written
        )
    except InvalidSemanticReply as error:
        reservations.append(error.reservation_id)
        return _fault(
            "invalid_semantic_reply", attempts, reservations, recovered, written
        )


async def _one_claim(
    context: L2RunContext,
    checkpoint: RunCheckpoint | None,
    candidate: IdentifiedCandidate,
    evidence: tuple[Evidence, ...],
    attempts: int,
    recovered: bool,
    written: list[RunCheckpoint],
) -> tuple[
    ComplianceResult,
    tuple[Evidence, ...],
    int,
    bool,
    SemanticOutcome | None,
    RecoveryOutcome | None,
]:
    if candidate.candidate.proposed_verdict is ComplianceVerdict.INSUFFICIENT_EVIDENCE:
        return (
            _insufficient(
                candidate.claim_id,
                VerificationStatus.INSUFFICIENT,
                "proposed_insufficient",
            ),
            evidence,
            attempts,
            recovered,
            None,
            None,
        )
    deterministic = context.deterministic_verifier(candidate.candidate, evidence)
    if not deterministic.passed:
        replacement = await _recover(
            context, candidate, evidence, deterministic, attempts, recovered
        )
        if replacement is None:
            return (
                _insufficient(
                    candidate.claim_id,
                    VerificationStatus.DETERMINISTIC_FAILED,
                    _reason(deterministic),
                ),
                evidence,
                attempts,
                recovered,
                None,
                None,
            )
        evidence, attempts, recovery = replacement
        recovered = True
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                CheckpointStage.RECOVERY_COMPLETED,
                evidence=evidence,
                attempts=attempts,
                recovered=True,
            )
            if checkpoint is not None:
                written.append(checkpoint)
        deterministic = context.deterministic_verifier(
            _rebuilt(candidate, evidence).candidate, evidence
        )
        if not deterministic.passed:
            return (
                _insufficient(
                    candidate.claim_id,
                    VerificationStatus.DETERMINISTIC_FAILED,
                    _reason(deterministic),
                ),
                evidence,
                attempts,
                recovered,
                None,
                recovery,
            )
    else:
        recovery = None
    if checkpoint is not None:
        checkpoint = await _advance(
            context,
            checkpoint,
            CheckpointStage.DETERMINISTIC_VERIFIED,
            evidence=evidence,
            attempts=attempts,
            recovered=recovered,
        )
        if checkpoint is not None:
            written.append(checkpoint)
    active = _rebuilt(candidate, evidence) if recovered else candidate
    checkpoint = await _prepare_generation(
        context, checkpoint, "verifier", active.claim_id, recovered, written
    )
    if not context.lease_is_live():
        return (
            _insufficient(
                active.claim_id, VerificationStatus.INSUFFICIENT, "lease_lost"
            ),
            evidence,
            attempts,
            recovered,
            None,
            recovery,
        )
    generation = _latest_generation(checkpoint, "verifier", active.claim_id, recovered)
    semantic = await context.semantic_verifier.verify(
        active,
        evidence,
        deterministic,
        replace(
            context.semantic_context,
            idempotency_key=(
                f"{active.claim_id}-{'recovery' if recovered else 'initial'}"
            ),
            reconstruction_generation=generation,
        ),
    )
    if not context.lease_is_live():
        return (
            _insufficient(
                active.claim_id, VerificationStatus.INSUFFICIENT, "lease_lost"
            ),
            evidence,
            attempts,
            recovered,
            None,
            recovery,
        )
    if semantic.decision.supports_verdict:
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                CheckpointStage.SEMANTIC_VERIFIED,
                evidence=evidence,
                attempts=attempts,
                recovered=recovered,
            )
            if checkpoint is not None:
                written.append(checkpoint)
        return (
            _verified(active, deterministic),
            evidence,
            attempts,
            recovered,
            semantic,
            recovery,
        )
    replacement = await _recover(
        context,
        active,
        evidence,
        deterministic,
        attempts,
        recovered,
        semantic_reason=semantic.decision.reason.value,
    )
    if replacement is None:
        return (
            _insufficient(
                active.claim_id,
                VerificationStatus.SEMANTIC_FAILED,
                semantic.decision.reason.value,
            ),
            evidence,
            attempts,
            recovered,
            semantic,
            recovery,
        )
    evidence, attempts, recovery = replacement
    recovered = True
    if checkpoint is not None:
        checkpoint = await _advance(
            context,
            checkpoint,
            CheckpointStage.RECOVERY_COMPLETED,
            evidence=evidence,
            attempts=attempts,
            recovered=True,
        )
        if checkpoint is not None:
            written.append(checkpoint)
    active = _rebuilt(active, evidence)
    deterministic = context.deterministic_verifier(active.candidate, evidence)
    if not deterministic.passed:
        return (
            _insufficient(
                active.claim_id,
                VerificationStatus.DETERMINISTIC_FAILED,
                _reason(deterministic),
            ),
            evidence,
            attempts,
            recovered,
            semantic,
            recovery,
        )
    if checkpoint is not None:
        checkpoint = await _advance(
            context,
            checkpoint,
            CheckpointStage.DETERMINISTIC_VERIFIED,
            evidence=evidence,
            attempts=attempts,
            recovered=True,
        )
        if checkpoint is not None:
            written.append(checkpoint)
    checkpoint = await _prepare_generation(
        context, checkpoint, "verifier", active.claim_id, True, written
    )
    if not context.lease_is_live():
        return (
            _insufficient(
                active.claim_id, VerificationStatus.INSUFFICIENT, "lease_lost"
            ),
            evidence,
            attempts,
            recovered,
            semantic,
            recovery,
        )
    semantic = await context.semantic_verifier.verify(
        active,
        evidence,
        deterministic,
        replace(
            context.semantic_context,
            idempotency_key=f"{active.claim_id}-recovery",
            reconstruction_generation=_latest_generation(
                checkpoint, "verifier", active.claim_id, True
            ),
        ),
    )
    if semantic.decision.supports_verdict:
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                CheckpointStage.SEMANTIC_VERIFIED,
                evidence=evidence,
                attempts=attempts,
                recovered=True,
            )
            if checkpoint is not None:
                written.append(checkpoint)
        return (
            _verified(active, deterministic),
            evidence,
            attempts,
            recovered,
            semantic,
            recovery,
        )
    return (
        _insufficient(
            active.claim_id,
            VerificationStatus.SEMANTIC_FAILED,
            semantic.decision.reason.value,
        ),
        evidence,
        attempts,
        recovered,
        semantic,
        recovery,
    )


async def _recover(
    context: L2RunContext,
    candidate: IdentifiedCandidate,
    evidence: tuple[Evidence, ...],
    deterministic: DeterministicResult,
    attempts: int,
    recovered: bool,
    *,
    semantic_reason: str | None = None,
) -> tuple[tuple[Evidence, ...], int, RecoveryOutcome] | None:
    if recovered or attempts >= _L2_TOOL_BUDGET or not context.lease_is_live():
        return None
    reasons = (
        (semantic_reason,)
        if semantic_reason is not None
        else tuple(
            check.fault.value
            for check in deterministic.checks
            if check.fault is not None
        )
    )
    outcome = await context.recovery_runner(candidate, evidence, reasons, attempts)
    if not context.lease_is_live() or outcome.attempts_used > _L2_TOOL_BUDGET:
        return None
    return _bounded_evidence(outcome.evidence), outcome.attempts_used, outcome


async def _prepare_generation(
    context: L2RunContext,
    checkpoint: RunCheckpoint | None,
    stage: Literal["planning", "compliance", "verifier"],
    claim_id: str | None,
    recovery: bool,
    written: list[RunCheckpoint],
) -> RunCheckpoint | None:
    if checkpoint is None:
        return None
    # Before *every* send record the generation token. New runs record g0;
    # an interrupted checkpoint advances to g1 before the reconstructed send.
    existing = [
        item
        for item in checkpoint.reconstruction_generations
        if item.stage == stage
        and item.claim_id == claim_id
        and item.recovery == recovery
    ]
    generation = 0 if not existing else max(item.generation for item in existing) + 1
    next_checkpoint = checkpoint.model_copy(
        update={
            "checkpoint_version": checkpoint.checkpoint_version + 1,
            "reconstruction_generations": checkpoint.reconstruction_generations
            + (
                StageGeneration(
                    stage=stage,
                    claim_id=claim_id,
                    recovery=recovery,
                    generation=generation,
                ),
            ),
        }
    )
    if context.checkpoint_writer is not None:
        next_checkpoint = await context.checkpoint_writer(next_checkpoint)
    written.append(next_checkpoint)
    return next_checkpoint


async def _advance(
    context: L2RunContext,
    previous: RunCheckpoint | None,
    stage: CheckpointStage,
    *,
    evidence: tuple[Evidence, ...] = (),
    reservation_ids: list[str] | None = None,
    attempts: int = 0,
    recovered: bool = False,
    plan_id: str | None = None,
    plan_hash: str | None = None,
    generation: StageGeneration | None = None,
    completed_results: tuple[ComplianceResult, ...] = (),
    allow_same_stage: bool = False,
) -> RunCheckpoint | None:
    if previous is None:
        return None
    if stage is previous.stage and not allow_same_stage:
        return previous
    refs = _refs(evidence) or previous.evidence
    generations = previous.reconstruction_generations
    if generation is not None and generation not in generations:
        generations += (generation,)
    next_checkpoint = previous.model_copy(
        update={
            "checkpoint_version": previous.checkpoint_version + 1,
            "stage": stage,
            "plan_id": plan_id if plan_id is not None else previous.plan_id,
            "plan_hash": plan_hash if plan_hash is not None else previous.plan_hash,
            "evidence": refs,
            "reservation_ids": tuple(
                UUID(value)
                for value in (
                    reservation_ids or [str(item) for item in previous.reservation_ids]
                )
            ),
            "tool_attempts_used": attempts,
            "recovery_attempted": recovered,
            "completed_claim_ids": tuple(item.claim_id for item in completed_results)
            or previous.completed_claim_ids,
            "completed_results": completed_results or previous.completed_results,
        }
    )
    if context.checkpoint_writer is not None:
        return await context.checkpoint_writer(next_checkpoint)
    return next_checkpoint


def _restore(
    context: L2RunContext, checkpoint: RunCheckpoint | None
) -> tuple[Evidence, ...]:
    if checkpoint is None or context.evidence_restorer is None:
        return ()
    return _bounded_evidence(context.evidence_restorer(checkpoint.evidence))


def _refs(evidence: tuple[Evidence, ...]) -> tuple[EvidenceCheckpointRef, ...]:
    return tuple(
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
        for item in evidence
    )


def _bounded_evidence(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    unique: dict[str, Evidence] = {}
    for item in evidence:
        unique.setdefault(item.excerpt.content_hash, item)
        if len(unique) == _MAX_EVIDENCE:
            break
    return tuple(unique.values())


def _rebuilt(
    candidate: IdentifiedCandidate, evidence: tuple[Evidence, ...]
) -> IdentifiedCandidate:
    return IdentifiedCandidate(
        claim_id=candidate.claim_id,
        candidate=ComplianceCandidate(
            claim=candidate.candidate.claim,
            proposed_verdict=candidate.candidate.proposed_verdict,
            evidence_ids=tuple(
                item.excerpt.content_hash
                for item in evidence[-_MAX_CANDIDATE_EVIDENCE:]
            ),
            rationale=candidate.candidate.rationale,
        ),
    )


def _verified(
    candidate: IdentifiedCandidate, deterministic: DeterministicResult
) -> ComplianceResult:
    return ComplianceResult(
        claim_id=candidate.claim_id,
        verdict=candidate.candidate.proposed_verdict,
        verification_status=VerificationStatus.VERIFIED,
        citations=deterministic.citations,
    )


def _reason(result: DeterministicResult) -> str:
    return next(
        (check.fault.value for check in result.checks if check.fault is not None),
        "no_verified_evidence",
    )


def _insufficient(
    claim_id: str, status: VerificationStatus, reason: str
) -> ComplianceResult:
    return ComplianceResult(
        claim_id=claim_id,
        verdict=ComplianceVerdict.INSUFFICIENT_EVIDENCE,
        verification_status=status,
        reason_code=reason,
    )


def _latest_generation(
    checkpoint: RunCheckpoint | None, stage: str, claim_id: str | None, recovery: bool
) -> int:
    if checkpoint is None:
        return 0
    values = [
        item.generation
        for item in checkpoint.reconstruction_generations
        if item.stage == stage
        and item.claim_id == claim_id
        and item.recovery == recovery
    ]
    return max(values) if values else 0


def _plan_hash(plan: ToolPlan) -> str:
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()


def _abandoned(
    attempts: int,
    reservations: list[str],
    recovered: bool,
    checkpoints: list[RunCheckpoint],
) -> L2Outcome:
    return L2Outcome(
        (),
        tuple(reservations),
        attempts,
        recovered,
        None,
        "lease_lost",
        checkpoints=tuple(checkpoints),
    )


def _fault(
    reason: str,
    attempts: int,
    reservations: list[str],
    recovered: bool,
    checkpoints: list[RunCheckpoint],
    *,
    egress: bool = False,
) -> L2Outcome:
    return L2Outcome(
        (),
        tuple(reservations),
        attempts,
        recovered,
        None,
        None if egress else reason,
        reason if egress else None,
        checkpoints=tuple(checkpoints),
    )


__all__ = ["L2Outcome", "L2RunContext", "logical_stage_key", "run_l2_attempt"]
