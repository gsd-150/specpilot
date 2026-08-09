"""The order of operations, driven end to end without a network or a database.

Three things here are properties of the *sequence* rather than of any one step,
so nothing but a test that runs the whole pass can hold them: the reservation is
committed before the send, a failed send still spends it, and verification is
against what this request disclosed rather than against the corpus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from specpilot.answer.evidence import build_evidence_set
from specpilot.answer.run import run_answer
from specpilot.contracts.answer import AnswerVerdict, RefusalReason
from specpilot.contracts.egress import ReservationRequest
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    ProviderUse,
    SourceManifestDraft,
)
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, iter_clause_texts
from specpilot.egress.ledger import AttemptOutcome, TransmittedUsage
from specpilot.manifests.store import ManifestStore
from specpilot.providers.base import ProviderError, ProviderResponse, ResponseMetadata
from tests.helpers import rfc_factory
from tests.unit.manifests.test_source_manifest import assessment, initial_fields

CORPUS = "c" * 64


@dataclass
class FakeEnforcer:
    """Prices the request. Records what it was asked to price."""

    seen: list[Any] = field(default_factory=list)

    def prepare(self, request: Any, counter: Any) -> ReservationRequest:
        self.seen.append(request)
        return ReservationRequest(
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            task_level=request.task_level,
            version=request.version,
            stage=request.stage,
            route=request.route,
            model_id=request.model_id,
            source_manifest=request.source_manifest,
            projected_payload=request.payload,
            disclosures=(),
        )


@dataclass
class FakeReservation:
    reservation_id: str = "res-1"
    replayed: bool = False


@dataclass
class FakeLedger:
    """Records the call order, which is the thing under test."""

    events: list[str] = field(default_factory=list)
    attempts: list[tuple[AttemptOutcome, TransmittedUsage]] = field(
        default_factory=list
    )

    async def check_and_reserve(
        self, request: Any, counter: Any, *, idempotency_key: str
    ) -> FakeReservation:
        self.events.append("reserve")
        return FakeReservation()

    async def record_attempt(
        self,
        reservation_id: str,
        route: Any,
        transmitted_usage: TransmittedUsage,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> None:
        self.events.append(f"record:{outcome.value}")
        self.attempts.append((outcome, transmitted_usage))


@dataclass
class FakeEndpoint:
    provider_id: str = "deepseek"
    model_id: str = "deepseek-v4-flash"


@dataclass
class FakeAdapter:
    """Returns a canned reply, or raises, and notes when it was called."""

    content: str = ""
    error: ProviderError | None = None
    events: list[str] | None = None
    endpoint: FakeEndpoint = field(default_factory=FakeEndpoint)

    def token_counter(self) -> Any:
        class _Counter:
            provider_id = "deepseek"
            model_id = "deepseek-v4-flash"

            def count_tokens(self, text: str) -> int:
                return len(text.encode("utf-8"))

        return _Counter()

    async def send(self, payload: Any) -> ProviderResponse:
        if self.events is not None:
            self.events.append("send")
        if self.error is not None:
            raise self.error
        return ProviderResponse(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            content=self.content,
            metadata=ResponseMetadata(
                prompt_tokens=120,
                completion_tokens=40,
                finish_reason="stop",
                duration_ms=900,
                request_bytes=4096,
            ),
        )


@pytest.fixture
def source(tmp_path: Path) -> Any:
    """An *authorized* manifest, which today means the v1 family.

    Default-deny is the point: a fresh manifest carries no route and the slice
    refuses to send from it. Authorization is a recorded compliance decision,
    which is what the successor is. The RFC family has no successor path yet —
    see the plan's Task 9 — so the slice is exercised through v1 here.
    """
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    xml = rfc_factory.write_safe(directory)
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    binding = ProviderRouteBinding(
        provider_id="provider-a",
        endpoint_purpose="evidence-review",
        use=ProviderUse.ONLINE_MAIN,
    )
    authorized = store.create_successor(
        initial,
        assessment=assessment(
            provider_id=binding.provider_id,
            endpoint_purpose=binding.endpoint_purpose,
        ),
        route_binding=binding,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )
    return authorized, xml


@pytest.fixture
def evidence(source: Any) -> Any:
    _, xml = source
    pairs = list(iter_clause_texts(xml, RfcLimits(), ClauseLimits()))[:2]
    return build_evidence_set(pairs, corpus_manifest_id=CORPUS), pairs


async def drive(source: Any, evidence: Any, adapter: FakeAdapter) -> Any:
    manifest, _ = source
    built, _ = evidence
    ledger = FakeLedger()
    adapter.events = ledger.events
    outcome = await run_answer(
        "Which paragraph exists?",
        built,
        enforcer=FakeEnforcer(),
        ledger=ledger,
        adapter=adapter,
        source_manifest=manifest,
        corpus_manifest_id=CORPUS,
        evaluation_root_id="slice-1",
        run_id="run-1",
    )
    return outcome, ledger


def reply(clause_ids: list[str], *, sufficient: bool = True) -> str:
    return json.dumps(
        {
            "sufficient": sufficient,
            "answer": "It exists." if sufficient else None,
            "citations": clause_ids,
        }
    )


@pytest.mark.anyio
async def test_the_budget_is_committed_before_anything_is_sent(
    source: Any, evidence: Any
) -> None:
    """A budget check after the bytes have left is a report, not a control."""
    built, pairs = evidence
    adapter = FakeAdapter(content=reply([pairs[0][0].clause_id]))

    _, ledger = await drive(source, evidence, adapter)

    assert ledger.events == ["reserve", "send", "record:succeeded"]


@pytest.mark.anyio
async def test_a_disclosed_clause_verifies_end_to_end(
    source: Any, evidence: Any
) -> None:
    built, pairs = evidence
    adapter = FakeAdapter(content=reply([pairs[0][0].clause_id]))

    outcome, _ = await drive(source, evidence, adapter)

    assert outcome.verified.verdict is AnswerVerdict.ANSWERED
    assert outcome.verified.answer == "It exists."
    assert [c.clause_id for c in outcome.verified.citations] == [
        pairs[0][0].clause_id
    ]
    assert outcome.transmitted == TransmittedUsage(
        transmitted_tokens=120, transmitted_bytes=4096
    )


@pytest.mark.anyio
async def test_a_real_clause_that_was_not_sent_is_still_refused(
    source: Any, evidence: Any
) -> None:
    """The whole point of the slice. The clause exists in the document and was
    not in this request's evidence, so it cannot be what the answer rests on."""
    _, xml = source
    every = list(iter_clause_texts(xml, RfcLimits(), ClauseLimits()))
    # Disclose only the first clause; cite the second, which is real and was
    # never sent.
    only_first = (build_evidence_set(every[:1], corpus_manifest_id=CORPUS), every[:1])
    unsent = every[1][0].clause_id
    assert unsent not in {item.disclosed.clause_id for item in only_first[0]}
    adapter = FakeAdapter(content=reply([unsent]))

    outcome, _ = await drive(source, only_first, adapter)

    assert outcome.verified.verdict is AnswerVerdict.REFUSED
    assert outcome.verified.refusal_reason is RefusalReason.UNVERIFIABLE_CITATION


@pytest.mark.anyio
async def test_a_failed_send_still_spends_the_reservation(
    source: Any, evidence: Any
) -> None:
    """A budget that refunds itself on error is one a retry loop walks through."""
    adapter = FakeAdapter(error=ProviderError("provider_timeout"))

    outcome, ledger = await drive(source, evidence, adapter)

    assert ledger.events == ["reserve", "send", "record:failed_known"]
    assert ledger.attempts[0][0] is AttemptOutcome.FAILED_KNOWN
    assert outcome.provider_error == "provider_timeout"
    assert outcome.verified.verdict is AnswerVerdict.REFUSED


@pytest.mark.anyio
async def test_the_model_declining_is_recorded_as_a_send_that_happened(
    source: Any, evidence: Any
) -> None:
    """It read the evidence and said no. That costs budget and is not a fault."""
    adapter = FakeAdapter(content=reply([], sufficient=False))

    outcome, ledger = await drive(source, evidence, adapter)

    assert ledger.events == ["reserve", "send", "record:succeeded"]
    assert outcome.verified.refusal_reason is RefusalReason.EVIDENCE_INSUFFICIENT
    assert outcome.verified.citation_faults == ()


@pytest.mark.anyio
async def test_an_unreadable_reply_refuses_and_names_the_parse_fault(
    source: Any, evidence: Any
) -> None:
    adapter = FakeAdapter(content="The paragraph exists, obviously.")

    outcome, _ = await drive(source, evidence, adapter)

    assert outcome.verified.verdict is AnswerVerdict.REFUSED
    assert outcome.parse_fault == "reply_not_json"
    assert outcome.verified.citation_faults == ("reply_not_json",)


@pytest.mark.anyio
async def test_nothing_retrieved_means_nothing_reserved(source: Any) -> None:
    """Reserving budget to disclose nothing would spend a cap on a request that
    cannot be answered from anything."""
    manifest, _ = source
    ledger = FakeLedger()

    outcome = await run_answer(
        "Which paragraph exists?",
        [],
        enforcer=FakeEnforcer(),
        ledger=ledger,
        adapter=FakeAdapter(),
        source_manifest=manifest,
        corpus_manifest_id=CORPUS,
        evaluation_root_id="slice-1",
        run_id="run-1",
    )

    assert ledger.events == []
    assert outcome.reservation_id is None
    assert outcome.verified.refusal_reason is RefusalReason.NO_EVIDENCE_RETRIEVED


@pytest.mark.anyio
async def test_the_request_is_priced_at_the_evidence_stage(
    source: Any, evidence: Any
) -> None:
    """Priced as JUDGE it would spend the allowance reserved for scoring."""
    manifest, _ = source
    built, pairs = evidence
    enforcer = FakeEnforcer()

    await run_answer(
        "Which paragraph exists?",
        built,
        enforcer=enforcer,
        ledger=FakeLedger(),
        adapter=FakeAdapter(content=reply([pairs[0][0].clause_id])),
        source_manifest=manifest,
        corpus_manifest_id=CORPUS,
        evaluation_root_id="slice-1",
        run_id="run-1",
    )

    priced = enforcer.seen[0]
    assert priced.stage.value == "evidence"
    assert priced.task_level.value == "L1"
    assert len(priced.payload.evidence_excerpts) == 2
