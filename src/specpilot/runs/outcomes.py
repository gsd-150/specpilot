"""Project answer, gate, and verifier results onto durable terminal states."""

from __future__ import annotations

from dataclasses import dataclass

from specpilot.answer.run import AnswerOutcome
from specpilot.contracts.answer import AnswerVerdict
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.runs.contracts import RunStatus, TerminalReason

# These are the stable failures produced by the policy enforcer before a send.
# Ledger/accounting errors and unknown future codes deliberately stay out: the
# worker cannot infer from their spelling whether bytes already left the host.
_ADMISSION_CODES = frozenset(
    {
        "authorization_clock_invalid",
        "claim_count_exceeded",
        "claim_payload_mismatch",
        "claim_unique_excerpts_exceeded",
        "claim_unique_tokens_exceeded",
        "claim_unique_bytes_exceeded",
        "corpus_document_cap_missing",
        "corpus_document_unique_excerpts_exceeded",
        "corpus_document_unique_tokens_exceeded",
        "corpus_document_unique_bytes_exceeded",
        "corpus_manifest_mismatch",
        "corpus_usage_mismatch",
        "corpus_unique_excerpts_exceeded",
        "corpus_unique_tokens_exceeded",
        "corpus_unique_bytes_exceeded",
        "disclosure_fact_mismatch",
        "document_id_mismatch",
        "document_version_mismatch",
        "evaluation_root_mismatch",
        "excerpt_bytes_exceeded",
        "excerpt_tokens_exceeded",
        "judge_unique_excerpts_exceeded",
        "judge_unique_tokens_exceeded",
        "judge_unique_bytes_exceeded",
        "judge_transmitted_tokens_exceeded",
        "judge_transmitted_bytes_exceeded",
        "online_unique_excerpts_exceeded",
        "online_unique_tokens_exceeded",
        "online_unique_bytes_exceeded",
        "online_transmitted_tokens_exceeded",
        "online_transmitted_bytes_exceeded",
        "payload_version_mismatch",
        "policy_snapshot_mismatch",
        "projected_text_tokens_exceeded",
        "reservation_accounting_mismatch",
        "reservation_primitive_invalid",
        "root_unique_excerpts_exceeded",
        "root_unique_tokens_exceeded",
        "root_unique_bytes_exceeded",
        "root_transmitted_tokens_exceeded",
        "root_transmitted_bytes_exceeded",
        "route_unauthorized",
        "source_manifest_mismatch",
        "source_manifest_unresolvable",
        "source_manifest_untrusted",
        "stage_payload_mismatch",
        "stage_route_mismatch",
        "task_level_mismatch",
        "toc_call_exceeded",
        "toc_run_exceeded",
        "token_accounting_unavailable",
        "token_counter_incompatible",
    }
)


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
    if (
        not isinstance(error, EgressPolicyViolation)
        or error.code not in _ADMISSION_CODES
    ):
        raise TypeError("not_gate_error")
    return Terminal(RunStatus.EGRESS_BLOCKED, error.code)


__all__ = ["Terminal", "project_answer_outcome", "project_gate_error"]
