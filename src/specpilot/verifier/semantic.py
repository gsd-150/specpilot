"""Ledger-bound semantic support verification after local evidence checks."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from specpilot.answer.evidence import Evidence
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    L2AtomicClaimPayload,
    TaskLevel,
    VersionMetadata,
)
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    RfcSourceManifest,
    SourceManifest,
)
from specpilot.contracts.verdict import IdentifiedCandidate, SemanticDecision
from specpilot.egress.ledger import RequestSize
from specpilot.providers.transport import PolicyBoundTransport
from specpilot.verifier.deterministic import DeterministicResult


class DeterministicVerificationRequired(Exception):
    """Semantic transport is forbidden without non-empty verified evidence."""

    def __init__(self) -> None:
        super().__init__("deterministic_verification_required")


class InvalidSemanticReply(Exception):
    """The semantic response failed its closed contract or evidence binding."""

    __slots__ = ("replayed", "request_size", "reservation_id")

    def __init__(
        self,
        *,
        reservation_id: str,
        replayed: bool,
        request_size: RequestSize,
    ) -> None:
        self.reservation_id = reservation_id
        self.replayed = replayed
        self.request_size = request_size
        super().__init__("invalid_semantic_reply")


@dataclass(frozen=True, slots=True)
class SemanticContext:
    source_manifest: SourceManifest | RfcSourceManifest
    corpus_manifest_id: str
    evaluation_root_id: str
    run_id: str
    model_id: str
    idempotency_key: str
    reconstruction_generation: int


@dataclass(frozen=True, slots=True)
class SemanticOutcome:
    decision: SemanticDecision
    reservation_id: str
    replayed: bool
    request_size: RequestSize


class SemanticVerifier:
    def __init__(self, transport: PolicyBoundTransport) -> None:
        self._transport = transport

    async def verify(
        self,
        candidate: IdentifiedCandidate,
        evidence: tuple[Evidence, ...],
        deterministic: DeterministicResult,
        context: SemanticContext,
    ) -> SemanticOutcome:
        if not deterministic.passed:
            raise DeterministicVerificationRequired()
        evidence_by_id = {item.excerpt.content_hash: item for item in evidence}
        citation_ids = {citation.content_hash for citation in deterministic.citations}
        selected_ids = tuple(candidate.candidate.evidence_ids)
        if not selected_ids or set(selected_ids) != citation_ids:
            raise DeterministicVerificationRequired()
        try:
            selected = tuple(evidence_by_id[item] for item in selected_ids)
        except KeyError:
            raise DeterministicVerificationRequired() from None
        version = _version(context)
        payload = L2AtomicClaimPayload(
            atomic_claim_id=candidate.claim_id,
            atomic_claim=candidate.candidate.claim,
            version=version,
            evidence_excerpts=tuple(item.excerpt for item in selected),
        )
        decision, reservation_id, replayed, request_size = await _send_and_parse(
            self._transport,
            EgressRequest(
                evaluation_root_id=context.evaluation_root_id,
                run_id=context.run_id,
                task_level=TaskLevel.L2,
                version=version,
                stage=EgressStage.VERIFIER,
                route=_authorized_route(context.source_manifest),
                model_id=context.model_id,
                source_manifest=context.source_manifest,
                payload=payload,
            ),
            _stage_key(context, "verifier"),
            set(selected_ids),
        )
        if decision is None:
            error = InvalidSemanticReply(
                reservation_id=reservation_id,
                replayed=replayed,
                request_size=request_size,
            )
            raise error
        return SemanticOutcome(
            decision=decision,
            reservation_id=reservation_id,
            replayed=replayed,
            request_size=request_size,
        )


async def _send_and_parse(
    transport: PolicyBoundTransport,
    request: EgressRequest,
    idempotency_key: str,
    expected_evidence_ids: set[str],
) -> tuple[SemanticDecision | None, str, bool, RequestSize]:
    receipt = await transport.send(request, idempotency_key=idempotency_key)
    reservation_id = receipt.reservation_id
    replayed = receipt.replayed
    request_size = receipt.request_size
    decision = _parse_content(receipt.response.content, expected_evidence_ids)
    del receipt
    return decision, reservation_id, replayed, request_size


def _parse_content(
    content: str,
    expected_evidence_ids: set[str],
) -> SemanticDecision | None:
    try:
        decision = SemanticDecision.model_validate_json(content)
    except (ValidationError, ValueError):
        decision = None
    if decision is None:
        return None
    response_evidence_ids = {item.evidence_id for item in decision.evidence}
    if response_evidence_ids != expected_evidence_ids:
        del decision
        del response_evidence_ids
        return None
    return decision


def _version(context: SemanticContext) -> VersionMetadata:
    source = context.source_manifest
    return VersionMetadata(
        source_manifest_id=source.manifest_id,
        corpus_manifest_id=context.corpus_manifest_id,
        document_id=source.document_id,
        document_version=source.document_version,
    )


def _authorized_route(
    source_manifest: SourceManifest | RfcSourceManifest,
) -> ProviderRouteBinding:
    route = source_manifest.provider_route_binding
    if route is None:
        raise ValueError("source manifest carries no authorized provider route")
    return route


def _stage_key(context: SemanticContext, stage: str) -> str:
    return (
        f"{context.run_id}-{stage}-{context.idempotency_key}"
        f"-g{context.reconstruction_generation}"
    )


__all__ = [
    "DeterministicVerificationRequired",
    "InvalidSemanticReply",
    "SemanticContext",
    "SemanticOutcome",
    "SemanticVerifier",
]
