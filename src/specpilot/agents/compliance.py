"""Ledger-bound candidate generation for the first L2 model stage.

The provider may propose claims, but all provider prose stays in memory.  This
stage returns the parsed, still-untrusted candidates only so deterministic
verification can reject an undisclosed handle before semantic review.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from specpilot.answer.evidence import Evidence
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    L2DesignPayload,
    TaskLevel,
    VersionMetadata,
)
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    RfcSourceManifest,
    SourceManifest,
)
from specpilot.contracts.verdict import (
    ComplianceBatch,
    IdentifiedCandidate,
    normalized_claim_id,
)
from specpilot.egress.ledger import RequestSize
from specpilot.providers.cache import CacheLinkage
from specpilot.providers.transport import PolicyBoundTransport, TransportReceipt

_MAX_L2_EXCERPTS = 12


class InvalidComplianceReply(Exception):
    """The provider response cannot become an untrusted candidate batch."""

    __slots__ = (
        "cache_hit",
        "cache_record_hash",
        "cache_request_hash",
        "raw_reply",
        "replayed",
        "request_size",
        "reservation_id",
    )

    def __init__(
        self,
        *,
        reservation_id: str | None,
        replayed: bool,
        request_size: RequestSize,
        cache_hit: bool = False,
        cache_request_hash: str | None = None,
        cache_record_hash: str | None = None,
        raw_reply: str | None = None,
    ) -> None:
        self.reservation_id = reservation_id
        self.replayed = replayed
        self.request_size = request_size
        self.cache_hit = cache_hit
        self.cache_request_hash = cache_request_hash
        self.cache_record_hash = cache_record_hash
        self.raw_reply = raw_reply
        super().__init__("invalid_compliance_reply")


@dataclass(frozen=True, slots=True)
class ComplianceContext:
    source_manifest: SourceManifest | RfcSourceManifest
    corpus_manifest_id: str
    evaluation_root_id: str
    run_id: str
    model_id: str
    idempotency_key: str
    reconstruction_generation: int
    cache_linkage: CacheLinkage | None = None


@dataclass(frozen=True, slots=True)
class ComplianceOutcome:
    batch: ComplianceBatch
    candidates: tuple[IdentifiedCandidate, ...]
    reservation_id: str | None
    replayed: bool
    request_size: RequestSize
    cache_hit: bool = False
    cache_request_hash: str | None = None
    cache_record_hash: str | None = None


class ComplianceAgent:
    def __init__(self, transport: PolicyBoundTransport) -> None:
        self._transport = transport

    async def evaluate(
        self,
        description: str,
        evidence: tuple[Evidence, ...],
        context: ComplianceContext,
    ) -> ComplianceOutcome:
        version = _version(context)
        payload = L2DesignPayload(
            design_description=description,
            version=version,
            evidence_excerpts=tuple(
                item.excerpt for item in _bounded_unique_evidence(evidence)
            ),
        )
        batch, receipt = await _send_and_parse(
            self._transport,
            EgressRequest(
                evaluation_root_id=context.evaluation_root_id,
                run_id=context.run_id,
                task_level=TaskLevel.L2,
                version=version,
                stage=EgressStage.COMPLIANCE,
                route=_authorized_route(context.source_manifest),
                model_id=context.model_id,
                source_manifest=context.source_manifest,
                payload=payload,
            ),
            _stage_key(context, "compliance"),
            context.cache_linkage,
        )
        if batch is None:
            error = InvalidComplianceReply(
                reservation_id=receipt.reservation_id,
                replayed=receipt.replayed,
                request_size=receipt.request_size,
                cache_hit=receipt.cache_hit,
                cache_request_hash=receipt.cache_request_hash,
                cache_record_hash=receipt.cache_record_hash,
                # Bounded and only ever projected into the author's restricted
                # outcome artifact: the raw bytes are the only way to fix a
                # reply contract from the data instead of guessing.
                raw_reply=receipt.response.content[:16384],
            )
            raise error
        return ComplianceOutcome(
            batch=batch,
            candidates=tuple(
                IdentifiedCandidate(
                    claim_id=normalized_claim_id(candidate.claim), candidate=candidate
                )
                for candidate in batch.candidates
            ),
            reservation_id=receipt.reservation_id,
            replayed=receipt.replayed,
            request_size=receipt.request_size,
            cache_hit=receipt.cache_hit,
            cache_request_hash=receipt.cache_request_hash,
            cache_record_hash=receipt.cache_record_hash,
        )


async def _send_and_parse(
    transport: PolicyBoundTransport,
    request: EgressRequest,
    idempotency_key: str,
    cache_linkage: CacheLinkage | None,
) -> tuple[ComplianceBatch | None, TransportReceipt]:
    receipt = await transport.send(
        request, idempotency_key=idempotency_key, cache_linkage=cache_linkage
    )
    batch = _parse_content(receipt.response.content)
    return batch, receipt


def _parse_content(content: str) -> ComplianceBatch | None:
    try:
        batch = ComplianceBatch.model_validate_json(content)
    except (ValidationError, ValueError):
        batch = None
    return batch


def _bounded_unique_evidence(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    selected: list[Evidence] = []
    seen: set[str] = set()
    for item in evidence:
        evidence_id = item.excerpt.content_hash
        if evidence_id in seen:
            continue
        selected.append(item)
        seen.add(evidence_id)
        if len(selected) == _MAX_L2_EXCERPTS:
            break
    return tuple(selected)


def _version(context: ComplianceContext) -> VersionMetadata:
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


def _stage_key(context: ComplianceContext, stage: str) -> str:
    return (
        f"{context.run_id}-{stage}-{context.idempotency_key}"
        f"-g{context.reconstruction_generation}"
    )


__all__ = [
    "ComplianceAgent",
    "ComplianceContext",
    "ComplianceOutcome",
    "InvalidComplianceReply",
]
