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

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from specpilot.answer.chain import build_request, verify_response
from specpilot.answer.evidence import Evidence
from specpilot.contracts.answer import AnswerVerdict, RefusalReason, VerifiedAnswer
from specpilot.contracts.manifests import RfcSourceManifest, SourceManifest
from specpilot.egress.ledger import AttemptOutcome, RequestSize
from specpilot.providers.base import ProviderError


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
    enforcer: object,
    ledger: object,
    adapter: object,
    source_manifest: SourceManifest | RfcSourceManifest,
    corpus_manifest_id: str,
    evaluation_root_id: str,
    run_id: str,
    idempotency_key: str | None = None,
) -> AnswerOutcome:
    """Reserve, send, record, verify — and refuse rather than raise.

    The enforcer prices the request and the ledger commits it. They are passed
    separately because they are separate authorities: the enforcer decides what
    a request would cost, and only the ledger's transaction decides whether the
    budget is there. Collapsing them would let a caller price a request and act
    on the answer without ever committing it.
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
        model_id=adapter.endpoint.model_id,  # type: ignore[attr-defined]
        evaluation_root_id=evaluation_root_id,
        run_id=run_id,
    )
    counter = adapter.token_counter  # type: ignore[attr-defined]
    reservation = await ledger.check_and_reserve(  # type: ignore[attr-defined]
        enforcer.prepare(request, counter),  # type: ignore[attr-defined]
        counter,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )

    started = time.monotonic()
    try:
        response = await adapter.send(request.payload)  # type: ignore[attr-defined]
    except ProviderError as error:
        elapsed = int((time.monotonic() - started) * 1000)
        # Recorded, not released. A budget that refunds itself on error is a
        # budget a retry loop walks straight through.
        await ledger.record_attempt(  # type: ignore[attr-defined]
            reservation.reservation_id,
            request.route,
            RequestSize(request_tokens=0, request_bytes=0),
            AttemptOutcome.FAILED_KNOWN,
            duration_ms=elapsed,
            public_error_code=error.public_error_code,
        )
        return AnswerOutcome(
            verified=VerifiedAnswer(
                verdict=AnswerVerdict.REFUSED,
                refusal_reason=RefusalReason.EVIDENCE_INSUFFICIENT,
                citation_faults=(error.public_error_code,),
            ),
            reservation_id=reservation.reservation_id,
            replayed=reservation.replayed,
            request_size=None,
            provider_error=error.public_error_code,
            parse_fault=None,
        )

    request_size = RequestSize(
        request_tokens=response.metadata.prompt_tokens,
        request_bytes=response.metadata.request_bytes,
    )
    await ledger.record_attempt(  # type: ignore[attr-defined]
        reservation.reservation_id,
        request.route,
        request_size,
        AttemptOutcome.SUCCEEDED,
        duration_ms=response.metadata.duration_ms,
    )

    verified, parse_fault = verify_response(
        response, evidence, corpus_manifest_id=corpus_manifest_id
    )
    return AnswerOutcome(
        verified=verified,
        reservation_id=reservation.reservation_id,
        replayed=reservation.replayed,
        request_size=request_size,
        provider_error=None,
        parse_fault=parse_fault,
    )


__all__ = ["AnswerOutcome", "run_answer"]
