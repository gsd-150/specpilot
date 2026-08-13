from __future__ import annotations

import hashlib
import json
from types import TracebackType

import pytest

from specpilot.answer.evidence import Evidence, build_evidence_from_unit
from specpilot.contracts.answer import Citation
from specpilot.contracts.verdict import ComplianceCandidate, IdentifiedCandidate
from specpilot.corpus.indexable import IndexUnit
from specpilot.providers.fake import FakeProvider
from specpilot.verifier.deterministic import (
    DeterministicCheck,
    DeterministicFault,
    DeterministicResult,
)
from specpilot.verifier.semantic import (
    DeterministicVerificationRequired,
    InvalidSemanticReply,
    SemanticContext,
    SemanticVerifier,
)
from tests.unit.egress.test_policy_projection import egress_request
from tests.unit.providers.test_transport_fail_closed import StubLedger, transport

pytestmark = pytest.mark.anyio


class CapturingLedger(StubLedger):
    def __init__(self) -> None:
        super().__init__()
        self.reserved_idempotency_keys: list[str] = []

    async def check_and_reserve(self, request, counter, *, idempotency_key):
        self.reserved_idempotency_keys.append(idempotency_key)
        return await super().check_and_reserve(
            request, counter, idempotency_key=idempotency_key
        )


def build_evidence(index: int = 1) -> Evidence:
    text = f"A sender MUST perform stated check {index}."
    unit = IndexUnit(
        unit_id=f"{index:x}" * 64,
        kind="clause",
        document_id="iso-9001",
        document_version="2026-edition",
        section_number=f"7.{index}",
        section_path=f"Requirements > 7.{index}",
        ordinal=index,
        text=text,
        indexed=text,
    )
    return build_evidence_from_unit(unit, corpus_manifest_id="c" * 64)


def identified_candidate(evidence: Evidence) -> IdentifiedCandidate:
    candidate = ComplianceCandidate(
        claim="The design performs the stated check.",
        proposed_verdict="compliant",
        evidence_ids=(evidence.excerpt.content_hash,),
        rationale="The candidate is locally bound before semantic review.",
    )
    return IdentifiedCandidate(
        claim_id=hashlib.sha256(candidate.claim.strip().encode()).hexdigest(),
        candidate=candidate,
    )


def deterministic(evidence: Evidence, *, passed: bool = True) -> DeterministicResult:
    citation = Citation(
        clause_id=evidence.disclosed.clause_id,
        corpus_manifest_id=evidence.disclosed.corpus_manifest_id,
        document_id=evidence.disclosed.document_id,
        document_version=evidence.disclosed.document_version,
        section_number=evidence.disclosed.section_number,
        content_hash=evidence.excerpt.content_hash,
    )
    return DeterministicResult(
        checks=(
            DeterministicCheck(
                evidence_id=evidence.excerpt.content_hash,
                fault=None if passed else DeterministicFault.NOT_DISCLOSED,
            ),
        ),
        citations=(citation,) if passed else (),
    )


def deterministic_for(*evidence: Evidence) -> DeterministicResult:
    return DeterministicResult(
        checks=tuple(
            DeterministicCheck(item.excerpt.content_hash, None) for item in evidence
        ),
        citations=tuple(
            Citation(
                clause_id=item.disclosed.clause_id,
                corpus_manifest_id=item.disclosed.corpus_manifest_id,
                document_id=item.disclosed.document_id,
                document_version=item.disclosed.document_version,
                section_number=item.disclosed.section_number,
                content_hash=item.excerpt.content_hash,
            )
            for item in evidence
        ),
    )


def traceback_strings(traceback: TracebackType | None) -> tuple[str, ...]:
    strings: list[str] = []
    while traceback is not None:
        strings.extend(
            value
            for value in traceback.tb_frame.f_locals.values()
            if isinstance(value, str)
        )
        traceback = traceback.tb_next
    return tuple(strings)


def production_traceback_reprs(traceback: TracebackType | None) -> tuple[str, ...]:
    assert traceback is not None
    traceback = traceback.tb_next
    representations: list[str] = []
    while traceback is not None:
        representations.extend(
            repr(value) for value in traceback.tb_frame.f_locals.values()
        )
        traceback = traceback.tb_next
    return tuple(representations)


def context(*, key: str = "recovery", generation: int = 2) -> SemanticContext:
    request = egress_request()
    return SemanticContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id="l2-case",
        run_id="l2-run",
        model_id=request.model_id,
        idempotency_key=key,
        reconstruction_generation=generation,
    )


async def test_semantic_uses_only_deterministically_verified_evidence() -> None:
    disclosed = build_evidence()
    provider = FakeProvider()
    ledger = CapturingLedger()

    outcome = await SemanticVerifier(transport(provider, ledger)).verify(
        identified_candidate(disclosed),
        (disclosed,),
        deterministic(disclosed),
        context(),
    )

    reserved = ledger.reserved[0]
    assert reserved.task_level.value == "L2"
    assert reserved.stage.value == "verifier"
    assert reserved.projected_payload.kind == "l2_atomic_claim"
    assert tuple(
        excerpt.content_hash for excerpt in reserved.projected_payload.evidence_excerpts
    ) == (disclosed.excerpt.content_hash,)
    assert outcome.decision.supports_verdict is True
    assert provider.call_count == 1
    assert ledger.reserved_idempotency_keys == ["l2-run-verifier-recovery-g2"]


async def test_semantic_refuses_before_transport_without_verified_evidence() -> None:
    disclosed = build_evidence()
    provider = FakeProvider()
    ledger = StubLedger()

    with pytest.raises(DeterministicVerificationRequired):
        await SemanticVerifier(transport(provider, ledger)).verify(
            identified_candidate(disclosed),
            (disclosed,),
            deterministic(disclosed, passed=False),
            context(),
        )

    assert provider.call_count == 0
    assert ledger.reserved == []
    assert ledger.attempts == []


async def test_semantic_refuses_before_transport_when_citations_include_extra_evidence(
) -> None:
    named = build_evidence(1)
    extra = build_evidence(2)
    provider = FakeProvider()
    ledger = StubLedger()

    with pytest.raises(DeterministicVerificationRequired):
        await SemanticVerifier(transport(provider, ledger)).verify(
            identified_candidate(named),
            (named, extra),
            deterministic_for(named, extra),
            context(),
        )

    assert provider.call_count == 0
    assert ledger.reserved == []
    assert ledger.attempts == []


async def test_semantic_rejects_a_reply_with_an_evidence_set_not_on_the_wire() -> None:
    disclosed = build_evidence()
    sentinel = "SEMANTIC_PROVIDER_RATIONALE_MUST_NOT_REACH_TRACEBACK"
    provider = FakeProvider(
        reply=json.dumps(
            {
                "supports_verdict": True,
                "evidence": [{"evidence_id": "b" * 64, "supports": True}],
                "reason": "supported",
                "rationale": sentinel,
            },
            separators=(",", ":"),
        )
    )
    ledger = StubLedger()

    with pytest.raises(InvalidSemanticReply) as caught:
        await SemanticVerifier(transport(provider, ledger)).verify(
            identified_candidate(disclosed),
            (disclosed,),
            deterministic(disclosed),
            context(),
        )

    assert str(caught.value) == "invalid_semantic_reply"
    assert caught.value.reservation_id == "res-1"
    assert caught.value.request_size.request_bytes > 0
    assert sentinel not in production_traceback_reprs(caught.value.__traceback__)
    assert provider.call_count == 1
