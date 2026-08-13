"""One bounded L2 attempt, with no persistence or provider construction.

This module deliberately owns the *ordering* of the L2 gates but not the
database, MCP client, or provider transport.  Those capabilities are injected
as already-bound services.  Keeping the state machine here makes the two
important bounds obvious: eight retrieval attempts and one recovery for the
whole run (not one recovery per claim).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol
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
from specpilot.checkpoints.contracts import CheckpointStage, RunCheckpoint
from specpilot.contracts.verdict import (
    ComplianceCandidate,
    ComplianceResult,
    ComplianceVerdict,
    IdentifiedCandidate,
    VerificationStatus,
)
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.providers.transport import ProviderAttemptError
from specpilot.verifier.deterministic import (
    DeterministicResult,
)
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
type LeaseIsLive = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class L2RunContext:
    """All ephemeral dependencies of one L2 attempt.

    ``checkpoint`` is optional so a newly queued run can use the same pure
    state machine before Task 7 wires the PostgreSQL checkpoint store.  If a
    writer is supplied, every accepted checkpoint is passed through it before
    the next outward operation.
    """

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
    checkpoint_writer: CheckpointWriter | None = None
    lease_is_live: LeaseIsLive = lambda: True


@dataclass(frozen=True, slots=True)
class L2Outcome:
    """Prose-free terminal projection for the worker and durable trace."""

    results: tuple[ComplianceResult, ...]
    reservation_ids: tuple[str, ...]
    tool_attempts_used: int
    recovery_attempted: bool
    provider_error: str | None
    parse_fault: str | None
    egress_error: str | None = None


def logical_stage_key(
    run_id: str,
    stage: str,
    claim_id: str | None,
    recovery: bool,
    reconstruction_generation: int,
) -> str:
    """Stable logical identity; only process-loss reconstruction increments g."""
    if claim_id is None:
        return f"{run_id}-{stage}-initial-g{reconstruction_generation}"
    branch = "recovery" if recovery else "initial"
    return f"{run_id}-{stage}-{claim_id}-{branch}-g{reconstruction_generation}"


async def run_l2_attempt(context: L2RunContext) -> L2Outcome:
    """Run planner → evidence → Compliance → deterministic → semantic.

    A failure in either verifier may use exactly one injected directed recovery.
    Recovery rebuilds the transient candidate evidence handles and always
    re-enters deterministic verification before semantic verification.
    """
    checkpoint = context.checkpoint
    reservations: list[str] = list(
        str(item) for item in (checkpoint.reservation_ids if checkpoint else ())
    )
    recovery_attempted = bool(checkpoint and checkpoint.recovery_attempted)
    attempts_used = checkpoint.tool_attempts_used if checkpoint else 0
    evidence: tuple[Evidence, ...] = ()
    plan: ToolPlan | None = None

    try:
        # Evidence can be reconstructed locally only when a caller provides a
        # ready plan/evidence cache. Checkpoints intentionally contain no prose,
        # so the durable planned/evidence stages reissue their lost model stage
        # under the next reconstruction generation.
        plan_generation = _generation(checkpoint, "planning", None, False)
        planning = await context.planner.plan(
            context.question,
            replace(
                context.planner_context,
                reconstruction_generation=plan_generation,
            ),
        )
        if not context.lease_is_live():
            return _abandoned(attempts_used, reservations, recovery_attempted)
        plan = planning.plan
        reservations.append(planning.reservation_id)
        checkpoint = await _checkpoint(
            context,
            checkpoint,
            CheckpointStage.PLANNED,
            plan_id=plan.plan_id,
            plan_hash=_plan_hash(plan),
            reservation_ids=tuple(reservations),
            attempts_used=attempts_used,
            recovery_attempted=recovery_attempted,
        )
        if not context.lease_is_live():
            return _abandoned(attempts_used, reservations, recovery_attempted)

        collected = await context.evidence_agent.collect(
            plan,
            context.planner_context.corpus_manifest_id,
            attempt_budget=_L2_TOOL_BUDGET,
            attempts_used=attempts_used,
        )
        if not context.lease_is_live():
            return _abandoned(attempts_used, reservations, recovery_attempted)
        attempts_used = collected.attempts_used
        evidence = _bounded_evidence(collected.evidence)
        checkpoint = await _checkpoint(
            context,
            checkpoint,
            CheckpointStage.EVIDENCE_COLLECTED,
            evidence=evidence,
            reservation_ids=tuple(reservations),
            attempts_used=attempts_used,
            recovery_attempted=recovery_attempted,
        )
        if not context.lease_is_live():
            return _abandoned(attempts_used, reservations, recovery_attempted)

        compliance_generation = _generation(checkpoint, "compliance", None, False)
        compliance = await context.compliance_agent.evaluate(
            context.question,
            evidence,
            replace(
                context.compliance_context,
                # The stage adapter prefixes ``run_id-stage`` itself; give it
                # the logical suffix, not the full key, to avoid a doubled
                # prefix at the enforcer boundary.
                idempotency_key="initial",
                reconstruction_generation=compliance_generation,
            ),
        )
        if not context.lease_is_live():
            return _abandoned(attempts_used, reservations, recovery_attempted)
        reservations.append(compliance.reservation_id)
        checkpoint = await _checkpoint(
            context,
            checkpoint,
            CheckpointStage.CANDIDATE_BUILT,
            reservation_ids=tuple(reservations),
            attempts_used=attempts_used,
            recovery_attempted=recovery_attempted,
        )
        if not context.lease_is_live():
            return _abandoned(attempts_used, reservations, recovery_attempted)

        results: list[ComplianceResult] = []
        for candidate in compliance.candidates:
            (
                result,
                evidence,
                attempts_used,
                recovery_attempted,
                semantic_id,
            ) = await _verify_candidate(
                context,
                candidate,
                evidence,
                attempts_used,
                recovery_attempted,
            )
            if semantic_id is not None:
                reservations.append(semantic_id)
            if not context.lease_is_live():
                return _abandoned(attempts_used, reservations, recovery_attempted)
            results.append(result)

        # A final result is checkpointed only after it is validated.  The
        # checkpoint contract is intentionally prose-free.
        if (
            checkpoint is not None
            and checkpoint.stage is CheckpointStage.SEMANTIC_VERIFIED
        ):
            await _checkpoint(
                context,
                checkpoint,
                CheckpointStage.COMPLETED,
                reservation_ids=tuple(reservations),
                attempts_used=attempts_used,
                recovery_attempted=recovery_attempted,
                completed_results=tuple(results),
            )
        return L2Outcome(
            tuple(results),
            tuple(reservations),
            attempts_used,
            recovery_attempted,
            None,
            None,
        )
    except EgressPolicyViolation as error:
        return L2Outcome(
            (),
            tuple(reservations),
            attempts_used,
            recovery_attempted,
            None,
            None,
            error.code,
        )
    except ProviderAttemptError as error:
        return L2Outcome(
            (),
            tuple(reservations),
            attempts_used,
            recovery_attempted,
            error.public_error_code,
            None,
        )
    except InvalidComplianceReply:
        return L2Outcome(
            (),
            tuple(reservations),
            attempts_used,
            recovery_attempted,
            None,
            "invalid_compliance_reply",
        )
    except InvalidSemanticReply:
        return L2Outcome(
            (),
            tuple(reservations),
            attempts_used,
            recovery_attempted,
            None,
            "invalid_semantic_reply",
        )


async def _verify_candidate(
    context: L2RunContext,
    candidate: IdentifiedCandidate,
    evidence: tuple[Evidence, ...],
    attempts_used: int,
    recovery_attempted: bool,
) -> tuple[ComplianceResult, tuple[Evidence, ...], int, bool, str | None]:
    if candidate.candidate.proposed_verdict is ComplianceVerdict.INSUFFICIENT_EVIDENCE:
        return (
            _insufficient(
                candidate.claim_id,
                VerificationStatus.INSUFFICIENT,
                "proposed_insufficient",
            ),
            evidence,
            attempts_used,
            recovery_attempted,
            None,
        )

    active = candidate
    deterministic = context.deterministic_verifier(active.candidate, evidence)
    checkpoint = context.checkpoint
    if not deterministic.passed:
        recovered = await _recover(
            context,
            active,
            evidence,
            _deterministic_reasons(deterministic),
            attempts_used,
            recovery_attempted,
        )
        if recovered is None:
            return (
                _insufficient(
                    active.claim_id,
                    VerificationStatus.DETERMINISTIC_FAILED,
                    _first_deterministic_reason(deterministic),
                ),
                evidence,
                attempts_used,
                recovery_attempted,
                None,
            )
        evidence, attempts_used = recovered
        recovery_attempted = True
        if checkpoint is not None:
            checkpoint = await _checkpoint(
                context,
                checkpoint,
                CheckpointStage.RECOVERY_COMPLETED,
                evidence=evidence,
                reservation_ids=tuple(str(item) for item in checkpoint.reservation_ids),
                attempts_used=attempts_used,
                recovery_attempted=True,
            )
        active = _rebuild_candidate(active, evidence)
        deterministic = context.deterministic_verifier(active.candidate, evidence)
        if not deterministic.passed:
            return (
                _insufficient(
                    active.claim_id,
                    VerificationStatus.DETERMINISTIC_FAILED,
                    _first_deterministic_reason(deterministic),
                ),
                evidence,
                attempts_used,
                recovery_attempted,
                None,
            )

    if checkpoint is not None:
        checkpoint = await _checkpoint(
            context,
            checkpoint,
            CheckpointStage.DETERMINISTIC_VERIFIED,
            evidence=evidence,
            reservation_ids=tuple(str(item) for item in checkpoint.reservation_ids),
            attempts_used=attempts_used,
            recovery_attempted=recovery_attempted,
        )
    generation = 0
    semantic = await context.semantic_verifier.verify(
        active,
        evidence,
        deterministic,
        replace(
            context.semantic_context,
            idempotency_key=(
                f"{active.claim_id}-recovery"
                if recovery_attempted
                else f"{active.claim_id}-initial"
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
            attempts_used,
            recovery_attempted,
            None,
        )
    if semantic.decision.supports_verdict:
        if checkpoint is not None:
            await _checkpoint(
                context,
                checkpoint,
                CheckpointStage.SEMANTIC_VERIFIED,
                evidence=evidence,
                reservation_ids=tuple(str(item) for item in checkpoint.reservation_ids),
                attempts_used=attempts_used,
                recovery_attempted=recovery_attempted,
            )
        return (
            ComplianceResult(
                claim_id=active.claim_id,
                verdict=active.candidate.proposed_verdict,
                verification_status=VerificationStatus.VERIFIED,
                citations=deterministic.citations,
            ),
            evidence,
            attempts_used,
            recovery_attempted,
            semantic.reservation_id,
        )

    recovered = await _recover(
        context,
        active,
        evidence,
        (semantic.decision.reason.value,),
        attempts_used,
        recovery_attempted,
    )
    if recovered is None:
        return (
            _insufficient(
                active.claim_id,
                VerificationStatus.SEMANTIC_FAILED,
                semantic.decision.reason.value,
            ),
            evidence,
            attempts_used,
            recovery_attempted,
            semantic.reservation_id,
        )
    evidence, attempts_used = recovered
    recovery_attempted = True
    if checkpoint is not None:
        checkpoint = await _checkpoint(
            context,
            checkpoint,
            CheckpointStage.RECOVERY_COMPLETED,
            evidence=evidence,
            reservation_ids=tuple(str(item) for item in checkpoint.reservation_ids),
            attempts_used=attempts_used,
            recovery_attempted=True,
        )
    active = _rebuild_candidate(active, evidence)
    deterministic = context.deterministic_verifier(active.candidate, evidence)
    if not deterministic.passed:
        return (
            _insufficient(
                active.claim_id,
                VerificationStatus.DETERMINISTIC_FAILED,
                _first_deterministic_reason(deterministic),
            ),
            evidence,
            attempts_used,
            recovery_attempted,
            semantic.reservation_id,
        )
    semantic = await context.semantic_verifier.verify(
        active,
        evidence,
        deterministic,
        replace(
            context.semantic_context,
            idempotency_key=f"{active.claim_id}-recovery",
            reconstruction_generation=0,
        ),
    )
    if semantic.decision.supports_verdict:
        if checkpoint is not None:
            await _checkpoint(
                context,
                checkpoint,
                CheckpointStage.SEMANTIC_VERIFIED,
                evidence=evidence,
                reservation_ids=tuple(str(item) for item in checkpoint.reservation_ids),
                attempts_used=attempts_used,
                recovery_attempted=recovery_attempted,
            )
        return (
            ComplianceResult(
                claim_id=active.claim_id,
                verdict=active.candidate.proposed_verdict,
                verification_status=VerificationStatus.VERIFIED,
                citations=deterministic.citations,
            ),
            evidence,
            attempts_used,
            recovery_attempted,
            semantic.reservation_id,
        )
    return (
        _insufficient(
            active.claim_id,
            VerificationStatus.SEMANTIC_FAILED,
            semantic.decision.reason.value,
        ),
        evidence,
        attempts_used,
        recovery_attempted,
        semantic.reservation_id,
    )


async def _recover(
    context: L2RunContext,
    candidate: IdentifiedCandidate,
    evidence: tuple[Evidence, ...],
    reasons: tuple[str, ...],
    attempts_used: int,
    recovery_attempted: bool,
) -> tuple[tuple[Evidence, ...], int] | None:
    if recovery_attempted or attempts_used >= _L2_TOOL_BUDGET:
        return None
    recovered = await context.recovery_runner(
        candidate, evidence, reasons, attempts_used
    )
    if not context.lease_is_live() or recovered.attempts_used > _L2_TOOL_BUDGET:
        return None
    return _bounded_evidence(recovered.evidence), recovered.attempts_used


def _rebuild_candidate(
    candidate: IdentifiedCandidate, evidence: tuple[Evidence, ...]
) -> IdentifiedCandidate:
    # Recovery never trusts a model to nominate a new Evidence ID. It derives a
    # fresh, bounded local candidate from the frozen evidence just recovered.
    rebuilt = ComplianceCandidate(
        claim=candidate.candidate.claim,
        proposed_verdict=candidate.candidate.proposed_verdict,
        # ``execute_recovery`` preserves the old items and appends locally
        # rebuilt ones.  Prefer the bounded replacement tail so a known-bad
        # initial handle cannot crowd the recovered evidence back out.
        evidence_ids=tuple(
            item.excerpt.content_hash for item in evidence[-_MAX_CANDIDATE_EVIDENCE:]
        ),
        rationale=candidate.candidate.rationale,
    )
    return IdentifiedCandidate(claim_id=candidate.claim_id, candidate=rebuilt)


def _bounded_evidence(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    unique: dict[str, Evidence] = {}
    for item in evidence:
        unique.setdefault(item.excerpt.content_hash, item)
        if len(unique) == _MAX_EVIDENCE:
            break
    return tuple(unique.values())


def _deterministic_reasons(result: DeterministicResult) -> tuple[str, ...]:
    return tuple(
        check.fault.value for check in result.checks if check.fault is not None
    )


def _first_deterministic_reason(result: DeterministicResult) -> str:
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


def _generation(
    checkpoint: RunCheckpoint | None, stage: str, claim_id: str | None, recovery: bool
) -> int:
    if checkpoint is None:
        return 0
    generations = [
        item.generation
        for item in checkpoint.reconstruction_generations
        if item.stage == stage
        and item.claim_id == claim_id
        and item.recovery == recovery
    ]
    return (max(generations) + 1) if generations else 0


async def _checkpoint(
    context: L2RunContext,
    previous: RunCheckpoint | None,
    stage: CheckpointStage,
    *,
    evidence: tuple[Evidence, ...] = (),
    reservation_ids: tuple[str, ...],
    attempts_used: int,
    recovery_attempted: bool,
    plan_id: str | None = None,
    plan_hash: str | None = None,
    completed_results: tuple[ComplianceResult, ...] = (),
) -> RunCheckpoint | None:
    # Task 7 owns creation of the initial durable envelope. This function only
    # advances a supplied envelope; it cannot accidentally persist prose.
    if previous is None or context.checkpoint_writer is None:
        return previous
    if stage is previous.stage:
        return previous
    from specpilot.checkpoints.contracts import EvidenceCheckpointRef

    safe_evidence = tuple(
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
    next_checkpoint = previous.model_copy(
        update={
            "checkpoint_version": previous.checkpoint_version + 1,
            "stage": stage,
            "plan_id": plan_id if plan_id is not None else previous.plan_id,
            "plan_hash": plan_hash if plan_hash is not None else previous.plan_hash,
            "evidence": safe_evidence or previous.evidence,
            "reservation_ids": tuple(_uuid_values(reservation_ids)),
            "tool_attempts_used": attempts_used,
            "recovery_attempted": recovery_attempted,
            "completed_claim_ids": tuple(item.claim_id for item in completed_results)
            or previous.completed_claim_ids,
            "completed_results": completed_results or previous.completed_results,
        }
    )
    return await context.checkpoint_writer(next_checkpoint)


def _uuid_values(values: tuple[str, ...]) -> tuple[UUID, ...]:
    from uuid import UUID

    return tuple(UUID(value) for value in values)


def _plan_hash(plan: ToolPlan) -> str:
    import hashlib

    return hashlib.sha256(plan.model_dump_json().encode("utf-8")).hexdigest()


def _abandoned(
    attempts_used: int, reservations: list[str], recovery_attempted: bool
) -> L2Outcome:
    return L2Outcome(
        (), tuple(reservations), attempts_used, recovery_attempted, None, "lease_lost"
    )


__all__ = ["L2Outcome", "L2RunContext", "logical_stage_key", "run_l2_attempt"]
