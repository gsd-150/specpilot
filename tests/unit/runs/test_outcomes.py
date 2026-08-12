from __future__ import annotations

import pytest

from specpilot.answer.run import AnswerOutcome
from specpilot.contracts.answer import (
    AnswerVerdict,
    Citation,
    RefusalReason,
    VerifiedAnswer,
)
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import LedgerError
from specpilot.providers.transport import (
    NoAdapterForRoute,
    ProviderAttemptError,
    TransportReplayError,
)
from specpilot.runs.contracts import RunStatus
from specpilot.runs.outcomes import Terminal, project_answer_outcome, project_gate_error


def _refused(
    reason: RefusalReason = RefusalReason.EVIDENCE_INSUFFICIENT,
    *,
    provider_error: str | None = None,
    parse_fault: str | None = None,
) -> AnswerOutcome:
    return AnswerOutcome(
        verified=VerifiedAnswer(
            verdict=AnswerVerdict.REFUSED,
            refusal_reason=reason,
            citation_faults=("citation_not_disclosed",) if parse_fault else (),
        ),
        reservation_id="reservation-1",
        replayed=False,
        request_size=None,
        provider_error=provider_error,
        parse_fault=parse_fault,
    )


def _answered() -> AnswerOutcome:
    return AnswerOutcome(
        verified=VerifiedAnswer(
            verdict=AnswerVerdict.ANSWERED,
            answer="A verified answer.",
            citations=(
                Citation(
                    clause_id="a" * 64,
                    corpus_manifest_id="b" * 64,
                    document_id="fixture-spec",
                    document_version="1",
                    section_number="1.1",
                    content_hash="c" * 64,
                ),
            ),
        ),
        reservation_id="reservation-1",
        replayed=False,
        request_size=None,
        provider_error=None,
        parse_fault=None,
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (_answered(), Terminal(RunStatus.ANSWERED, None)),
        (
            _refused(RefusalReason.NO_EVIDENCE_RETRIEVED),
            Terminal(RunStatus.REFUSED, "no_evidence_retrieved"),
        ),
        (
            _refused(RefusalReason.EVIDENCE_INSUFFICIENT),
            Terminal(RunStatus.REFUSED, "evidence_insufficient"),
        ),
        (
            _refused(
                RefusalReason.UNVERIFIABLE_CITATION,
                parse_fault="reply_not_json",
            ),
            Terminal(RunStatus.REFUSED, "unverifiable_citation"),
        ),
    ],
)
def test_verdict_projects_without_inventing_provider_failure(
    outcome: AnswerOutcome, expected: Terminal
) -> None:
    assert project_answer_outcome(outcome) == expected


def test_provider_error_wins_over_refusal_and_parser_faults() -> None:
    outcome = _refused(
        RefusalReason.UNVERIFIABLE_CITATION,
        provider_error="provider_timeout",
        parse_fault="reply_not_json",
    )

    assert project_answer_outcome(outcome) == Terminal(
        RunStatus.FAILED, "provider_timeout"
    )


@pytest.mark.parametrize(
    "code",
    [
        "root_unique_excerpts_exceeded",
        "excerpt_bytes_exceeded",
        "policy_snapshot_mismatch",
        "route_unauthorized",
    ],
)
def test_closed_admission_family_is_visible_as_egress_blocked(code: str) -> None:
    error = EgressPolicyViolation(code, "raw question and ledger detail")

    projected = project_gate_error(error)

    assert projected == Terminal(RunStatus.EGRESS_BLOCKED, code)
    assert "raw question" not in repr(projected)


@pytest.mark.parametrize(
    "error",
    [
        LedgerError("ledger_unavailable", "raw accounting detail"),
        LedgerError("reservation_ambiguous", "raw accounting detail"),
        LedgerError("run_sealed", "raw accounting detail"),
        LedgerError("ledger_integrity_error", "raw accounting detail"),
        EgressPolicyViolation("unknown_policy_code", "raw policy detail"),
    ],
)
def test_post_send_accounting_and_unknown_policy_errors_are_not_gate_rejections(
    error: Exception,
) -> None:
    with pytest.raises(TypeError, match="not_gate_error"):
        project_gate_error(error)


@pytest.mark.parametrize(
    "error",
    [
        ProviderAttemptError("provider_timeout", "reservation-1", False, None),
        TransportReplayError("reservation-1"),
        NoAdapterForRoute(),
    ],
)
def test_transport_and_provider_errors_are_not_gate_rejections(
    error: Exception,
) -> None:
    with pytest.raises(TypeError, match="not_gate_error"):
        project_gate_error(error)
