"""Closed, sanitized contracts for durable asynchronous run traces.

Trace payloads are deliberately metadata-only.  There is no generic mapping
event: every persisted kind has a frozen, extra-forbidding model so a future
caller cannot add a question, excerpt, provider body, credential, or path by
accident.  Provider failure and verifier outcome are separate fields because a
timeout must never be reported as the system's evidence-based refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from specpilot.agents.contracts import ToolName
from specpilot.contracts.answer import AnswerVerdict
from specpilot.contracts.egress import EgressStage
from specpilot.contracts.manifests import Sha256

TraceIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
ArgumentKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$",
    ),
]
TerminalReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    ),
]
Sequence = Annotated[int, Field(ge=1, le=10_000)]
Count = Annotated[int, Field(ge=0, le=1_000_000)]
DurationMs = Annotated[int, Field(ge=0, le=3_600_000)]
CostMicrounits = Annotated[int, Field(ge=0, le=1_000_000_000)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    ANSWERED = "answered"
    REFUSED = "refused"
    EGRESS_BLOCKED = "egress_blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


_NONTERMINAL_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})


class RunEventKind(StrEnum):
    STATE_TRANSITION = "state_transition"
    PLAN_SUMMARY = "plan_summary"
    AGENT_STEP = "agent_step"
    TOOL_FINISHED = "tool_finished"
    CANDIDATE_SUMMARY = "candidate_summary"
    EVIDENCE_SUMMARY = "evidence_summary"
    EGRESS_SUMMARY = "egress_summary"
    USAGE_SUMMARY = "usage_summary"
    ANSWER_OUTCOME = "answer_outcome"
    VERIFIER_SUMMARY = "verifier_summary"
    TERMINAL = "terminal"


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    EVIDENCE_AGENT = "evidence_agent"
    ANSWER = "answer"
    VERIFIER = "verifier"


class AgentStepPhase(StrEnum):
    STARTED = "started"
    FINISHED = "finished"


class _RunEventBase(_FrozenModel):
    sequence: Sequence


class StateTransitionEvent(_RunEventBase):
    kind: Literal[RunEventKind.STATE_TRANSITION] = RunEventKind.STATE_TRANSITION
    previous_status: RunStatus | None
    status: RunStatus
    reason: TerminalReason | None


class PlanSummaryEvent(_RunEventBase):
    kind: Literal[RunEventKind.PLAN_SUMMARY] = RunEventKind.PLAN_SUMMARY
    plan_id: TraceIdentifier
    step_count: Annotated[int, Field(ge=1, le=4)]
    max_tool_calls: Annotated[int, Field(ge=1, le=6)]


class AgentStepEvent(_RunEventBase):
    kind: Literal[RunEventKind.AGENT_STEP] = RunEventKind.AGENT_STEP
    agent: AgentName
    step_id: TraceIdentifier
    phase: AgentStepPhase
    duration_ms: DurationMs | None
    error_code: TerminalReason | None


class ToolFinishedEvent(_RunEventBase):
    kind: Literal[RunEventKind.TOOL_FINISHED] = RunEventKind.TOOL_FINISHED
    step_id: TraceIdentifier
    tool: ToolName
    argument_keys: Annotated[tuple[ArgumentKey, ...], Field(max_length=16)]
    result_count: Count
    duration_ms: DurationMs
    retry_count: Annotated[int, Field(ge=0, le=1)]
    error_code: TerminalReason | None


class CandidateScoreSummary(_FrozenModel):
    candidate_id: TraceIdentifier
    score: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]


class CandidateSummaryEvent(_RunEventBase):
    kind: Literal[RunEventKind.CANDIDATE_SUMMARY] = RunEventKind.CANDIDATE_SUMMARY
    candidates: Annotated[tuple[CandidateScoreSummary, ...], Field(max_length=20)]


class EvidenceRefSummary(_FrozenModel):
    evidence_id: Sha256
    content_hash: Sha256


class EvidenceSummaryEvent(_RunEventBase):
    kind: Literal[RunEventKind.EVIDENCE_SUMMARY] = RunEventKind.EVIDENCE_SUMMARY
    evidence: Annotated[tuple[EvidenceRefSummary, ...], Field(max_length=5)]


class EgressSummaryEvent(_RunEventBase):
    kind: Literal[RunEventKind.EGRESS_SUMMARY] = RunEventKind.EGRESS_SUMMARY
    stage: EgressStage
    reservation_id: UUID | None
    ledger_id: UUID | None
    admitted: bool
    request_tokens: Count
    request_bytes: Count
    cost_microunits: CostMicrounits
    error_code: TerminalReason | None

    @model_validator(mode="after")
    def _admission_has_a_stable_result(self) -> Self:
        if self.admitted and self.error_code is not None:
            raise ValueError("an admitted egress summary has no error code")
        if not self.admitted and self.error_code is None:
            raise ValueError("a blocked egress summary requires a stable error code")
        return self


class UsageSummaryEvent(_RunEventBase):
    kind: Literal[RunEventKind.USAGE_SUMMARY] = RunEventKind.USAGE_SUMMARY
    stage: EgressStage
    prompt_tokens: Count
    completion_tokens: Count
    request_bytes: Count
    duration_ms: DurationMs
    cost_microunits: CostMicrounits


class AnswerOutcomeEvent(_RunEventBase):
    kind: Literal[RunEventKind.ANSWER_OUTCOME] = RunEventKind.ANSWER_OUTCOME
    verdict: AnswerVerdict
    refusal_reason: TerminalReason | None
    provider_error: TerminalReason | None
    reservation_id: UUID | None
    replayed: bool
    parse_fault_code: TerminalReason | None

    @model_validator(mode="after")
    def _verdict_has_its_own_reason(self) -> Self:
        if self.verdict is AnswerVerdict.ANSWERED and self.refusal_reason is not None:
            raise ValueError("an answered outcome has no refusal reason")
        if self.verdict is AnswerVerdict.REFUSED and self.refusal_reason is None:
            raise ValueError("a refused outcome requires a refusal reason")
        return self


class VerifierCheckSummary(_FrozenModel):
    evidence_id: Sha256 | None
    passed: bool
    fault_code: TerminalReason | None

    @model_validator(mode="after")
    def _failure_has_a_stable_fault(self) -> Self:
        if self.passed and self.fault_code is not None:
            raise ValueError("a passed verifier check has no fault code")
        if not self.passed and self.fault_code is None:
            raise ValueError("a failed verifier check requires a fault code")
        return self


class VerifierSummaryEvent(_RunEventBase):
    kind: Literal[RunEventKind.VERIFIER_SUMMARY] = RunEventKind.VERIFIER_SUMMARY
    checks: Annotated[tuple[VerifierCheckSummary, ...], Field(max_length=20)]
    duration_ms: DurationMs


class TerminalEvent(_RunEventBase):
    kind: Literal[RunEventKind.TERMINAL] = RunEventKind.TERMINAL
    status: RunStatus
    reason: TerminalReason | None

    @model_validator(mode="after")
    def _status_is_terminal(self) -> Self:
        if self.status in _NONTERMINAL_STATUSES:
            raise ValueError("a terminal event requires a terminal status")
        if self.status is RunStatus.ANSWERED and self.reason is not None:
            raise ValueError("an answered terminal event has no failure reason")
        if self.status is not RunStatus.ANSWERED and self.reason is None:
            raise ValueError("a non-success terminal event requires a stable reason")
        return self


RunEvent = Annotated[
    StateTransitionEvent
    | PlanSummaryEvent
    | AgentStepEvent
    | ToolFinishedEvent
    | CandidateSummaryEvent
    | EvidenceSummaryEvent
    | EgressSummaryEvent
    | UsageSummaryEvent
    | AnswerOutcomeEvent
    | VerifierSummaryEvent
    | TerminalEvent,
    Field(discriminator="kind"),
]


class RunRecord(_FrozenModel):
    """Owner-bound persistence shape; query identity is a hash only."""

    run_id: UUID
    request_id: UUID
    session_id: TraceIdentifier
    task_level: Literal["L1"]
    profile: TraceIdentifier
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256
    policy_hash: Sha256
    configuration_hash: Sha256
    prompt_id: TraceIdentifier
    prompt_hash: Sha256
    provider_id: TraceIdentifier
    model_id: TraceIdentifier
    query_hash: Sha256
    status: RunStatus
    terminal_reason: TerminalReason | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    lease_owner: TraceIdentifier | None
    lease_expires_at: datetime | None
    last_heartbeat_at: datetime | None

    @field_validator(
        "created_at",
        "started_at",
        "completed_at",
        "lease_expires_at",
        "last_heartbeat_at",
    )
    @classmethod
    def _timestamps_are_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _state_timestamps_and_lease_agree(self) -> Self:
        lease_present = (
            self.lease_owner is not None and self.lease_expires_at is not None
        )
        lease_partial = (self.lease_owner is None) != (self.lease_expires_at is None)
        if lease_partial:
            raise ValueError("lease owner and expiry must be set together")

        if self.status in _NONTERMINAL_STATUSES:
            if self.terminal_reason is not None or self.completed_at is not None:
                raise ValueError("a nonterminal run has no terminal metadata")
            if not lease_present:
                raise ValueError("a queued or running run requires a lease")
        else:
            if self.completed_at is None:
                raise ValueError("a terminal run requires a completion time")
            if self.status is RunStatus.ANSWERED and self.terminal_reason is not None:
                raise ValueError("an answered run has no failure reason")
            if self.status is not RunStatus.ANSWERED and self.terminal_reason is None:
                raise ValueError("a non-success terminal run requires a stable reason")
            if lease_present or self.last_heartbeat_at is not None:
                raise ValueError("a terminal run may not retain a lease")

        if self.status is RunStatus.QUEUED and self.started_at is not None:
            raise ValueError("a queued run has not started")
        if self.status is RunStatus.RUNNING and self.started_at is None:
            raise ValueError("a running run requires a start time")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("start time precedes creation")
        if self.completed_at is not None:
            floor = self.started_at or self.created_at
            if self.completed_at < floor:
                raise ValueError("completion time precedes run activity")
        if (
            self.lease_expires_at is not None
            and self.lease_expires_at <= self.created_at
        ):
            raise ValueError("lease expiry must follow creation")
        if self.last_heartbeat_at is not None:
            if self.lease_expires_at is None:
                raise ValueError("a heartbeat requires a lease")
            floor = self.started_at or self.created_at
            if not floor <= self.last_heartbeat_at <= self.lease_expires_at:
                raise ValueError("heartbeat must fall within the active lease")
        return self


class RunView(_FrozenModel):
    """Owner-authorized API projection without owner, query, or lease internals."""

    run_id: UUID
    request_id: UUID
    task_level: Literal["L1"]
    profile: TraceIdentifier
    corpus_manifest_id: Sha256
    status: RunStatus
    reason: TerminalReason | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    events: Annotated[tuple[RunEvent, ...], Field(max_length=10_000)]

    @field_validator("created_at", "started_at", "completed_at")
    @classmethod
    def _timestamps_are_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _reason_matches_effective_status(self) -> Self:
        if self.status in _NONTERMINAL_STATUSES and self.reason is not None:
            raise ValueError("a nonterminal view has no terminal reason")
        if self.status is RunStatus.ANSWERED and self.reason is not None:
            raise ValueError("an answered view has no failure reason")
        if (
            self.status not in _NONTERMINAL_STATUSES
            and self.status is not RunStatus.ANSWERED
            and self.reason is None
        ):
            raise ValueError("a non-success terminal view requires a stable reason")
        return self


__all__ = [
    "AgentName",
    "AgentStepEvent",
    "AgentStepPhase",
    "AnswerOutcomeEvent",
    "CandidateScoreSummary",
    "CandidateSummaryEvent",
    "EgressSummaryEvent",
    "EvidenceRefSummary",
    "EvidenceSummaryEvent",
    "PlanSummaryEvent",
    "RunEvent",
    "RunEventKind",
    "RunRecord",
    "RunStatus",
    "RunView",
    "StateTransitionEvent",
    "TerminalEvent",
    "TerminalReason",
    "ToolFinishedEvent",
    "UsageSummaryEvent",
    "VerifierCheckSummary",
    "VerifierSummaryEvent",
]
