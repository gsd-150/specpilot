from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from specpilot.contracts.verdict import (
    ComplianceBatch,
    ComplianceCandidate,
    ComplianceResult,
    IdentifiedCandidate,
    SemanticDecision,
    SemanticEvidenceDecision,
    normalized_claim_id,
)


def candidate(
    verdict: str = "compliant", evidence_ids: tuple[str, ...] = ("a" * 64,)
) -> dict[str, object]:
    return {
        "claim": "A sender always emits the field.",
        "proposed_verdict": verdict,
        "evidence_ids": evidence_ids,
        "rationale": "The candidate is not yet verified.",
    }


def test_compliance_batch_rejects_a_fourth_atomic_claim() -> None:
    with pytest.raises(ValidationError, match="at most 3"):
        ComplianceBatch(candidates=tuple(candidate() for _ in range(4)))


def test_compliance_batch_requires_at_least_one_atomic_claim() -> None:
    with pytest.raises(ValidationError):
        ComplianceBatch(candidates=())


def test_determinate_candidate_requires_evidence() -> None:
    with pytest.raises(
        ValidationError, match="determinate candidate requires evidence"
    ):
        ComplianceCandidate.model_validate(candidate(evidence_ids=()))


def test_insufficient_candidate_cannot_claim_evidence() -> None:
    with pytest.raises(ValidationError, match="insufficient candidate has no evidence"):
        ComplianceCandidate.model_validate(candidate("insufficient_evidence"))


def test_determinate_result_requires_semantic_support_and_citations() -> None:
    with pytest.raises(
        ValidationError, match="determinate result requires verified support"
    ):
        ComplianceResult(
            claim_id="a" * 64,
            verdict="violating",
            verification_status="semantic_failed",
            citations=(),
            reason_code="unsupported",
        )


def test_normalized_claims_have_stable_ids_and_duplicates_are_rejected() -> None:
    claim = "A sender always emits the field."

    assert normalized_claim_id(f"  {claim}  ") == hashlib.sha256(
        claim.encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValidationError, match="duplicate normalized claim"):
        ComplianceBatch(candidates=(candidate(), candidate()))


def test_identified_candidate_requires_the_derived_claim_id() -> None:
    parsed = ComplianceCandidate.model_validate(candidate())
    claim_id = normalized_claim_id(parsed.claim)

    assert IdentifiedCandidate(claim_id=claim_id, candidate=parsed).claim_id == claim_id
    with pytest.raises(ValidationError, match="claim_id must match normalized claim"):
        IdentifiedCandidate(claim_id="b" * 64, candidate=parsed)


def test_candidate_evidence_ids_are_unique_full_hashes_and_limited_to_four() -> None:
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        ComplianceCandidate.model_validate(candidate(evidence_ids=("a" * 64,) * 2))
    with pytest.raises(ValidationError):
        ComplianceCandidate.model_validate(candidate(evidence_ids=("a" * 63,)))
    with pytest.raises(ValidationError):
        ComplianceCandidate.model_validate(
            candidate(evidence_ids=tuple(f"{number:064x}" for number in range(5)))
        )


def test_semantic_evidence_ids_are_unique_and_limited_to_four() -> None:
    with pytest.raises(ValidationError, match="semantic evidence IDs must be unique"):
        SemanticDecision(
            supports_verdict=True,
            evidence=(
                SemanticEvidenceDecision(evidence_id="a" * 64, supports=True),
                SemanticEvidenceDecision(evidence_id="a" * 64, supports=True),
            ),
            reason="supported",
            rationale="The excerpt supports it.",
        )
    with pytest.raises(ValidationError):
        SemanticDecision(
            supports_verdict=True,
            evidence=tuple(
                SemanticEvidenceDecision(evidence_id=f"{number:064x}", supports=True)
                for number in range(5)
            ),
            reason="supported",
            rationale="The excerpts support it.",
        )


def test_unsupported_semantic_decision_cannot_name_supported_reason() -> None:
    with pytest.raises(
        ValidationError, match="unsupported decision has non-supported reason"
    ):
        SemanticDecision(
            supports_verdict=False,
            evidence=(SemanticEvidenceDecision(evidence_id="a" * 64, supports=False),),
            reason="supported",
            rationale="The excerpt does not support it.",
        )


def test_insufficient_result_has_no_citations() -> None:
    with pytest.raises(ValidationError, match="insufficient result has no citations"):
        ComplianceResult(
            claim_id="a" * 64,
            verdict="insufficient_evidence",
            verification_status="insufficient",
            citations=(
                {
                    "clause_id": "b" * 64,
                    "corpus_manifest_id": "c" * 64,
                    "document_id": "rfc-9110",
                    "document_version": "2022-06",
                    "content_hash": "d" * 64,
                },
            ),
        )
