from __future__ import annotations

import hashlib
import json
from types import TracebackType

import pytest

from specpilot.agents.compliance import (
    ComplianceAgent,
    ComplianceContext,
    InvalidComplianceReply,
)
from specpilot.answer.evidence import build_evidence_from_unit
from specpilot.corpus.indexable import IndexUnit
from specpilot.providers.fake import FakeProvider
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


def evidence(index: int) -> object:
    text = f"A sender MUST perform bounded check {index}."
    return build_evidence_from_unit(
        IndexUnit(
            unit_id=hashlib.sha256(f"clause-{index}".encode()).hexdigest(),
            kind="clause",
            document_id="iso-9001",
            document_version="2026-edition",
            section_number=f"7.{index}",
            section_path=f"Requirements > 7.{index}",
            ordinal=index,
            text=text,
            indexed=text,
        ),
        corpus_manifest_id="c" * 64,
    )


def context(*, generation: int = 0) -> ComplianceContext:
    request = egress_request()
    return ComplianceContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id="l2-case",
        run_id="l2-run",
        model_id=request.model_id,
        idempotency_key="initial",
        reconstruction_generation=generation,
    )


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


async def test_compliance_reserves_a_bounded_l2_design_with_a_generation_key() -> None:
    provider = FakeProvider()
    ledger = CapturingLedger()

    outcome = await ComplianceAgent(transport(provider, ledger)).evaluate(
        "The proposed design must follow the requirements.",
        tuple(evidence(index) for index in range(13)),
        context(),
    )

    reserved = ledger.reserved[0]
    assert reserved.task_level.value == "L2"
    assert reserved.stage.value == "compliance"
    assert reserved.projected_payload.kind == "l2_design"
    assert len(reserved.projected_payload.evidence_excerpts) == 12
    assert ledger.reserved[0].evaluation_root_id == "l2-case"
    assert outcome.candidates[0].claim_id == hashlib.sha256(
        outcome.batch.candidates[0].claim.strip().encode()
    ).hexdigest()
    assert provider.call_count == 1
    assert outcome.reservation_id == "res-1"
    assert outcome.request_size.request_bytes > 0
    assert ledger.reserved[0] == reserved
    assert ledger.attempts[0].reservation_id == "res-1"
    assert ledger.reserved and ledger.attempts
    assert ledger.reserved_idempotency_keys == ["l2-run-compliance-initial-g0"]


async def test_compliance_drops_provider_prose_when_its_json_is_malformed() -> None:
    sentinel = "COMPLIANCE_PROVIDER_PROSE_MUST_NOT_REACH_TRACEBACK"
    provider = FakeProvider(reply=sentinel)
    ledger = StubLedger()

    with pytest.raises(InvalidComplianceReply) as caught:
        await ComplianceAgent(transport(provider, ledger)).evaluate(
            "The design must be checked.", (evidence(1),), context()
        )

    error = caught.value
    assert str(error) == "invalid_compliance_reply"
    assert error.reservation_id == "res-1"
    assert error.replayed is False
    assert error.request_size.request_bytes > 0
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sentinel not in production_traceback_reprs(error.__traceback__)
    assert provider.call_count == 1


async def test_compliance_keeps_an_undisclosed_valid_evidence_id_for_local_rejection(
) -> None:
    undisclosed_id = "b" * 64
    provider = FakeProvider(
        reply=json.dumps(
            {
                "candidates": [
                    {
                        "claim": "The design follows the requirement.",
                        "proposed_verdict": "compliant",
                        "evidence_ids": [undisclosed_id],
                        "rationale": "The model selected an undisclosed handle.",
                    }
                ]
            },
            separators=(",", ":"),
        )
    )

    outcome = await ComplianceAgent(transport(provider, StubLedger())).evaluate(
        "The design must be checked.", (evidence(1),), context()
    )

    assert outcome.batch.candidates[0].evidence_ids == (undisclosed_id,)
