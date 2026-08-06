from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
    Reservation,
    ReservationAmbiguous,
    ReservationState,
    RunSealed,
    TransmittedUsage,
)
from specpilot.providers.base import ProviderError
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import (
    NoAdapterForRoute,
    PolicyBoundTransport,
)
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_manifest_provenance import unstored_authorized_manifest
from tests.unit.egress.test_policy_projection import (
    NOW,
    egress_request,
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
        transmitted_usage: TransmittedUsage,
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
            transmitted_usage=transmitted_usage,
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


def transport(
    provider: FakeProvider,
    ledger: StubLedger,
    *,
    clock: datetime = NOW,
) -> PolicyBoundTransport:
    return PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
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
    provider, ledger, response = await send(
        egress_request(payload=l1_payload(evidence_excerpts=(distinct_excerpt(1),)))
    )

    assert provider.call_count == 1
    assert len(ledger.reserved) == 1
    assert len(ledger.attempts) == 1
    assert ledger.attempts[0].outcome is AttemptOutcome.SUCCEEDED
    assert response.content.startswith("fixture answer")


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
        evidence_excerpts=(distinct_excerpt(1, sized_quote(tokens=513)),)
    )

    error = await assert_no_send(
        egress_request(payload=payload), EgressPolicyViolation
    )

    assert error.code == "excerpt_tokens_exceeded"


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


async def test_a_known_provider_failure_records_a_failed_attempt() -> None:
    provider = FakeProvider(fail_with="provider_timeout")
    ledger = StubLedger()
    line = transport(provider, ledger)

    with pytest.raises(ProviderError) as caught:
        await line.send(egress_request(), idempotency_key="key-1")
    assert caught.value.public_error_code == "provider_timeout"

    assert provider.call_count == 1
    assert len(ledger.attempts) == 1
    assert ledger.attempts[0].outcome is AttemptOutcome.FAILED_KNOWN
    assert ledger.attempts[0].public_error_code == "provider_timeout"
    assert ledger.sealed == [], "a known failure is reconciled, not sealed"


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
