from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from specpilot.contracts.egress import EgressRequest
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


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    """The complete, sanitized result of one policy-bound provider attempt."""

    response: ProviderResponse
    reservation_id: str
    replayed: bool
    request_size: RequestSize


class ProviderAttemptError(Exception):
    """A recorded provider failure with reservation identity and no raw cause."""

    __slots__ = ("public_error_code", "replayed", "reservation_id")

    def __init__(
        self,
        public_error_code: str,
        reservation_id: str,
        replayed: bool,
    ) -> None:
        self.public_error_code = public_error_code
        self.reservation_id = reservation_id
        self.replayed = replayed
        super().__init__(public_error_code)


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
    ) -> None:
        self.__enforcer = enforcer
        self.__ledger = ledger
        self.__adapters = {
            (adapter.provider_id, adapter.model_id): adapter for adapter in adapters
        }

    async def send(
        self,
        request: EgressRequest,
        *,
        idempotency_key: str,
    ) -> TransportReceipt:
        adapter = self.__adapters.get((request.route.provider_id, request.model_id))
        if adapter is None:
            raise NoAdapterForRoute()

        counter = adapter.token_counter
        reservation_request = self.__enforcer.prepare(request, counter)
        reservation = await self.__ledger.check_and_reserve(
            reservation_request,
            counter,
            idempotency_key=idempotency_key,
        )

        # Filled from the response, not from the disclosure facts. This used to
        # record `sum(fact.byte_count)` — the enforcer's content projection —
        # into a field documented as what went on the wire, while the answer
        # path recorded the real request size into the same column. One column,
        # two quantities, decided by which caller you came through.
        started = time.monotonic()
        try:
            response = await adapter.send(reservation_request.projected_payload)
        except ProviderError as error:
            await self.__record(
                reservation.reservation_id,
                request,
                _NOTHING_MEASURED,
                AttemptOutcome.FAILED_KNOWN,
                duration_ms=_elapsed_ms(started),
                public_error_code=error.public_error_code,
            )
            raise ProviderAttemptError(
                error.public_error_code,
                reservation.reservation_id,
                reservation.replayed,
            ) from None
        except Exception:
            # An unclassified adapter fault: it is not known whether anything
            # left the machine, so this is recorded and re-raised, never retried
            # transparently.
            await self.__record(
                reservation.reservation_id,
                request,
                _NOTHING_MEASURED,
                AttemptOutcome.FAILED_KNOWN,
                duration_ms=_elapsed_ms(started),
                public_error_code="provider_unclassified_error",
            )
            raise ProviderAttemptError(
                "provider_unclassified_error",
                reservation.reservation_id,
                reservation.replayed,
            ) from None

        request_size = RequestSize(
            request_tokens=response.metadata.prompt_tokens,
            request_bytes=response.metadata.request_bytes,
        )
        await self.__record(
            reservation.reservation_id,
            request,
            request_size,
            AttemptOutcome.SUCCEEDED,
            duration_ms=_elapsed_ms(started),
        )
        return TransportReceipt(
            response=response,
            reservation_id=reservation.reservation_id,
            replayed=reservation.replayed,
            request_size=request_size,
        )

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


__all__ = [
    "NoAdapterForRoute",
    "PolicyBoundTransport",
    "ProviderAttemptError",
    "TransportReceipt",
]
