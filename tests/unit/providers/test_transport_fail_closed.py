from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

import specpilot.providers.transport as transport_module
from specpilot.agents.contracts import ToolPlan
from specpilot.contracts.egress import (
    CorpusUsage,
    EgressRequest,
    ReservationRequest,
    TokenCounter,
    UsageSnapshot,
)
from specpilot.contracts.manifests import ProviderRouteBinding, ProviderUse
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.ledger import (
    Attempt,
    AttemptOutcome,
    LedgerUnavailable,
    RequestSize,
    Reservation,
    ReservationAmbiguous,
    ReservationState,
    RunSealed,
)
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import (
    NoAdapterForRoute,
    PolicyBoundTransport,
    ProviderAttemptError,
    TransportReceipt,
)
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_manifest_provenance import unstored_authorized_manifest
from tests.unit.egress.test_planning_projection import planning_request
from tests.unit.egress.test_policy_projection import (
    NOW,
    egress_request,
    fixture_policy,
    fixture_store,
    l1_payload,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


class StubLedger:
    """A ledger double that grants everything unless told to fail."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.reserved: list[ReservationRequest] = []
        self.attempts: list[Attempt] = []
        self.sealed: list[tuple[str, str, str]] = []

    async def check_and_reserve(
        self,
        request: ReservationRequest,
        counter: TokenCounter,
        *,
        idempotency_key: str,
    ) -> Reservation:
        if self._raises is not None:
            raise self._raises
        self.reserved.append(request)
        return Reservation(
            reservation_id=f"res-{len(self.reserved)}",
            idempotency_key=idempotency_key,
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            policy_hash="a" * 64,
            corpus_manifest_id=request.version.corpus_manifest_id,
            route=request.route,
            state=ReservationState.RESERVED,
            usage=UsageSnapshot(
                evaluation_root_id=request.evaluation_root_id,
                task_level=request.task_level,
                policy_hash="a" * 64,
            ),
            corpus_usage=CorpusUsage(
                corpus_manifest_id=request.version.corpus_manifest_id,
                policy_hash="a" * 64,
            ),
        )

    async def record_attempt(
        self,
        reservation_id: str,
        route: ProviderRouteBinding,
        request_size: RequestSize,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> Attempt:
        attempt = Attempt(
            attempt_id=f"att-{len(self.attempts) + 1}",
            reservation_id=reservation_id,
            route=route,
            outcome=outcome,
            request_size=request_size,
            duration_ms=duration_ms,
            public_error_code=public_error_code,
        )
        self.attempts.append(attempt)
        return attempt

    async def seal_run(
        self,
        evaluation_root_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        self.sealed.append((evaluation_root_id, run_id, reason))


class FailingRecorder(StubLedger):
    async def record_attempt(self, *args: object, **kwargs: object) -> Attempt:
        raise LedgerUnavailable()


class ReplayLedger(StubLedger):
    def __init__(self) -> None:
        super().__init__()
        self._reservation: Reservation | None = None

    async def check_and_reserve(
        self,
        request: ReservationRequest,
        counter: TokenCounter,
        *,
        idempotency_key: str,
    ) -> Reservation:
        if self._reservation is None:
            self._reservation = await super().check_and_reserve(
                request, counter, idempotency_key=idempotency_key
            )
            return self._reservation
        return self._reservation.model_copy(update={"replayed": True})


def transport(
    provider: FakeProvider,
    ledger: StubLedger,
    *,
    clock: datetime = NOW,
) -> PolicyBoundTransport:
    return PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            fixture_policy(),
            manifests=fixture_store(),
            clock=lambda: clock,
        ),
        ledger=ledger,
        adapters=(provider,),
    )


async def send(request: EgressRequest, **kwargs: object):
    provider = kwargs.pop("provider", None) or FakeProvider()
    ledger = kwargs.pop("ledger", None) or StubLedger()
    clock = kwargs.pop("clock", NOW)
    assert not kwargs
    line = transport(provider, ledger, clock=clock)
    return provider, ledger, await line.send(request, idempotency_key="key-1")


async def assert_no_send(request: EgressRequest, expected: type[Exception], **kwargs):
    provider = kwargs.pop("provider", None) or FakeProvider()
    ledger = kwargs.pop("ledger", None) or StubLedger()
    clock = kwargs.pop("clock", NOW)
    line = transport(provider, ledger, clock=clock)

    with pytest.raises(expected) as caught:
        await line.send(request, idempotency_key="key-1")

    assert provider.call_count == 0, "the provider was called despite a refusal"
    assert ledger.attempts == [], "an attempt was recorded for a call never made"
    return caught.value


async def test_happy_path_sends_once_and_records_one_attempt() -> None:
    provider, ledger, receipt = await send(
        egress_request(payload=l1_payload(evidence_excerpts=(distinct_excerpt(1),)))
    )

    assert provider.call_count == 1
    assert len(ledger.reserved) == 1
    assert len(ledger.attempts) == 1
    assert ledger.attempts[0].outcome is AttemptOutcome.SUCCEEDED
    assert isinstance(receipt, TransportReceipt)
    assert '"sufficient":true' in receipt.response.content
    assert receipt.reservation_id == "res-1"
    assert receipt.replayed is False
    assert receipt.request_size == ledger.attempts[0].request_size


async def test_replayed_reservation_fails_closed_before_adapter_send() -> None:
    provider = FakeProvider()
    ledger = ReplayLedger()
    line = transport(provider, ledger)

    first = await line.send(egress_request(), idempotency_key="key-1")
    with pytest.raises(transport_module.TransportReplayError) as caught:
        await line.send(egress_request(), idempotency_key="key-1")

    assert caught.value.reservation_id == first.reservation_id
    assert caught.value.replayed is True
    assert caught.value.code == "transport_replay_refused"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert provider.call_count == 1
    assert len(ledger.attempts) == 1


async def test_fake_provider_returns_a_content_json_planning_reply() -> None:
    provider = FakeProvider()

    response = await provider.send(
        planning_request(query="When may a sender retry?").payload
    )

    parsed = ToolPlan.model_validate_json(response.content)
    assert parsed.plan_id == "fixture-plan"
    assert [step.step_id for step in parsed.steps] == ["search", "read"]
    assert provider.call_count == 1


async def test_the_attempt_records_the_request_not_the_cap_figure() -> None:
    """Two quantities that used to share a column, decided by the caller.

    This path recorded `sum(fact.byte_count)` — the enforcer's projection of
    source text, which is what the caps bind — into a field documented as what
    went on the wire, while the answer path recorded the real request size into
    the same column. Both writers were self-consistent and the column held
    whichever one you happened to produce.
    """
    excerpt = distinct_excerpt(1)
    _, ledger, receipt = await send(
        egress_request(payload=l1_payload(evidence_excerpts=(excerpt,)))
    )

    recorded = ledger.attempts[0].request_size

    assert recorded.request_bytes == receipt.response.metadata.request_bytes
    assert recorded.request_tokens == receipt.response.metadata.prompt_tokens
    # The distinction is only worth a column if the two numbers differ: the
    # request carries the whole payload, the cap prices the quoted text alone.
    assert recorded.request_bytes > len(excerpt.quote.encode("utf-8"))


async def test_unstored_manifest_is_no_send() -> None:
    await assert_no_send(
        egress_request(manifest=unstored_authorized_manifest()),
        EgressPolicyViolation,
    )


async def test_route_the_manifest_does_not_authorize_is_no_send() -> None:
    other = ProviderRouteBinding(
        provider_id="provider-a",
        endpoint_purpose="some-other-purpose",
        use=ProviderUse.ONLINE_MAIN,
    )
    request = egress_request().model_copy(update={"route": other})

    await assert_no_send(request, EgressPolicyViolation)


async def test_expired_authorization_is_no_send() -> None:
    await assert_no_send(
        egress_request(),
        EgressPolicyViolation,
        clock=datetime(2026, 8, 9, tzinfo=UTC),
    )


async def test_excerpt_over_the_token_cap_is_no_send() -> None:
    payload = l1_payload(
        evidence_excerpts=(
            distinct_excerpt(1, sized_quote(tokens=512, byte_count=8193)),
        )
    )

    error = await assert_no_send(egress_request(payload=payload), EgressPolicyViolation)

    assert error.code == "excerpt_bytes_exceeded"


async def test_a_budget_refusal_from_the_ledger_is_no_send() -> None:
    await assert_no_send(
        egress_request(),
        EgressPolicyViolation,
        ledger=StubLedger(
            raises=EgressPolicyViolation("online_unique_excerpts_exceeded", "over")
        ),
    )


async def test_an_unavailable_ledger_is_no_send() -> None:
    await assert_no_send(
        egress_request(),
        LedgerUnavailable,
        ledger=StubLedger(raises=LedgerUnavailable()),
    )


async def test_an_ambiguous_reservation_is_no_send() -> None:
    await assert_no_send(
        egress_request(),
        ReservationAmbiguous,
        ledger=StubLedger(raises=ReservationAmbiguous()),
    )


async def test_a_sealed_run_is_no_send() -> None:
    await assert_no_send(
        egress_request(),
        RunSealed,
        ledger=StubLedger(raises=RunSealed()),
    )


async def test_a_route_with_no_adapter_is_no_send() -> None:
    provider = FakeProvider(provider_id="provider-b")
    ledger = StubLedger()
    line = transport(provider, ledger)

    with pytest.raises(NoAdapterForRoute):
        await line.send(egress_request(), idempotency_key="key-1")

    assert provider.call_count == 0
    assert ledger.reserved == [], "no budget may be spent before a route resolves"


async def test_a_model_with_no_adapter_is_no_send() -> None:
    provider = FakeProvider(model_id="some-other-model")
    ledger = StubLedger()
    line = transport(provider, ledger)

    with pytest.raises(NoAdapterForRoute):
        await line.send(egress_request(), idempotency_key="key-1")

    assert provider.call_count == 0
    assert ledger.reserved == []


async def test_a_known_provider_failure_records_a_failed_attempt() -> None:
    provider = FakeProvider(fail_with="provider_timeout")
    ledger = StubLedger()
    line = transport(provider, ledger)

    with pytest.raises(ProviderAttemptError) as caught:
        await line.send(egress_request(), idempotency_key="key-1")
    assert caught.value.public_error_code == "provider_timeout"
    assert caught.value.reservation_id == "res-1"
    assert caught.value.replayed is False
    assert caught.value.request_size is None
    assert str(caught.value) == "provider_timeout"

    assert provider.call_count == 1
    assert len(ledger.attempts) == 1
    assert ledger.attempts[0].outcome is AttemptOutcome.FAILED_KNOWN
    assert ledger.attempts[0].public_error_code == "provider_timeout"
    assert ledger.sealed == [], "a known failure is reconciled, not sealed"


async def test_an_unclassified_adapter_failure_carries_no_raw_exception() -> None:
    marker = "secret-provider-body /private/provider-path"

    class LeakyProvider(FakeProvider):
        async def send(self, projected_payload: object):
            self.calls.append(projected_payload)  # type: ignore[arg-type]
            raise RuntimeError(marker)

    provider = LeakyProvider()
    ledger = StubLedger()
    line = transport(provider, ledger)

    with pytest.raises(ProviderAttemptError) as caught:
        await line.send(egress_request(), idempotency_key="key-1")

    assert caught.value.public_error_code == "provider_unclassified_error"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)
    assert caught.value.request_size is None
    assert ledger.attempts[0].public_error_code == "provider_unclassified_error"


async def test_accounting_failure_after_adapter_fault_retains_no_raw_context() -> None:
    marker = "secret-provider-body /private/provider-path"

    class LeakyProvider(FakeProvider):
        async def send(self, projected_payload: object):
            self.calls.append(projected_payload)  # type: ignore[arg-type]
            raise RuntimeError(marker)

    ledger = FailingRecorder()
    line = transport(LeakyProvider(), ledger)

    with pytest.raises(LedgerUnavailable) as caught:
        await line.send(egress_request(), idempotency_key="key-1")

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert marker not in repr(caught.value)
    assert ledger.sealed


async def test_cancellation_during_adapter_send_is_reconciled_and_sealed() -> None:
    entered = asyncio.Event()
    never = asyncio.Event()

    class MaybeSentProvider(FakeProvider):
        async def send(self, projected_payload: object):
            self.calls.append(projected_payload)  # type: ignore[arg-type]
            entered.set()
            await never.wait()
            raise AssertionError("unreachable")

    provider = MaybeSentProvider()
    ledger = StubLedger()
    line = transport(provider, ledger)
    task = asyncio.create_task(line.send(egress_request(), idempotency_key="key-1"))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.call_count == 1
    assert len(ledger.attempts) == 1
    assert ledger.attempts[0].outcome is AttemptOutcome.FAILED_KNOWN
    assert ledger.attempts[0].public_error_code == "provider_cancelled"
    assert ledger.sealed, "an ambiguously sent cancellation must seal the run"


async def test_cancellation_during_success_accounting_finishes_and_seals() -> None:
    record_started = asyncio.Event()
    release_record = asyncio.Event()

    class BlockingRecorder(StubLedger):
        async def record_attempt(self, *args: object, **kwargs: object) -> Attempt:
            record_started.set()
            await release_record.wait()
            return await super().record_attempt(*args, **kwargs)  # type: ignore[arg-type]

    provider = FakeProvider()
    ledger = BlockingRecorder()
    line = transport(provider, ledger)
    task = asyncio.create_task(line.send(egress_request(), idempotency_key="key-1"))
    await record_started.wait()

    task.cancel()
    await asyncio.sleep(0)
    # Cleanup itself may receive another cancellation and must still settle.
    task.cancel()
    release_record.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.call_count == 1
    assert len(ledger.attempts) == 1
    assert ledger.attempts[0].outcome is AttemptOutcome.SUCCEEDED
    assert ledger.sealed, "cancellation after a response conservatively seals the run"


async def test_cancellation_preserves_cancelled_error_when_recording_fails() -> None:
    entered = asyncio.Event()

    class MaybeSentProvider(FakeProvider):
        async def send(self, projected_payload: object):
            self.calls.append(projected_payload)  # type: ignore[arg-type]
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    ledger = FailingRecorder()
    line = transport(MaybeSentProvider(), ledger)
    task = asyncio.create_task(line.send(egress_request(), idempotency_key="key-1"))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ledger.sealed, "record failure still takes the conservative seal path"


async def test_cancel_reconciliation_is_bounded_when_recording_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    class MaybeSentProvider(FakeProvider):
        async def send(self, projected_payload: object):
            self.calls.append(projected_payload)  # type: ignore[arg-type]
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class HangingRecorder(StubLedger):
        async def record_attempt(self, *args: object, **kwargs: object) -> Attempt:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    monkeypatch.setattr(transport_module, "_RECONCILIATION_TIMEOUT_SECONDS", 0.01)
    ledger = HangingRecorder()
    task = asyncio.create_task(
        transport(MaybeSentProvider(), ledger).send(
            egress_request(), idempotency_key="key-1"
        )
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)

    assert ledger.sealed, "timeout of attempt write must not skip bounded sealing"


async def test_unrecordable_attempt_seals_the_run() -> None:
    provider = FakeProvider()
    ledger = FailingRecorder()
    line = transport(provider, ledger)

    with pytest.raises(LedgerUnavailable):
        await line.send(egress_request(), idempotency_key="key-1")

    assert provider.call_count == 1, "the send did happen"
    assert ledger.sealed, (
        "a send whose accounting could not be written leaves usage unknown, so "
        "the run must be sealed rather than allowed to continue"
    )


async def test_transport_exposes_no_raw_adapter() -> None:
    provider = FakeProvider()
    line = transport(provider, StubLedger())

    public = {name for name in dir(line) if not name.startswith("_")}

    assert public == {"send"}, f"transport leaks more than send(): {public}"
