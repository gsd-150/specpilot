"""Closed contracts for the two independently checked L2 model stages.

Compliance may propose atomic candidates, but it cannot publish a conclusion.
The separate semantic stage decides whether the disclosed excerpts support a
candidate.  Only the prose-free ``ComplianceResult`` can cross the durable and
public boundary.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from specpilot.contracts.answer import Citation
from specpilot.contracts.egress import ShortText
from specpilot.contracts.manifests import Identifier, Sha256


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComplianceVerdict(StrEnum):
    COMPLIANT = "compliant"
    VIOLATING = "violating"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    DETERMINISTIC_FAILED = "deterministic_failed"
    SEMANTIC_FAILED = "semantic_failed"
    INSUFFICIENT = "insufficient"


class SemanticReason(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONDITION_MISMATCH = "condition_mismatch"
    EXCEPTION_MISSING = "exception_missing"
    POLARITY_MISMATCH = "polarity_mismatch"


class ComplianceCandidate(_FrozenModel):
    """An untrusted atomic claim proposed by the Compliance model."""

    claim: ShortText
    proposed_verdict: ComplianceVerdict
    evidence_ids: Annotated[tuple[Sha256, ...], Field(max_length=4)] = ()
    rationale: ShortText

    @model_validator(mode="after")
    def _require_evidence_that_matches_the_proposed_verdict(self) -> Self:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence IDs must be unique")
        if self.proposed_verdict is ComplianceVerdict.INSUFFICIENT_EVIDENCE:
            if self.evidence_ids:
                raise ValueError("insufficient candidate has no evidence")
            return self
        if not self.evidence_ids:
            raise ValueError("determinate candidate requires evidence")
        return self


def normalized_claim_id(claim: str) -> str:
    """Name a validated claim by its whitespace-normalized UTF-8 bytes."""
    return hashlib.sha256(claim.strip().encode("utf-8")).hexdigest()


class ComplianceBatch(_FrozenModel):
    candidates: Annotated[
        tuple[ComplianceCandidate, ...], Field(min_length=1, max_length=3)
    ]

    @model_validator(mode="after")
    def _reject_duplicate_normalized_claims(self) -> Self:
        claim_ids = tuple(
            normalized_claim_id(candidate.claim) for candidate in self.candidates
        )
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("duplicate normalized claim")
        return self


class IdentifiedCandidate(_FrozenModel):
    """Local pairing of a server-owned claim ID with its untrusted candidate."""

    claim_id: Sha256
    candidate: ComplianceCandidate


class SemanticEvidenceDecision(_FrozenModel):
    evidence_id: Sha256
    supports: bool


class SemanticDecision(_FrozenModel):
    supports_verdict: bool
    evidence: Annotated[
        tuple[SemanticEvidenceDecision, ...], Field(min_length=1, max_length=4)
    ]
    reason: SemanticReason
    rationale: ShortText

    @model_validator(mode="after")
    def _require_consistent_support_reasoning(self) -> Self:
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("semantic evidence IDs must be unique")
        if not self.supports_verdict and self.reason is SemanticReason.SUPPORTED:
            raise ValueError("unsupported decision has non-supported reason")
        return self


class ComplianceResult(_FrozenModel):
    """The prose-free final metadata suitable for durable publication."""

    claim_id: Sha256
    verdict: ComplianceVerdict
    verification_status: VerificationStatus
    citations: tuple[Citation, ...] = ()
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def _permit_only_verified_determinate_results(self) -> Self:
        if self.verdict is ComplianceVerdict.INSUFFICIENT_EVIDENCE:
            if self.citations:
                raise ValueError("insufficient result has no citations")
            return self
        if (
            self.verification_status is not VerificationStatus.VERIFIED
            or not self.citations
        ):
            raise ValueError("determinate result requires verified support")
        return self


__all__ = [
    "ComplianceBatch",
    "ComplianceCandidate",
    "ComplianceResult",
    "ComplianceVerdict",
    "IdentifiedCandidate",
    "SemanticDecision",
    "SemanticEvidenceDecision",
    "SemanticReason",
    "VerificationStatus",
    "normalized_claim_id",
]
