"""The online L1 path, end to end, with the gate in the middle.

Question in, verified answer or a refusal out. Every step already existed; what
did not exist was a caller that runs them in the one order that keeps the claim
true:

    retrieve → build evidence → reserve against the ledger → send → verify

The order is load-bearing in two places. The reservation happens *before* the
send, because a budget check after the bytes have left is a report rather than
a control. And verification happens *after* the reply, against the evidence
this request actually disclosed, because that is the only comparison that
catches a model citing a real clause it was never shown.

Nothing here reaches a provider except through `PolicyBoundTransport`'s
successor, the ledger-backed reservation — ADR 0001's rule that the enforcer is
the only path outward is what makes the disclosure caps mean anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from specpilot.answer.evidence import Evidence, build_evidence_set
from specpilot.answer.reply import parse_reply
from specpilot.answer.verify import verify_answer
from specpilot.contracts.answer import AnswerVerdict, RefusalReason, VerifiedAnswer
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    L1OnlinePayload,
    TaskLevel,
    VersionMetadata,
)
from specpilot.contracts.manifests import RfcSourceManifest
from specpilot.corpus.clauses import Clause
from specpilot.egress.ledger import TransmittedUsage
from specpilot.providers.base import ProviderResponse


@dataclass(frozen=True, slots=True)
class AnswerRun:
    """One completed pass, including what it cost and what it disclosed."""

    verified: VerifiedAnswer
    evidence_count: int
    reservation_id: str | None
    transmitted: TransmittedUsage | None
    parse_fault: str | None
    retrieved_clause_ids: tuple[str, ...]


def build_request(
    question: str,
    evidence: Sequence[Evidence],
    *,
    source_manifest: RfcSourceManifest,
    corpus_manifest_id: str,
    model_id: str,
    evaluation_root_id: str,
    run_id: str,
) -> EgressRequest:
    """Assemble the one request this question is allowed to send.

    `stage` is `EVIDENCE` and never `JUDGE`: the caps are scoped per stage, and
    a question priced against the judge's budget would spend an allowance
    reserved for scoring.

    A manifest with no authorized route raises rather than sending. §3.2 makes
    source manifests default-deny, so the absence of a route binding is a
    compliance decision that was never made, not a field that was forgotten.
    """
    if source_manifest.provider_route_binding is None:
        raise ValueError("source manifest carries no authorized provider route")
    return EgressRequest(
        evaluation_root_id=evaluation_root_id,
        run_id=run_id,
        task_level=TaskLevel.L1,
        version=VersionMetadata(
            source_manifest_id=source_manifest.manifest_id,
            corpus_manifest_id=corpus_manifest_id,
            document_id=source_manifest.document_id,
            document_version=source_manifest.document_version,
        ),
        stage=EgressStage.EVIDENCE,
        route=source_manifest.provider_route_binding,
        model_id=model_id,
        source_manifest=source_manifest,
        payload=L1OnlinePayload(
            query=question,
            version=VersionMetadata(
                source_manifest_id=source_manifest.manifest_id,
                corpus_manifest_id=corpus_manifest_id,
                document_id=source_manifest.document_id,
                document_version=source_manifest.document_version,
            ),
            evidence_excerpts=tuple(item.excerpt for item in evidence),
        ),
    )


def evidence_for(
    ranked: Sequence[tuple[Clause, str]], *, corpus_manifest_id: str
) -> tuple[Evidence, ...]:
    return build_evidence_set(ranked, corpus_manifest_id=corpus_manifest_id)


def verify_response(
    response: ProviderResponse,
    evidence: Sequence[Evidence],
    *,
    corpus_manifest_id: str,
) -> tuple[VerifiedAnswer, str | None]:
    """Read the reply, then check it against what this request disclosed.

    A parse fault becomes a refusal with `evidence_insufficient` rather than a
    citation fault: nothing was cited badly, the reply simply could not be read,
    and filing it under unverifiable citations would inflate a count that is
    supposed to mean the model cited something it should not have.
    """
    parsed = parse_reply(response.content)
    if not parsed.usable:
        return (
            VerifiedAnswer(
                verdict=AnswerVerdict.REFUSED,
                refusal_reason=RefusalReason.EVIDENCE_INSUFFICIENT,
                citation_faults=(parsed.parse_fault or "reply_unreadable",),
            ),
            parsed.parse_fault,
        )
    return (
        verify_answer(
            answer_text=parsed.answer,
            claimed_citations=parsed.citations,
            disclosed=[item.disclosed for item in evidence],
            corpus_manifest_id=corpus_manifest_id,
            model_refused=not parsed.sufficient,
        ),
        None,
    )


__all__ = ["AnswerRun", "build_request", "evidence_for", "verify_response"]
