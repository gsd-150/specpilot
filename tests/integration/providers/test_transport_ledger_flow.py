from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest

import specpilot.providers.transport as transport_module
from specpilot.contracts.manifests import ProviderRouteBinding, ProviderUse
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.ledger import LedgerUnavailable, RunSealed
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.providers.cache import (
    CacheNamespace,
    LocalResponseCache,
    ResponseCacheError,
)
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import (
    PolicyBoundTransport,
    ProviderAttemptError,
)
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


def cached_transport(
    dsn: str,
    provider: FakeProvider,
    cache: LocalResponseCache,
) -> PolicyBoundTransport:
    request = request_with(distinct_excerpt(1))
    return PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
        ),
        ledger=real_ledger(dsn),
        adapters=(provider,),
        cache=cache,
        cache_namespace=CacheNamespace(
            configuration_hash="a" * 64,
            prompt_id="l1-answer-v1",
            prompt_hash="b" * 64,
            source_manifest_id=request.version.source_manifest_id,
            corpus_manifest_id=request.version.corpus_manifest_id,
        ),
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


async def test_cache_hit_prepares_policy_but_skips_reservation_and_adapter(
    clean_ledger: str, tmp_path: Path
) -> None:
    provider = FakeProvider()
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    line = cached_transport(clean_ledger, provider, cache)
    request = request_with(distinct_excerpt(1))

    miss = await line.send(request, idempotency_key="first")
    hit = await line.send(request, idempotency_key="second")

    assert miss.cache_hit is False
    assert miss.reservation_id is not None
    assert miss.cache_record_hash is not None
    assert hit.cache_hit is True
    assert hit.reservation_id is None
    assert hit.cache_record_hash == miss.cache_record_hash
    assert hit.response == miss.response
    assert provider.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 1


async def test_cache_hit_cannot_bypass_prepare(
    clean_ledger: str, tmp_path: Path
) -> None:
    provider = FakeProvider()
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    line = cached_transport(clean_ledger, provider, cache)
    request = request_with(distinct_excerpt(1))
    await line.send(request, idempotency_key="first")
    invalid = request.model_copy(update={"model_id": "other-model"})

    with pytest.raises(Exception) as caught:
        await line.send(invalid, idempotency_key="second")

    assert getattr(caught.value, "code", None) == "no_adapter_for_route"
    assert provider.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1


async def test_cache_fault_fails_closed_before_reservation(
    clean_ledger: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider()
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    line = cached_transport(clean_ledger, provider, cache)

    def fail_get(*args: object) -> None:
        raise ResponseCacheError("cache_record_invalid")

    monkeypatch.setattr(cache, "get", fail_get)
    with pytest.raises(ResponseCacheError, match="cache_record_invalid"):
        await line.send(request_with(distinct_excerpt(1)), idempotency_key="first")

    assert provider.call_count == 0
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 0


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


async def test_a_replayed_key_fails_closed_without_an_uncharged_transmission(
    clean_ledger: str,
) -> None:
    provider = FakeProvider()
    line = transport(clean_ledger, provider)
    request = request_with(distinct_excerpt(1))

    first = await line.send(request, idempotency_key="evidence-1")
    with pytest.raises(transport_module.TransportReplayError) as caught:
        await line.send(request, idempotency_key="evidence-1")

    usage = await scalar(
        clean_ledger, "SELECT usage_snapshot FROM egress_evaluation_root"
    )
    assert isinstance(usage, dict)
    assert usage["root_transmitted_tokens"] == 2, (
        "the replay reused the reservation, so no second charge"
    )
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1
    assert first.replayed is False
    assert caught.value.replayed is True
    assert caught.value.reservation_id == first.reservation_id
    assert provider.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 1


async def test_a_failed_attempt_replay_is_not_sent_again(clean_ledger: str) -> None:
    provider = FakeProvider(fail_with="provider_timeout")
    line = transport(clean_ledger, provider)
    request = request_with(distinct_excerpt(1))

    with pytest.raises(ProviderAttemptError) as failed:
        await line.send(request, idempotency_key="evidence-1")
    with pytest.raises(transport_module.TransportReplayError) as replay:
        await line.send(request, idempotency_key="evidence-1")

    assert replay.value.reservation_id == failed.value.reservation_id
    assert replay.value.replayed is True
    assert provider.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 1


async def test_concurrent_same_key_has_one_send_and_one_closed_replay(
    clean_ledger: str,
) -> None:
    provider = FakeProvider()
    line = transport(clean_ledger, provider)
    request = request_with(distinct_excerpt(1))

    results = await asyncio.gather(
        line.send(request, idempotency_key="evidence-1"),
        line.send(request, idempotency_key="evidence-1"),
        return_exceptions=True,
    )

    receipts = sum(
        isinstance(item, transport_module.TransportReceipt) for item in results
    )
    replays = sum(
        isinstance(item, transport_module.TransportReplayError) for item in results
    )
    assert receipts == 1
    assert replays == 1
    assert provider.call_count == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 1


async def test_cancelled_possible_send_is_recorded_and_seals_real_run(
    clean_ledger: str,
) -> None:
    entered = asyncio.Event()

    class MaybeSentProvider(FakeProvider):
        async def send(self, projected_payload: object):
            self.calls.append(projected_payload)  # type: ignore[arg-type]
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    provider = MaybeSentProvider()
    line = transport(clean_ledger, provider)
    task = asyncio.create_task(
        line.send(request_with(distinct_excerpt(1)), idempotency_key="evidence-1")
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    state = await scalar(clean_ledger, "SELECT state FROM egress_reservation")
    error_code = await scalar(
        clean_ledger, "SELECT public_error_code FROM egress_attempt"
    )
    assert state == "failed_known"
    assert error_code == "provider_cancelled"
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_run_seal") == 1


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
        await scalar(clean_ledger, "SELECT unique_excerpts FROM egress_corpus_ledger")
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
