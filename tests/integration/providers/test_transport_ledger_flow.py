from __future__ import annotations

import psycopg
import pytest

from specpilot.contracts.manifests import ProviderRouteBinding, ProviderUse
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.ledger import LedgerUnavailable, RunSealed
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import PolicyBoundTransport, ProviderAttemptError
from tests.unit.egress.test_disclosure_caps import distinct_excerpt
from tests.unit.egress.test_policy_projection import (
    NOW,
    authorized_manifest,
    egress_request,
    fixture_policy,
    fixture_store,
    l1_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def real_ledger(dsn: str) -> PostgresEgressLedger:
    return PostgresEgressLedger(
        dsn,
        policy=fixture_policy(),
        manifests=fixture_store(),
        clock=lambda: NOW,
    )


def transport(dsn: str, *providers: FakeProvider) -> PolicyBoundTransport:
    return PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
        ),
        ledger=real_ledger(dsn),
        adapters=providers,
    )


def request_with(*excerpts):
    return egress_request(payload=l1_payload(evidence_excerpts=excerpts))


async def scalar(dsn: str, query: str, params: tuple[object, ...] = ()) -> object:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (await connection.execute(query, params)).fetchone()
    return None if row is None else row[0]


async def test_a_full_send_records_one_reservation_and_one_attempt(
    clean_ledger: str,
) -> None:
    provider = FakeProvider()
    line = transport(clean_ledger, provider)

    await line.send(request_with(distinct_excerpt(1)), idempotency_key="evidence-1")

    assert provider.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 1
    assert (
        await scalar(clean_ledger, "SELECT state FROM egress_reservation")
    ) == "succeeded"


async def test_a_retry_under_a_new_key_is_charged_transmitted_usage_again(
    clean_ledger: str,
) -> None:
    provider = FakeProvider()
    line = transport(clean_ledger, provider)
    request = request_with(distinct_excerpt(1))

    await line.send(request, idempotency_key="evidence-1")
    await line.send(request, idempotency_key="evidence-1-retry")

    assert provider.call_count == 2
    usage = await scalar(
        clean_ledger, "SELECT usage_snapshot FROM egress_evaluation_root"
    )
    assert isinstance(usage, dict)
    assert usage["root_transmitted_tokens"] == 4, "two real sends, two charges"
    assert usage["root_unique_tokens"] == 2, "the same excerpt is one disclosure"
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 2


async def test_a_replayed_key_reuses_reservation_identity_and_reports_replay(
    clean_ledger: str,
) -> None:
    provider = FakeProvider()
    line = transport(clean_ledger, provider)
    request = request_with(distinct_excerpt(1))

    first = await line.send(request, idempotency_key="evidence-1")
    replay = await line.send(request, idempotency_key="evidence-1")

    usage = await scalar(
        clean_ledger, "SELECT usage_snapshot FROM egress_evaluation_root"
    )
    assert isinstance(usage, dict)
    assert usage["root_transmitted_tokens"] == 2, (
        "the replay reused the reservation, so no second charge"
    )
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.reservation_id == first.reservation_id
    assert provider.call_count == 2
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 2


async def test_a_fallback_provider_records_a_second_route_disclosure(
    clean_ledger: str,
) -> None:
    fallback_route = ProviderRouteBinding(
        provider_id="provider-b",
        endpoint_purpose="evidence-review",
        use=ProviderUse.ONLINE_MAIN,
    )
    primary = FakeProvider()
    fallback = FakeProvider(provider_id="provider-b")
    line = transport(clean_ledger, primary, fallback)

    excerpt = distinct_excerpt(1)
    await line.send(request_with(excerpt), idempotency_key="evidence-1")

    fallback_manifest = authorized_manifest(route=fallback_route)
    fallback_request = egress_request(
        payload=l1_payload(evidence_excerpts=(excerpt,)),
        manifest=fallback_manifest,
        route=fallback_route,
    )
    await line.send(fallback_request, idempotency_key="evidence-1-fallback")

    assert (
        await scalar(clean_ledger, "SELECT count(*) FROM egress_route_disclosure")
    ) == 2, "one excerpt seen by two providers is two route disclosures"
    assert (
        await scalar(
            clean_ledger, "SELECT unique_excerpts FROM egress_corpus_ledger"
        )
    ) == 1, "but it is still one piece of source text out of the corpus"


async def test_a_known_provider_failure_is_recorded_and_does_not_seal(
    clean_ledger: str,
) -> None:
    provider = FakeProvider(fail_with="provider_timeout")
    line = transport(clean_ledger, provider)

    with pytest.raises(ProviderAttemptError) as caught:
        await line.send(request_with(distinct_excerpt(1)), idempotency_key="evidence-1")

    assert caught.value.reservation_id == str(
        await scalar(clean_ledger, "SELECT reservation_id FROM egress_reservation")
    )
    assert caught.value.replayed is False

    assert (
        await scalar(clean_ledger, "SELECT public_error_code FROM egress_attempt")
    ) == "provider_timeout"
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_run_seal") == 0

    # The run is still usable, because the failure was fully accounted for.
    healthy = FakeProvider()
    await transport(clean_ledger, healthy).send(
        request_with(distinct_excerpt(2)), idempotency_key="evidence-2"
    )
    assert provider.call_count == 1
    assert healthy.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 2


async def test_unrecordable_accounting_seals_the_run_against_further_sends(
    clean_ledger: str,
) -> None:
    provider = FakeProvider()

    class UnrecordableLedger(PostgresEgressLedger):
        async def record_attempt(self, *args: object, **kwargs: object):
            raise LedgerUnavailable()

    sealing_line = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
        ),
        ledger=UnrecordableLedger(
            clean_ledger,
            policy=fixture_policy(),
            manifests=fixture_store(),
            clock=lambda: NOW,
        ),
        adapters=(provider,),
    )

    with pytest.raises(LedgerUnavailable):
        await sealing_line.send(
            request_with(distinct_excerpt(1)), idempotency_key="evidence-1"
        )

    assert provider.call_count == 1, "the send did leave the machine"
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_run_seal") == 1

    # A healthy transport must still refuse the sealed run.
    healthy = transport(clean_ledger, FakeProvider())
    with pytest.raises(RunSealed):
        await healthy.send(
            request_with(distinct_excerpt(2)), idempotency_key="evidence-2"
        )
