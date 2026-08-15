"""Unit coverage for the l2-outcome/v1 artifact contract."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from specpilot.agents.compliance import ComplianceOutcome
from specpilot.contracts.answer import Citation
from specpilot.contracts.verdict import (
    ComplianceBatch,
    ComplianceCandidate,
    ComplianceResult,
    ComplianceVerdict,
    IdentifiedCandidate,
    VerificationStatus,
    normalized_claim_id,
)
from specpilot.egress.ledger import RequestSize
from specpilot.runtime.l2 import L2Outcome
from specpilot.runtime.outcome_capture import (
    L2_OUTCOME_SCHEMA_VERSION,
    build_l2_outcome,
    validate_l2_outcome,
    write_l2_outcome,
)


def _candidate(
    claim: str = "The design satisfies the cited requirement.",
    *,
    verdict: ComplianceVerdict = ComplianceVerdict.COMPLIANT,
) -> IdentifiedCandidate:
    proposed = ComplianceCandidate(
        claim=claim,
        proposed_verdict=verdict,
        evidence_ids=("a" * 64,),
        rationale="The cited excerpt states the requirement.",
    )
    return IdentifiedCandidate(
        claim_id=normalized_claim_id(proposed.claim), candidate=proposed
    )


def _citation() -> Citation:
    return Citation(
        clause_id="b" * 64,
        corpus_manifest_id="c" * 64,
        document_id="ietf-rfc-9999",
        document_version="2026-08",
        section_number="1",
        content_hash="a" * 64,
    )


def _outcome(
    *,
    provider_error: str | None = None,
    parse_fault: str | None = None,
    egress_error: str | None = None,
) -> L2Outcome:
    if (
        provider_error is not None
        or parse_fault is not None
        or egress_error is not None
    ):
        return L2Outcome(
            results=(),
            reservation_ids=(),
            tool_attempts_used=0,
            recovery_attempted=False,
            provider_error=provider_error,
            parse_fault=parse_fault,
            egress_error=egress_error,
        )
    identified = _candidate()
    compliance = ComplianceOutcome(
        batch=ComplianceBatch(candidates=(identified.candidate,)),
        candidates=(identified,),
        reservation_id="00000000-0000-0000-0000-000000000002",
        replayed=False,
        request_size=RequestSize(request_tokens=1, request_bytes=1),
    )
    result = ComplianceResult(
        claim_id=identified.claim_id,
        verdict=ComplianceVerdict.COMPLIANT,
        verification_status=VerificationStatus.VERIFIED,
        citations=(_citation(), _citation()),
    )
    return L2Outcome(
        results=(result,),
        reservation_ids=("00000000-0000-0000-0000-000000000002",),
        tool_attempts_used=1,
        recovery_attempted=False,
        provider_error=None,
        parse_fault=None,
        compliance=compliance,
    )


def test_build_l2_outcome_projects_candidates_and_prose_free_results() -> None:
    outcome = _outcome()

    payload = build_l2_outcome("l2-dev-001", "A design description.", outcome)

    assert payload == {
        "schema_version": L2_OUTCOME_SCHEMA_VERSION,
        "case_id": "l2-dev-001",
        "design_description": "A design description.",
        "candidates": [
            {
                "claim_id": payload["candidates"][0]["claim_id"],
                "claim": "The design satisfies the cited requirement.",
                "proposed_verdict": "compliant",
                "rationale": "The cited excerpt states the requirement.",
                "evidence_ids": payload["candidates"][0]["evidence_ids"],
            }
        ],
        "results": [
            {
                "claim_id": payload["results"][0]["claim_id"],
                "verdict": "compliant",
                "verification_status": "verified",
                "citation_count": 2,
            }
        ],
        "evidence": [],
        "search_scopes": [],
        "provider_error": None,
        "parse_fault": None,
    }
    validate_l2_outcome(payload)


def test_build_l2_outcome_preserves_insufficient_verdict() -> None:
    claim = "No evidence supports this design."
    identified = IdentifiedCandidate(
        claim_id=normalized_claim_id(claim),
        candidate=ComplianceCandidate(
            claim=claim,
            proposed_verdict=ComplianceVerdict.INSUFFICIENT_EVIDENCE,
            rationale="Nothing in scope states it.",
        ),
    )
    compliance = ComplianceOutcome(
        batch=ComplianceBatch(candidates=(identified.candidate,)),
        candidates=(identified,),
        reservation_id="00000000-0000-0000-0000-000000000002",
        replayed=False,
        request_size=RequestSize(request_tokens=1, request_bytes=1),
    )
    outcome = L2Outcome(
        results=(
            ComplianceResult(
                claim_id=identified.claim_id,
                verdict=ComplianceVerdict.INSUFFICIENT_EVIDENCE,
                verification_status=VerificationStatus.INSUFFICIENT,
                reason_code="proposed_insufficient",
            ),
        ),
        reservation_ids=(),
        tool_attempts_used=0,
        recovery_attempted=False,
        provider_error=None,
        parse_fault=None,
        compliance=compliance,
    )

    payload = build_l2_outcome("l2-dev-002", "Another design.", outcome)

    assert payload["results"] == [
        {
            "claim_id": identified.claim_id,
            "verdict": "insufficient_evidence",
            "verification_status": "insufficient",
            "citation_count": 0,
        }
    ]


def test_build_l2_outcome_projects_provider_failure_with_empty_lists() -> None:
    outcome = _outcome(provider_error="provider_timeout")

    payload = build_l2_outcome("l2-dev-003", "A design.", outcome)

    assert payload["candidates"] == []
    assert payload["results"] == []
    assert payload["provider_error"] == "provider_timeout"
    assert payload["parse_fault"] is None
    validate_l2_outcome(payload)


def test_build_l2_outcome_projects_parse_fault_with_empty_lists() -> None:
    outcome = _outcome(parse_fault="invalid_compliance_reply")

    payload = build_l2_outcome("l2-dev-004", "A design.", outcome)

    assert payload["candidates"] == []
    assert payload["results"] == []
    assert payload["provider_error"] is None
    assert payload["parse_fault"] == "invalid_compliance_reply"
    validate_l2_outcome(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "wrong/v1"},
        {"case_id": "../escape"},
        {"results": [{"claim_id": "not-hex", "verdict": "compliant",
                      "verification_status": "verified", "citation_count": 0}]},
        {"candidates": [{"claim_id": "a" * 64, "claim": "x",
                         "proposed_verdict": "maybe", "rationale": "y"}]},
        {"provider_error": 3},
    ],
)
def test_validate_l2_outcome_refuses_malformed_payloads(
    mutation: dict[str, object],
) -> None:
    base = build_l2_outcome("l2-dev-005", "A design.", _outcome())
    base.update(mutation)

    with pytest.raises(ValueError):
        validate_l2_outcome(base)


def test_write_l2_outcome_enforces_private_permissions(tmp_path: Path) -> None:
    out_dir = tmp_path / "nested" / "outcomes"
    payload = build_l2_outcome("l2-dev-006", "A design.", _outcome())

    path = write_l2_outcome(out_dir, "l2-dev-006", payload)

    assert path == out_dir / "l2-dev-006.json"
    assert stat.S_IMODE(os.stat(out_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == payload
