"""Drive one L1 question through the gate and back.

Separated from the CLI handler so the order can be tested without a terminal,
and separated from `chain.py` so the pure assembly stays free of I/O. What lives
here is the part with consequences: a ledger write, a network send, and the
decision about what happens when the two disagree.

The one rule this file exists to hold: **a reservation is spent whether or not
the send succeeds.** A failed attempt is recorded against it rather than
silently released, because a budget that refunds itself on error is a budget an
error loop can walk straight through.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from specpilot.answer.chain import build_request, verify_response
from specpilot.answer.evidence import Evidence
from specpilot.contracts.answer import AnswerVerdict, RefusalReason, VerifiedAnswer
from specpilot.contracts.manifests import RfcSourceManifest, SourceManifest
from specpilot.egress.ledger import RequestSize
from specpilot.providers.transport import PolicyBoundTransport, ProviderAttemptError


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    verified: VerifiedAnswer
    reservation_id: str | None
    replayed: bool
    request_size: RequestSize | None
    provider_error: str | None
    parse_fault: str | None


async def run_answer(
    question: str,
    evidence: Sequence[Evidence],
    *,
    transport: PolicyBoundTransport,
    model_id: str,
    source_manifest: SourceManifest | RfcSourceManifest,
    corpus_manifest_id: str,
    evaluation_root_id: str,
    run_id: str,
    idempotency_key: str | None = None,
) -> AnswerOutcome:
    """Reserve, send, record, verify — and refuse rather than raise.

    The transport owns the enforcer, ledger, and adapter as one outward
    boundary. This caller cannot price, reserve, or send those pieces
    separately, so every provider attempt retains the same policy sequence.
    """
    if not evidence:
        # No send at all. Reserving budget to disclose nothing would spend a
        # cap on a request that cannot be answered from anything.
        return AnswerOutcome(
            verified=VerifiedAnswer(
                verdict=AnswerVerdict.REFUSED,
                refusal_reason=RefusalReason.NO_EVIDENCE_RETRIEVED,
            ),
            reservation_id=None,
            replayed=False,
            request_size=None,
            provider_error=None,
            parse_fault=None,
        )

    request = build_request(
        question,
        evidence,
        source_manifest=source_manifest,
        corpus_manifest_id=corpus_manifest_id,
        model_id=model_id,
        evaluation_root_id=evaluation_root_id,
        run_id=run_id,
    )
    try:
        receipt = await transport.send(
            request,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
    except ProviderAttemptError as error:
        return AnswerOutcome(
            verified=VerifiedAnswer(
                verdict=AnswerVerdict.REFUSED,
                refusal_reason=RefusalReason.EVIDENCE_INSUFFICIENT,
            ),
            reservation_id=error.reservation_id,
            replayed=error.replayed,
            request_size=None,
            provider_error=error.public_error_code,
            parse_fault=None,
        )

    verified, parse_fault = verify_response(
        receipt.response, evidence, corpus_manifest_id=corpus_manifest_id
    )
    return AnswerOutcome(
        verified=verified,
        reservation_id=receipt.reservation_id,
        replayed=receipt.replayed,
        request_size=receipt.request_size,
        provider_error=None,
        parse_fault=parse_fault,
    )


__all__ = ["AnswerOutcome", "run_answer"]
