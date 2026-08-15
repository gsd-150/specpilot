from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass

from specpilot.contracts.egress import EgressRequest, ReservationRequest
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.ledger import (
    AttemptOutcome,
    EgressLedger,
    LedgerError,
    RequestSize,
)
from specpilot.providers.base import (
    ProviderError,
    ProviderResponse,
    _ProviderAdapter,
)
from specpilot.providers.cache import (
    CacheKey,
    CacheLinkage,
    CacheNamespace,
    LocalResponseCache,
    ResponseCacheError,
)
from specpilot.providers.fake import FakeProvider


class NoAdapterForRoute(LedgerError):
    """No adapter is bound to this route, so nothing may be sent or reserved."""

    def __init__(
        self,
        message: str = "no provider adapter is bound to that route",
        *,
        code: str = "no_adapter_for_route",
    ) -> None:
        super().__init__(code, message)


# A failed send has no measured request: the adapter never produced response
# metadata. Zero here means "nothing was measured", not "nothing left the
# machine" — for the unclassified fault that distinction is real and the record
# cannot express it. What bounds the disclosure in that case is the reservation,
# which was already committed and is never refunded on error.
_NOTHING_MEASURED = RequestSize(request_tokens=0, request_bytes=0)
_RECONCILIATION_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    """The complete, sanitized result of one policy-bound provider attempt."""

    response: ProviderResponse
    reservation_id: str | None
    replayed: bool
    request_size: RequestSize
    cache_hit: bool = False
    cache_request_hash: str | None = None
    cache_record_hash: str | None = None


class ProviderAttemptError(Exception):
    """A recorded provider failure with reservation identity and no raw cause."""

    __slots__ = ("public_error_code", "replayed", "request_size", "reservation_id")

    def __init__(
        self,
        public_error_code: str,
        reservation_id: str,
        replayed: bool,
        request_size: RequestSize | None,
    ) -> None:
        self.public_error_code = public_error_code
        self.reservation_id = reservation_id
        self.replayed = replayed
        self.request_size = request_size
        super().__init__(public_error_code)


class TransportReplayError(Exception):
    """A reservation was already consumed, but no response is stored to replay."""

    __slots__ = ("code", "replayed", "reservation_id")

    def __init__(self, reservation_id: str) -> None:
        self.code = "transport_replay_refused"
        self.reservation_id = reservation_id
        self.replayed = True
        super().__init__(self.code)


class PolicyBoundTransport:
    """The only way a payload reaches a provider.

    The order matters and is the whole point:

    1. Resolve the adapter. An unroutable request must not spend budget.
    2. ``prepare`` -- every field, cap, and authorization check, before the
       ledger is touched and long before the network is.
    3. ``check_and_reserve`` -- the durable, atomic budget decision.
    4. Exactly one ``send``.
    5. ``record_attempt`` -- and if that write fails, seal the run, because a
       send that happened without accounting leaves usage unknowable.

    Adapters are held privately. Nothing here returns one, so a caller cannot
    obtain a raw client and route around steps 2 and 3.
    """

    def __init__(
        self,
        *,
        enforcer: EgressPolicyEnforcer,
        ledger: EgressLedger,
        adapters: Iterable[_ProviderAdapter],
        cache: LocalResponseCache | None = None,
        cache_namespace: CacheNamespace | None = None,
    ) -> None:
        if (cache is None) != (cache_namespace is None):
            raise ValueError("cache and cache namespace must be configured together")
        self.__enforcer = enforcer
        self.__ledger = ledger
        self.__cache = cache
        self.__cache_namespace = cache_namespace
        self.__adapters = {
            (adapter.provider_id, adapter.model_id): adapter for adapter in adapters
        }

    async def send(
        self,
        request: EgressRequest,
        *,
        idempotency_key: str,
        cache_linkage: CacheLinkage | None = None,
    ) -> TransportReceipt:
        adapter = self.__adapters.get((request.route.provider_id, request.model_id))
        if adapter is None:
            raise NoAdapterForRoute()

        counter = adapter.token_counter
        reservation_request = self.__enforcer.prepare(request, counter)
        cache_key = self.__cache_key(reservation_request)
        cache = self.__cache
        if cache is not None:
            assert cache_key is not None
            if cache_linkage is None or cache_linkage.run_id != request.run_id:
                raise ResponseCacheError("cache_linkage_invalid")
            cached = await _cache_thread(
                lambda: cache.get(cache_key, linkage=cache_linkage)
            )
            if cached is not None:
                cached_response = cached.response
                return TransportReceipt(
                    response=cached_response,
                    reservation_id=None,
                    replayed=False,
                    request_size=RequestSize(
                        request_tokens=cached_response.metadata.prompt_tokens,
                        request_bytes=cached_response.metadata.request_bytes,
                    ),
                    cache_hit=True,
                    cache_request_hash=cache_key.request_hash,
                    cache_record_hash=cached.record_hash,
                )
        reservation = await self.__ledger.check_and_reserve(
            reservation_request,
            counter,
            idempotency_key=idempotency_key,
        )
        if reservation.replayed:
            # A reservation authorizes exactly one transmission. The ledger
            # intentionally stores no provider response body, so there is
            # nothing safe to return and no authority for another send.
            raise TransportReplayError(reservation.reservation_id)

        # Filled from the response, not from the disclosure facts. This used to
        # record `sum(fact.byte_count)` — the enforcer's content projection —
        # into a field documented as what went on the wire, while the answer
        # path recorded the real request size into the same column. One column,
        # two quantities, decided by which caller you came through.
        started = time.monotonic()
        response: ProviderResponse | None = None
        failure_code: str | None = None
        try:
            if isinstance(adapter, FakeProvider):
                response = await adapter.send_for_run(
                    reservation_request.projected_payload,
                    run_id=request.run_id,
                )
            else:
                response = await adapter.send(reservation_request.projected_payload)
        except ProviderError as error:
            failure_code = error.public_error_code
        except asyncio.CancelledError:
            await self.__reconcile_cancelled_send(
                reservation.reservation_id,
                request,
                duration_ms=_elapsed_ms(started),
            )
            raise
        except Exception:
            # An unclassified adapter fault: it is not known whether anything
            # left the machine, so this is recorded and re-raised, never retried
            # transparently.
            failure_code = "provider_unclassified_error"

        # Perform accounting only after the raw adapter exception is out of
        # scope. Otherwise either the public provider error or a ledger error
        # could retain the raw exception as ``__context__``.
        if failure_code is not None:
            await self.__record_after_send(
                reservation.reservation_id,
                request,
                _NOTHING_MEASURED,
                AttemptOutcome.FAILED_KNOWN,
                duration_ms=_elapsed_ms(started),
                public_error_code=failure_code,
            )
            raise ProviderAttemptError(
                failure_code,
                reservation.reservation_id,
                reservation.replayed,
                None,
            ) from None

        assert response is not None
        request_size = RequestSize(
            request_tokens=response.metadata.prompt_tokens,
            request_bytes=response.metadata.request_bytes,
        )
        await self.__record_after_send(
            reservation.reservation_id,
            request,
            request_size,
            AttemptOutcome.SUCCEEDED,
            duration_ms=_elapsed_ms(started),
        )
        cache_record_hash: str | None = None
        if cache is not None:
            assert cache_key is not None
            assert cache_linkage is not None
            cached = await _cache_thread(
                lambda: cache.put(
                    cache_key,
                    response,
                    linkage=cache_linkage,
                )
            )
            cache_record_hash = cached.record_hash
        return TransportReceipt(
            response=response,
            reservation_id=reservation.reservation_id,
            replayed=reservation.replayed,
            request_size=request_size,
            cache_hit=False,
            cache_request_hash=(None if cache_key is None else cache_key.request_hash),
            cache_record_hash=cache_record_hash,
        )

    def __cache_key(self, request: ReservationRequest) -> CacheKey | None:
        if self.__cache_namespace is None:
            return None
        # ``prepare`` returns the closed ReservationRequest contract. Keeping
        # this helper after prepare prevents malformed or unauthorized request
        # bytes from becoming a cache oracle.
        namespace = self.__cache_namespace
        if (
            namespace.source_manifest_id != request.version.source_manifest_id
            or namespace.corpus_manifest_id != request.version.corpus_manifest_id
        ):
            raise ResponseCacheError("cache_namespace_mismatch")
        payload = json.dumps(
            request.projected_payload.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return CacheKey.create(
            namespace=namespace,
            provider_id=request.route.provider_id,
            model_id=request.model_id,
            stage=request.stage,
            policy_hash=self.__enforcer.policy_hash,
            request_hash=hashlib.sha256(payload).hexdigest(),
        )

    async def __record_after_send(
        self,
        reservation_id: str,
        request: EgressRequest,
        request_size: RequestSize,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> None:
        task = asyncio.create_task(
            self.__record(
                reservation_id,
                request,
                request_size,
                outcome,
                duration_ms=duration_ms,
                public_error_code=public_error_code,
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await _settle_bounded(task)
            await self.__seal_bounded(request)
            raise

    async def __reconcile_cancelled_send(
        self,
        reservation_id: str,
        request: EgressRequest,
        *,
        duration_ms: int,
    ) -> None:
        # Cancellation can arrive after socket bytes left but before a response
        # became definite. Spend and close rather than pretending it was a
        # no-send. Repeated cancellation cannot interrupt these local writes.
        record = asyncio.create_task(
            self.__record(
                reservation_id,
                request,
                _NOTHING_MEASURED,
                AttemptOutcome.FAILED_KNOWN,
                duration_ms=duration_ms,
                public_error_code="provider_cancelled",
            )
        )
        await _settle_bounded(record)
        await self.__seal_bounded(request)

    async def __seal_bounded(self, request: EgressRequest) -> None:
        seal = asyncio.create_task(
            self.__ledger.seal_run(
                request.evaluation_root_id,
                request.run_id,
                "provider send was cancelled after reservation",
            )
        )
        await _settle_bounded(seal)

    async def __record(
        self,
        reservation_id: str,
        request: EgressRequest,
        request_size: RequestSize,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> None:
        try:
            await self.__ledger.record_attempt(
                reservation_id,
                request.route,
                request_size,
                outcome,
                duration_ms=duration_ms,
                public_error_code=public_error_code,
            )
        except LedgerError:
            # The send already happened. Its cost is now unknown, so the run is
            # closed rather than allowed to keep spending an unmeasurable budget.
            await self.__ledger.seal_run(
                request.evaluation_root_id,
                request.run_id,
                "attempt accounting could not be written after a send",
            )
            raise


def _elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 0)


async def _cache_thread[CacheResult](
    operation: Callable[[], CacheResult],
) -> CacheResult:
    """Keep private filesystem locks off-loop and observe detached completion."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_consume_detached_cache_result)
        raise


def _consume_detached_cache_result(task: asyncio.Task[object]) -> None:
    with suppress(BaseException):
        task.result()


async def _settle_bounded(task: asyncio.Task[object]) -> None:
    """Finish one shielded ledger operation despite repeated cancellation."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RECONCILIATION_TIMEOUT_SECONDS
    while not task.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            task.cancel()
            break
        try:
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            continue
    if not task.done():
        task.cancel()
    if task.done():
        with suppress(BaseException):
            task.result()


__all__ = [
    "NoAdapterForRoute",
    "PolicyBoundTransport",
    "ProviderAttemptError",
    "TransportReplayError",
    "TransportReceipt",
]
