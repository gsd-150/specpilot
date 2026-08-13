"""Closed, prose-free contracts for a resumable L2 run.

The checkpoint deliberately records only reconstruction inputs that are safe to
persist: frozen identities, hashes, counters, reservations, and already-public
metadata.  Text that could lead to a new outbound disclosure is not representable
by this model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from specpilot.contracts.answer import SectionNumber
from specpilot.contracts.manifests import Identifier, Sha256, _FrozenModel
from specpilot.contracts.verdict import ComplianceResult
from specpilot.runs.contracts import TerminalReason, TraceIdentifier


class CheckpointStage(StrEnum):
    PLANNED = "planned"
    EVIDENCE_COLLECTED = "evidence_collected"
    CANDIDATE_BUILT = "candidate_built"
    DETERMINISTIC_VERIFIED = "deterministic_verified"
    RECOVERY_COMPLETED = "recovery_completed"
    SEMANTIC_VERIFIED = "semantic_verified"
    COMPLETED = "completed"


LEGAL_TRANSITIONS: dict[CheckpointStage, frozenset[CheckpointStage]] = {
    CheckpointStage.PLANNED: frozenset({CheckpointStage.EVIDENCE_COLLECTED}),
    CheckpointStage.EVIDENCE_COLLECTED: frozenset({CheckpointStage.CANDIDATE_BUILT}),
    CheckpointStage.CANDIDATE_BUILT: frozenset(
        {CheckpointStage.DETERMINISTIC_VERIFIED, CheckpointStage.RECOVERY_COMPLETED}
    ),
    CheckpointStage.DETERMINISTIC_VERIFIED: frozenset(
        {CheckpointStage.SEMANTIC_VERIFIED, CheckpointStage.RECOVERY_COMPLETED}
    ),
    CheckpointStage.RECOVERY_COMPLETED: frozenset(
        {CheckpointStage.DETERMINISTIC_VERIFIED}
    ),
    CheckpointStage.SEMANTIC_VERIFIED: frozenset({CheckpointStage.COMPLETED}),
    CheckpointStage.COMPLETED: frozenset(),
}


class EvidenceCheckpointRef(_FrozenModel):
    """Frozen identity of an excerpt, excluding the excerpt bytes themselves."""

    evidence_id: Sha256
    content_hash: Sha256
    quote_hash: Sha256
    clause_id: Sha256
    document_id: Identifier
    document_version: Identifier
    section_number: SectionNumber | None = None
    paragraph_start: Annotated[int, Field(ge=0)]
    paragraph_end: Annotated[int, Field(ge=0)]
    token_start: Annotated[int, Field(ge=0)]
    token_end: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _span_is_ordered(self) -> Self:
        if self.paragraph_end < self.paragraph_start:
            raise ValueError("paragraph span is ordered")
        if self.token_end < self.token_start:
            raise ValueError("token span is ordered")
        return self


class StageGeneration(_FrozenModel):
    """Generation for one potentially lost model result, not its content."""

    stage: Literal["planning", "compliance", "verifier"]
    claim_id: Sha256 | None = None
    recovery: bool
    generation: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _planning_has_no_claim(self) -> Self:
        if self.stage == "planning" and self.claim_id is not None:
            raise ValueError("planning generation has no claim")
        if self.stage != "planning" and self.claim_id is None:
            raise ValueError("claim stage generation requires claim_id")
        return self


class RunCheckpoint(_FrozenModel):
    schema_version: Literal["run-checkpoint/v1"] = "run-checkpoint/v1"
    run_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    checkpoint_version: Annotated[int, Field(ge=1)]
    stage: CheckpointStage
    task_level: Literal["L2"]
    query_hash: Sha256
    evaluation_root_id: TraceIdentifier
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256
    policy_hash: Sha256
    configuration_hash: Sha256
    compliance_prompt_hash: Sha256
    verifier_prompt_hash: Sha256
    provider_id: TraceIdentifier
    model_id: TraceIdentifier
    plan_id: TraceIdentifier | None
    plan_hash: Sha256 | None
    evidence: Annotated[tuple[EvidenceCheckpointRef, ...], Field(max_length=12)]
    tool_attempts_used: Annotated[int, Field(ge=0, le=8)]
    reservation_ids: Annotated[tuple[UUID, ...], Field(max_length=16)]
    reconstruction_generations: Annotated[
        tuple[StageGeneration, ...], Field(max_length=8)
    ]
    recovery_attempted: bool
    recovery_reason: TerminalReason | None
    completed_claim_ids: Annotated[tuple[Sha256, ...], Field(max_length=3)]
    completed_results: Annotated[tuple[ComplianceResult, ...], Field(max_length=3)]
    last_accessed_at: datetime

    @field_validator("last_accessed_at")
    @classmethod
    def _access_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("last_accessed_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _state_is_sanitized_and_internally_consistent(self) -> Self:
        if self.compliance_prompt_hash == self.verifier_prompt_hash:
            raise ValueError("L2 stage prompt hashes must be distinct")
        if (self.plan_id is None) != (self.plan_hash is None):
            raise ValueError("plan id and plan hash are set together")
        if (
            self.stage is CheckpointStage.RECOVERY_COMPLETED
            and not self.recovery_attempted
        ):
            raise ValueError("recovery_completed requires recovery_attempted")
        if not self.recovery_attempted and self.recovery_reason is not None:
            raise ValueError("recovery reason requires recovery_attempted")
        if len(set(self.completed_claim_ids)) != len(self.completed_claim_ids):
            raise ValueError("completed claim IDs must be unique")
        result_ids = tuple(result.claim_id for result in self.completed_results)
        if self.completed_claim_ids != result_ids:
            raise ValueError("completed result IDs match completed claim IDs")
        if len(set(self.reservation_ids)) != len(self.reservation_ids):
            raise ValueError("reservation IDs must be unique")
        evidence_ids = tuple(reference.evidence_id for reference in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("checkpoint evidence IDs must be unique")
        generations = tuple(
            (item.stage, item.claim_id, item.recovery, item.generation)
            for item in self.reconstruction_generations
        )
        if len(set(generations)) != len(generations):
            raise ValueError("reconstruction generations must be unique")
        return self


def validate_transition(previous: RunCheckpoint, current: RunCheckpoint) -> None:
    """Reject every cross-checkpoint mutation except one legal stage advance."""
    if previous.run_id != current.run_id or previous.attempt != current.attempt:
        raise ValueError("checkpoint run and attempt are immutable")
    if current.checkpoint_version != previous.checkpoint_version + 1:
        raise ValueError("checkpoint versions advance by one")
    if current.stage not in LEGAL_TRANSITIONS[previous.stage]:
        raise ValueError("checkpoint stage transition is illegal")
    immutable = (
        "task_level",
        "query_hash",
        "evaluation_root_id",
        "source_manifest_id",
        "corpus_manifest_id",
        "policy_hash",
        "configuration_hash",
        "compliance_prompt_hash",
        "verifier_prompt_hash",
        "provider_id",
        "model_id",
    )
    if any(getattr(previous, field) != getattr(current, field) for field in immutable):
        raise ValueError("checkpoint binding is immutable")
    if current.tool_attempts_used < previous.tool_attempts_used:
        raise ValueError("tool attempts are monotonic")
    if not set(previous.reservation_ids).issubset(current.reservation_ids):
        raise ValueError("reservation IDs are monotonic")
    if not set(previous.reconstruction_generations).issubset(
        current.reconstruction_generations
    ):
        raise ValueError("reconstruction generations are monotonic")
    if previous.recovery_attempted and not current.recovery_attempted:
        raise ValueError("recovery attempt is monotonic")
    if (
        previous.recovery_attempted
        and current.stage is CheckpointStage.RECOVERY_COMPLETED
    ):
        raise ValueError("recovery may not occur twice")
    if (
        current.stage is CheckpointStage.RECOVERY_COMPLETED
        and not current.recovery_attempted
    ):
        raise ValueError("recovery_completed requires recovery_attempted")


__all__ = [
    "CheckpointStage",
    "EvidenceCheckpointRef",
    "LEGAL_TRANSITIONS",
    "RunCheckpoint",
    "StageGeneration",
    "validate_transition",
]
