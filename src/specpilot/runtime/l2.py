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
from specpilot.agents.contracts import ToolCallSummary, ToolPlan
from specpilot.agents.evidence import EvidenceResult
from specpilot.agents.planner import InvalidToolPlan, PlannerContext, PlannerResult
from specpilot.answer.evidence import Evidence
from specpilot.checkpoints.contracts import (
    CheckpointStage,
    EvidenceCheckpointRef,
    RunCheckpoint,
    StageGeneration,
)
from specpilot.contracts.egress import EgressStage
from specpilot.contracts.verdict import (
    ComplianceCandidate,
    ComplianceResult,
    ComplianceVerdict,
    IdentifiedCandidate,
    VerificationStatus,
)
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import RequestSize
from specpilot.providers.transport import ProviderAttemptError, TransportReplayError
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


class _GenerationBudgetExhausted(Exception):
    """The durable eight-send boundary is reached before an egress call."""


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
type CheckpointWriter = Callable[[int | None, RunCheckpoint], Awaitable[RunCheckpoint]]
type EvidenceRestorer = Callable[
    [tuple[EvidenceCheckpointRef, ...]], tuple[Evidence, ...]
]
type PlanRestorer = Callable[[str, str], ToolPlan | None]
type LeaseIsLive = Callable[[], bool]
type L2AuditSink = Callable[["L2AuditEvent"], Awaitable[None]]


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
    plan_restorer: PlanRestorer | None = None
    lease_is_live: LeaseIsLive = lambda: True
    audit_sink: L2AuditSink | None = None


@dataclass(frozen=True, slots=True)
class L2PlanningAudit:
    outcome: PlannerResult


@dataclass(frozen=True, slots=True)
class L2EvidenceAudit:
    calls: tuple[ToolCallSummary, ...]
    audit_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class L2ComplianceAudit:
    outcome: ComplianceOutcome


@dataclass(frozen=True, slots=True)
class L2EgressAudit:
    stage: EgressStage
    reservation_id: str
    replayed: bool
    request_size: RequestSize | None


@dataclass(frozen=True, slots=True)
class L2DeterministicAudit:
    claim_id: str
    audit_id: str
    result: DeterministicResult


@dataclass(frozen=True, slots=True)
class L2SemanticAudit:
    claim_id: str
    outcome: SemanticOutcome


@dataclass(frozen=True, slots=True)
class L2RecoveryAudit:
    audit_id: str
    outcome: RecoveryOutcome


type L2AuditEvent = (
    L2PlanningAudit
    | L2EvidenceAudit
    | L2ComplianceAudit
    | L2EgressAudit
    | L2DeterministicAudit
    | L2SemanticAudit
    | L2RecoveryAudit
)


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
    evidence_calls: tuple[ToolCallSummary, ...] = ()
    deterministic_outcomes: tuple[tuple[str, DeterministicResult], ...] = ()
    audit_events: tuple[L2AuditEvent, ...] = ()
    checkpoints: tuple[RunCheckpoint, ...] = ()
    planning: PlannerResult | None = None


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


def _idempotency_suffix(
    run_id: str, stage: str, claim_id: str | None, recovery: bool, generation: int
) -> str:
    """Inverse the agent-owned key wrapper from the canonical L2 key."""
    logical = logical_stage_key(run_id, stage, claim_id, recovery, generation)
    prefix = f"{run_id}-{stage}-"
    marker = f"-g{generation}"
    if not logical.startswith(prefix) or not logical.endswith(marker):
        raise ValueError("logical_stage_key_invalid")
    return logical[len(prefix) : -len(marker)]


def _planning_suffix(run_id: str) -> str:
    return logical_stage_key(run_id, "planning", None, False, 0)


async def _emit_audit(context: L2RunContext, event: L2AuditEvent) -> None:
    """Persist one closed audit fact before any later outward operation."""
    if context.audit_sink is not None:
        await context.audit_sink(event)


async def _emit_exception_egress(
    context: L2RunContext,
    stage: EgressStage,
    error: ProviderAttemptError
    | TransportReplayError
    | InvalidToolPlan
    | InvalidComplianceReply
    | InvalidSemanticReply,
) -> None:
    await _emit_audit(
        context,
        L2EgressAudit(
            stage=stage,
            reservation_id=error.reservation_id,
            replayed=error.replayed,
            request_size=getattr(error, "request_size", None),
        ),
    )


async def run_l2_attempt(context: L2RunContext) -> L2Outcome:
    """Execute exactly the missing portion of the closed checkpoint graph."""
    checkpoint = context.checkpoint
    reservations = [
        str(item) for item in (checkpoint.reservation_ids if checkpoint else ())
    ]
    attempts = checkpoint.tool_attempts_used if checkpoint else 0
    recovered = bool(checkpoint and checkpoint.recovery_attempted)
    written: list[RunCheckpoint] = []
    semantic_outcomes: list[tuple[str, SemanticOutcome]] = []
    recovery_outcomes: list[RecoveryOutcome] = []
    evidence_calls: tuple[ToolCallSummary, ...] = ()
    deterministic_outcomes: list[tuple[str, DeterministicResult]] = []
    audit_events: list[L2AuditEvent] = []
    plan: ToolPlan | None = None
    planning_result: PlannerResult | None = None
    if checkpoint is not None and checkpoint.stage is CheckpointStage.COMPLETED:
        return L2Outcome(
            checkpoint.completed_results,
            tuple(reservations),
            attempts,
            recovered,
            None,
            None,
        )
    # A semantic cursor can be terminal without reconstructing any local
    # prose.  This is intentionally before both restorer gates.
    if (
        checkpoint is not None
        and checkpoint.stage is CheckpointStage.SEMANTIC_VERIFIED
        and checkpoint.candidate_count > 0
        and len(checkpoint.completed_results) == checkpoint.candidate_count
    ):
        return L2Outcome(
            checkpoint.completed_results,
            tuple(reservations),
            attempts,
            recovered,
            None,
            None,
        )
    evidence = _restore(context, checkpoint)

    async def write(stage: CheckpointStage, **updates: Any) -> None:
        nonlocal checkpoint
        checkpoint = await _advance(context, checkpoint, stage, **updates)
        if checkpoint is not None:
            written.append(checkpoint)

    try:
        # Write a g0 planning skeleton before the first model request.  It has
        # no plan prose, but closes the crash window around the reservation.
        if checkpoint is None:
            checkpoint = await _first_checkpoint(
                context, None, reservations, attempts, written
            )
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            try:
                planning = await context.planner.plan(
                    context.question,
                    replace(
                        context.planner_context,
                        idempotency_key=_planning_suffix(context.run_id),
                        reconstruction_generation=0,
                    ),
                )
            except (
                ProviderAttemptError,
                TransportReplayError,
                InvalidToolPlan,
            ) as error:
                await _emit_exception_egress(context, EgressStage.PLANNING, error)
                raise
            planning_result = planning
            await _emit_audit(context, L2PlanningAudit(planning))
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
        elif checkpoint.stage is CheckpointStage.PLANNED:
            # The plan's text-bearing tool arguments are deliberately absent;
            # a process loss sends planning at a newly persisted generation.
            checkpoint = await _prepare_generation(
                context, checkpoint, "planning", None, False, written
            )
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            generation = _latest_generation(checkpoint, "planning", None, False)
            try:
                planning = await context.planner.plan(
                    context.question,
                    replace(
                        context.planner_context,
                        idempotency_key=_planning_suffix(context.run_id),
                        reconstruction_generation=generation,
                    ),
                )
            except (
                ProviderAttemptError,
                TransportReplayError,
                InvalidToolPlan,
            ) as error:
                await _emit_exception_egress(context, EgressStage.PLANNING, error)
                raise
            planning_result = planning
            await _emit_audit(context, L2PlanningAudit(planning))
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
            )
            plan = planning.plan
        if checkpoint is None or checkpoint.stage is CheckpointStage.PLANNED:
            if plan is None:
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
            evidence_audit_calls = tuple(collected.calls)
            attempt_number = 1 if checkpoint is None else checkpoint.attempt
            await _emit_audit(
                context,
                L2EvidenceAudit(
                    evidence_audit_calls,
                    tuple(
                        f"tool/initial/a{attempt_number}/{call.step_id}"
                        for call in evidence_audit_calls
                    ),
                ),
            )
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            attempts = collected.attempts_used
            prior_attempts = checkpoint.tool_attempts_used if checkpoint else 0
            if attempts < prior_attempts or attempts > _L2_TOOL_BUDGET:
                return _fault(
                    "checkpoint_attempt_integrity",
                    attempts,
                    reservations,
                    recovered,
                    written,
                )
            evidence = _bounded_evidence(collected.evidence)
            evidence_calls = tuple(collected.calls)
            await write(
                CheckpointStage.EVIDENCE_COLLECTED,
                evidence=evidence,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
            )
        elif not evidence:
            return _fault(
                "checkpoint_evidence_unavailable",
                attempts,
                reservations,
                recovered,
                written,
            )

        checkpoint = await _prepare_generation(
            context, checkpoint, "compliance", None, False, written
        )
        if not context.lease_is_live():
            return _abandoned(attempts, reservations, recovered, written)
        generation = _latest_generation(checkpoint, "compliance", None, False)
        try:
            compliance = await context.compliance_agent.evaluate(
                context.question,
                evidence,
                replace(
                    context.compliance_context,
                    idempotency_key=_idempotency_suffix(
                        context.run_id, "compliance", None, False, generation
                    ),
                    reconstruction_generation=generation,
                ),
            )
        except (
            ProviderAttemptError,
            TransportReplayError,
            InvalidComplianceReply,
        ) as error:
            await _emit_exception_egress(context, EgressStage.COMPLIANCE, error)
            raise
        await _emit_audit(context, L2ComplianceAudit(compliance))
        if not context.lease_is_live():
            return _abandoned(attempts, reservations, recovered, written)
        reservations.append(compliance.reservation_id)
        if (
            checkpoint is not None
            and checkpoint.stage is CheckpointStage.EVIDENCE_COLLECTED
        ):
            await write(
                CheckpointStage.CANDIDATE_BUILT,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                candidate_count=len(compliance.candidates),
            )

        if not _candidate_batch_is_consistent(checkpoint, compliance.candidates):
            return _fault(
                "checkpoint_candidate_integrity",
                attempts,
                reservations,
                recovered,
                written,
            )
        if not _recovery_reservation_is_consistent(
            checkpoint, compliance.candidates
        ):
            return _fault(
                "checkpoint_recovery_claim_integrity",
                attempts,
                reservations,
                recovered,
                written,
            )
        elif checkpoint is not None:
            # Later-stage resume loses candidate prose, so it must resend
            # Compliance. Keep its receipt via a legal same-stage mutation;
            # never rewind the checkpoint graph merely to record the send.
            await write(
                checkpoint.stage,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
                candidate_count=len(compliance.candidates),
            )

        results: list[ComplianceResult] = []
        completed_ids = set(
            () if checkpoint is None else checkpoint.completed_claim_ids
        )
        candidates = _recovery_candidate_first(checkpoint, compliance.candidates)
        for candidate in candidates:
            if candidate.claim_id in completed_ids:
                continue
            (
                result,
                evidence,
                attempts,
                recovered,
                semantics,
                recovery,
            ) = await _one_claim(
                context,
                checkpoint,
                candidate,
                evidence,
                attempts,
                recovered,
                written,
                deterministic_outcomes,
                audit_events,
            )
            # The per-claim transition is the authoritative checkpoint for the
            # next candidate.  Never reuse the old CAS version in a batch.
            if written:
                checkpoint = written[-1]
            for semantic in semantics:
                reservations.append(semantic.reservation_id)
                semantic_outcomes.append((candidate.claim_id, semantic))
            if recovery is not None:
                recovery_outcomes.append(recovery)
            if not context.lease_is_live():
                return _abandoned(attempts, reservations, recovered, written)
            results.append(result)
            if checkpoint is not None:
                # Persist the cursor before the next candidate can issue a
                # semantic request. A process loss here resumes after this ID.
                await write(
                    CheckpointStage.SEMANTIC_VERIFIED,
                    reservation_ids=reservations,
                    attempts=attempts,
                    recovered=recovered,
                    completed_results=checkpoint.completed_results + (result,),
                    allow_same_stage=True,
                )

        if checkpoint is not None:
            await write(
                CheckpointStage.COMPLETED,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                completed_results=checkpoint.completed_results,
            )
        published_results = (
            tuple(results) if checkpoint is None else checkpoint.completed_results
        )
        return L2Outcome(
            published_results,
            tuple(reservations),
            attempts,
            recovered,
            None,
            None,
            compliance=compliance,
            semantic_outcomes=tuple(semantic_outcomes),
            recovery_outcomes=tuple(recovery_outcomes),
            evidence_calls=evidence_calls,
            deterministic_outcomes=tuple(deterministic_outcomes),
            audit_events=tuple(audit_events),
            checkpoints=tuple(written),
            planning=planning_result,
        )
    except EgressPolicyViolation as error:
        return _fault(
            error.code, attempts, reservations, recovered, written, egress=True
        )
    except ProviderAttemptError as error:
        reservations.append(error.reservation_id)
        if checkpoint is not None:
            await write(
                checkpoint.stage,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
            )
        return L2Outcome(
            (),
            tuple(reservations),
            attempts,
            recovered,
            error.public_error_code,
            None,
            checkpoints=tuple(written),
        )
    except TransportReplayError as error:
        reservations.append(error.reservation_id)
        if checkpoint is not None:
            await write(
                checkpoint.stage,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
            )
        return _fault(
            "transport_replay_refused", attempts, reservations, recovered, written
        )
    except _GenerationBudgetExhausted:
        return _fault(
            "reconstruction_generation_exhausted",
            attempts,
            reservations,
            recovered,
            written,
        )
    except InvalidComplianceReply as error:
        reservations.append(error.reservation_id)
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                checkpoint.stage,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
            )
            if checkpoint is not None:
                written.append(checkpoint)
        return _fault(
            "invalid_compliance_reply", attempts, reservations, recovered, written
        )
    except InvalidSemanticReply as error:
        reservations.append(error.reservation_id)
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                checkpoint.stage,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
            )
            if checkpoint is not None:
                written.append(checkpoint)
        return _fault(
            "invalid_semantic_reply", attempts, reservations, recovered, written
        )
    except InvalidToolPlan as error:
        reservations.append(error.reservation_id)
        if checkpoint is not None:
            await write(
                checkpoint.stage,
                reservation_ids=reservations,
                attempts=attempts,
                recovered=recovered,
                allow_same_stage=True,
            )
        return _fault("invalid_tool_plan", attempts, reservations, recovered, written)


async def _verify_and_record(
    context: L2RunContext,
    candidate: IdentifiedCandidate,
    evidence: tuple[Evidence, ...],
    deterministic_outcomes: list[tuple[str, DeterministicResult]],
    audit_events: list[L2AuditEvent],
    *,
    phase: str,
) -> DeterministicResult:
    result = context.deterministic_verifier(candidate.candidate, evidence)
    deterministic_outcomes.append((candidate.claim_id, result))
    attempt = 1 if context.checkpoint is None else context.checkpoint.attempt
    audit = L2DeterministicAudit(
        candidate.claim_id,
        f"deterministic/{candidate.claim_id}/{phase}/a{attempt}",
        result,
    )
    audit_events.append(audit)
    await _emit_audit(context, audit)
    return result


def _reserve_recovery_attempts(attempts: int, reasons: tuple[str, ...]) -> int:
    if any(
        reason in {"not_disclosed", "document_scope_mismatch"}
        for reason in reasons
    ):
        maximum_calls = 2
    elif "content_hash_mismatch" in reasons:
        maximum_calls = 1
    elif "exception_missing" in reasons:
        maximum_calls = 2
    else:
        maximum_calls = 0
    return min(_L2_TOOL_BUDGET, attempts + maximum_calls)


async def _one_claim(
    context: L2RunContext,
    checkpoint: RunCheckpoint | None,
    candidate: IdentifiedCandidate,
    evidence: tuple[Evidence, ...],
    attempts: int,
    recovered: bool,
    written: list[RunCheckpoint],
    deterministic_outcomes: list[tuple[str, DeterministicResult]],
    audit_events: list[L2AuditEvent],
) -> tuple[
    ComplianceResult,
    tuple[Evidence, ...],
    int,
    bool,
    tuple[SemanticOutcome, ...],
    RecoveryOutcome | None,
]:
    resumed_from_recovery = recovered
    if checkpoint is not None and checkpoint.stage is CheckpointStage.RECOVERY_RESERVED:
        if checkpoint.recovery_claim_id != candidate.claim_id:
            raise ValueError("checkpoint_recovery_claim_integrity")
        return (
            _insufficient(
                candidate.claim_id,
                VerificationStatus.INSUFFICIENT,
                "recovery_result_lost",
            ),
            evidence,
            checkpoint.tool_attempts_used,
            True,
            (),
            None,
        )
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
            (),
            None,
        )
    deterministic = await _verify_and_record(
        context,
        candidate,
        evidence,
        deterministic_outcomes,
        audit_events,
        phase="initial",
    )
    if not deterministic.passed:
        reserved_attempts = attempts
        if checkpoint is not None and not recovered:
            reserved_attempts = _reserve_recovery_attempts(
                attempts,
                tuple(
                    check.fault.value
                    for check in deterministic.checks
                    if check.fault
                ),
            )
            if reserved_attempts > attempts:
                checkpoint = await _advance(
                    context,
                    checkpoint,
                    CheckpointStage.RECOVERY_RESERVED,
                    evidence=evidence,
                    attempts=reserved_attempts,
                    recovered=True,
                    recovery_reason=_reason(deterministic),
                    recovery_claim_id=candidate.claim_id,
                )
                if checkpoint is not None:
                    written.append(checkpoint)
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
                reserved_attempts,
                reserved_attempts > attempts or recovered,
                (),
                None,
            )
        evidence, attempts, recovery = replacement
        attempts = max(attempts, reserved_attempts)
        recovery = replace(recovery, attempts_used=attempts)
        recovery_audit = L2RecoveryAudit(
            f"recovery/{candidate.claim_id}/deterministic/"
            f"a{1 if checkpoint is None else checkpoint.attempt}",
            recovery,
        )
        audit_events.append(recovery_audit)
        await _emit_audit(context, recovery_audit)
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
        rebuilt = _rebuilt(candidate, evidence)
        deterministic = await _verify_and_record(
            context,
            rebuilt,
            evidence,
            deterministic_outcomes,
            audit_events,
            phase="post_deterministic_recovery",
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
                (),
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
    if resumed_from_recovery:
        # A resumed ``recovery_completed`` checkpoint reconstructs the
        # candidate's complete current Evidence set. The local verifier output
        # must be recomputed against that same rebuilt set before it can bind
        # the semantic payload; otherwise its citations describe the pre-crash
        # candidate while the semantic gate sees the rebuilt candidate.
        deterministic = await _verify_and_record(
            context,
            active,
            evidence,
            deterministic_outcomes,
            audit_events,
            phase="resume_recovery",
        )
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
                (),
                recovery,
            )
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
            (),
            recovery,
        )
    generation = _latest_generation(checkpoint, "verifier", active.claim_id, recovered)
    try:
        semantic = await context.semantic_verifier.verify(
            active,
            evidence,
            deterministic,
            replace(
                context.semantic_context,
                idempotency_key=_idempotency_suffix(
                    context.run_id,
                    "verifier",
                    active.claim_id,
                    recovered,
                    generation,
                ),
                reconstruction_generation=generation,
            ),
        )
    except (
        ProviderAttemptError,
        TransportReplayError,
        InvalidSemanticReply,
    ) as error:
        await _emit_exception_egress(context, EgressStage.VERIFIER, error)
        raise
    semantic_audit = L2SemanticAudit(active.claim_id, semantic)
    audit_events.append(semantic_audit)
    await _emit_audit(context, semantic_audit)
    first_semantic = semantic
    if checkpoint is not None:
        # A first semantic receipt is itself an egress fact.  Persist it before
        # recovery can await MCP so an intervening crash cannot erase the send.
        checkpoint = await _advance(
            context,
            checkpoint,
            checkpoint.stage,
            reservation_ids=[
                *(str(item) for item in checkpoint.reservation_ids),
                semantic.reservation_id,
            ],
            attempts=attempts,
            recovered=recovered,
            allow_same_stage=True,
        )
        if checkpoint is not None:
            written.append(checkpoint)
    if not context.lease_is_live():
        return (
            _insufficient(
                active.claim_id, VerificationStatus.INSUFFICIENT, "lease_lost"
            ),
            evidence,
            attempts,
            recovered,
            (semantic,),
            recovery,
        )
    if semantic.decision.supports_verdict:
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                CheckpointStage.SEMANTIC_VERIFIED,
                evidence=evidence,
                reservation_ids=[
                    *(str(item) for item in checkpoint.reservation_ids),
                ],
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
            (semantic,),
            recovery,
        )
    reserved_attempts = attempts
    if checkpoint is not None and not recovered:
        reserved_attempts = _reserve_recovery_attempts(
            attempts, (semantic.decision.reason.value,)
        )
        if reserved_attempts > attempts:
            checkpoint = await _advance(
                context,
                checkpoint,
                CheckpointStage.RECOVERY_RESERVED,
                evidence=evidence,
                attempts=reserved_attempts,
                recovered=True,
                recovery_reason=semantic.decision.reason.value,
                recovery_claim_id=active.claim_id,
            )
            if checkpoint is not None:
                written.append(checkpoint)
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
            reserved_attempts,
            reserved_attempts > attempts or recovered,
            (semantic,),
            recovery,
        )
    evidence, attempts, recovery = replacement
    attempts = max(attempts, reserved_attempts)
    recovery = replace(recovery, attempts_used=attempts)
    recovery_audit = L2RecoveryAudit(
        f"recovery/{active.claim_id}/semantic/"
        f"a{1 if checkpoint is None else checkpoint.attempt}",
        recovery,
    )
    audit_events.append(recovery_audit)
    await _emit_audit(context, recovery_audit)
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
    deterministic = await _verify_and_record(
        context,
        active,
        evidence,
        deterministic_outcomes,
        audit_events,
        phase="post_semantic_recovery",
    )
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
            (semantic,),
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
            (first_semantic,),
            recovery,
        )
    try:
        semantic = await context.semantic_verifier.verify(
            active,
            evidence,
            deterministic,
            replace(
                context.semantic_context,
                idempotency_key=_idempotency_suffix(
                    context.run_id,
                    "verifier",
                    active.claim_id,
                    True,
                    _latest_generation(
                        checkpoint, "verifier", active.claim_id, True
                    ),
                ),
                reconstruction_generation=_latest_generation(
                    checkpoint, "verifier", active.claim_id, True
                ),
            ),
        )
    except (
        ProviderAttemptError,
        TransportReplayError,
        InvalidSemanticReply,
    ) as error:
        await _emit_exception_egress(context, EgressStage.VERIFIER, error)
        raise
    semantic_audit = L2SemanticAudit(active.claim_id, semantic)
    audit_events.append(semantic_audit)
    await _emit_audit(context, semantic_audit)
    if checkpoint is not None:
        checkpoint = await _advance(
            context,
            checkpoint,
            checkpoint.stage,
            reservation_ids=[
                *(str(item) for item in checkpoint.reservation_ids),
                semantic.reservation_id,
            ],
            attempts=attempts,
            recovered=True,
            allow_same_stage=True,
        )
        if checkpoint is not None:
            written.append(checkpoint)
    if not context.lease_is_live():
        return (
            _insufficient(
                active.claim_id, VerificationStatus.INSUFFICIENT, "lease_lost"
            ),
            evidence,
            attempts,
            recovered,
            (first_semantic, semantic),
            recovery,
        )
    if semantic.decision.supports_verdict:
        if checkpoint is not None:
            checkpoint = await _advance(
                context,
                checkpoint,
                CheckpointStage.SEMANTIC_VERIFIED,
                evidence=evidence,
                reservation_ids=[
                    *(str(item) for item in checkpoint.reservation_ids),
                ],
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
            (first_semantic, semantic),
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
        (first_semantic, semantic),
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
    if (
        not context.lease_is_live()
        or outcome.attempts_used > _L2_TOOL_BUDGET
        or outcome.attempts_used < attempts
    ):
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
    if len(checkpoint.reconstruction_generations) >= 8:
        raise _GenerationBudgetExhausted()
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
        next_checkpoint = await context.checkpoint_writer(
            checkpoint.checkpoint_version, next_checkpoint
        )
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
    recovery_reason: str | None = None,
    recovery_claim_id: str | None = None,
    plan_id: str | None = None,
    plan_hash: str | None = None,
    generation: StageGeneration | None = None,
    completed_results: tuple[ComplianceResult, ...] = (),
    candidate_count: int | None = None,
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
                UUID(value) for value in _merged_reservations(previous, reservation_ids)
            ),
            "tool_attempts_used": attempts,
            "recovery_attempted": recovered,
            "recovery_reason": (
                previous.recovery_reason
                if recovery_reason is None
                else recovery_reason
            ),
            "recovery_claim_id": (
                previous.recovery_claim_id
                if (
                    stage is CheckpointStage.RECOVERY_RESERVED
                    and recovery_claim_id is None
                )
                else recovery_claim_id
            ),
            "completed_claim_ids": tuple(item.claim_id for item in completed_results)
            or previous.completed_claim_ids,
            "completed_results": completed_results or previous.completed_results,
            "candidate_count": (
                previous.candidate_count if candidate_count is None else candidate_count
            ),
        }
    )
    if context.checkpoint_writer is not None:
        return await context.checkpoint_writer(
            previous.checkpoint_version, next_checkpoint
        )
    return next_checkpoint


def _merged_reservations(
    previous: RunCheckpoint, reservation_ids: list[str] | None
) -> list[str]:
    """A local outcome may lag a same-stage receipt checkpoint; never erase it."""
    values = [str(item) for item in previous.reservation_ids]
    for reservation_id in reservation_ids or ():
        if reservation_id not in values:
            values.append(reservation_id)
    return values


async def _first_checkpoint(
    context: L2RunContext,
    plan: ToolPlan | None,
    reservations: list[str],
    attempts: int,
    written: list[RunCheckpoint],
) -> RunCheckpoint | None:
    """Persist the first accepted planning result with the real CAS ``None``."""
    if context.checkpoint_factory is None:
        return None
    first = context.checkpoint_factory().model_copy(
        update={
            "plan_id": None if plan is None else plan.plan_id,
            "plan_hash": None if plan is None else _plan_hash(plan),
            "reservation_ids": tuple(UUID(value) for value in reservations),
            "tool_attempts_used": attempts,
            "reconstruction_generations": (
                StageGeneration(
                    stage="planning", claim_id=None, recovery=False, generation=0
                ),
            ),
        }
    )
    if context.checkpoint_writer is not None:
        first = await context.checkpoint_writer(None, first)
    written.append(first)
    return first


def _restore(
    context: L2RunContext, checkpoint: RunCheckpoint | None
) -> tuple[Evidence, ...]:
    if checkpoint is None or context.evidence_restorer is None:
        return ()
    restored = context.evidence_restorer(checkpoint.evidence)
    if len(restored) != len(checkpoint.evidence):
        return ()
    actual = _refs(restored)
    if actual != checkpoint.evidence:
        return ()
    return restored


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


def _candidate_batch_is_consistent(
    checkpoint: RunCheckpoint | None, candidates: tuple[IdentifiedCandidate, ...]
) -> bool:
    """Reject reconstructed batches that could rewrite a saved claim cursor."""
    if checkpoint is None:
        return len(candidates) <= 3
    ids = tuple(candidate.claim_id for candidate in candidates)
    if len(ids) != len(set(ids)) or len(ids) > 3:
        return False
    if checkpoint.candidate_count and len(ids) != checkpoint.candidate_count:
        return False
    return set(checkpoint.completed_claim_ids).issubset(ids)


def _recovery_reservation_is_consistent(
    checkpoint: RunCheckpoint | None, candidates: tuple[IdentifiedCandidate, ...]
) -> bool:
    """Bind a reserved MCP action to exactly one reconstructed candidate."""
    if checkpoint is None or checkpoint.stage is not CheckpointStage.RECOVERY_RESERVED:
        return True
    claim_id = checkpoint.recovery_claim_id
    return claim_id is not None and sum(
        candidate.claim_id == claim_id for candidate in candidates
    ) == 1 and claim_id not in checkpoint.completed_claim_ids


def _recovery_candidate_first(
    checkpoint: RunCheckpoint | None, candidates: tuple[IdentifiedCandidate, ...]
) -> tuple[IdentifiedCandidate, ...]:
    """Close a lost reserved action for its owner, never the first pending claim."""
    if checkpoint is None or checkpoint.stage is not CheckpointStage.RECOVERY_RESERVED:
        return candidates
    claim_id = checkpoint.recovery_claim_id
    assert claim_id is not None
    bound = tuple(
        candidate for candidate in candidates if candidate.claim_id == claim_id
    )
    return bound + tuple(
        candidate for candidate in candidates if candidate.claim_id != claim_id
    )


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


__all__ = [
    "L2AuditEvent",
    "L2ComplianceAudit",
    "L2DeterministicAudit",
    "L2EgressAudit",
    "L2EvidenceAudit",
    "L2Outcome",
    "L2PlanningAudit",
    "L2RecoveryAudit",
    "L2RunContext",
    "L2SemanticAudit",
    "logical_stage_key",
    "run_l2_attempt",
]
