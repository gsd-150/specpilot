"""Project answer, gate, and verifier results onto durable terminal states."""

from __future__ import annotations

from dataclasses import dataclass

from specpilot.answer.run import AnswerOutcome
from specpilot.contracts.answer import AnswerVerdict
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import LedgerError
from specpilot.providers.transport import NoAdapterForRoute
from specpilot.runs.contracts import RunStatus, TerminalReason


@dataclass(frozen=True, slots=True)
class Terminal:
    """One closed run state with only its stable public reason."""

    status: RunStatus
    reason: TerminalReason | None


def project_answer_outcome(outcome: AnswerOutcome) -> Terminal:
    """Keep provider health distinct from the answer verifier's decision."""
    if outcome.provider_error is not None:
        return Terminal(RunStatus.FAILED, outcome.provider_error)
    verified = outcome.verified
    if verified.verdict is AnswerVerdict.ANSWERED:
        return Terminal(RunStatus.ANSWERED, None)
    if verified.refusal_reason is None:
        raise ValueError("refused_outcome_missing_reason")
    return Terminal(RunStatus.REFUSED, verified.refusal_reason.value)


def project_gate_error(error: BaseException) -> Terminal:
    """Project only fail-closed admission errors, never transport failures."""
    if isinstance(error, NoAdapterForRoute) or not isinstance(
        error, (EgressPolicyViolation, LedgerError)
    ):
        raise TypeError("not_gate_error")
    return Terminal(RunStatus.EGRESS_BLOCKED, error.code)


__all__ = ["Terminal", "project_answer_outcome", "project_gate_error"]
