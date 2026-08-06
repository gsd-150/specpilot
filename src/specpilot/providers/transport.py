from __future__ import annotations

import time
from collections.abc import Iterable

from specpilot.contracts.egress import EgressRequest
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.ledger import (
    AttemptOutcome,
    EgressLedger,
    LedgerError,
    TransmittedUsage,
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
    ) -> ProviderResponse:
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

        transmitted = TransmittedUsage(
            transmitted_tokens=sum(
                fact.token_count for fact in reservation_request.disclosures
            ),
            transmitted_bytes=sum(
                fact.byte_count for fact in reservation_request.disclosures
            ),
        )
        started = time.monotonic()
        try:
            response = await adapter.send(reservation_request.projected_payload)
        except ProviderError as error:
            await self.__record(
                reservation.reservation_id,
                request,
                transmitted,
                AttemptOutcome.FAILED_KNOWN,
                duration_ms=_elapsed_ms(started),
                public_error_code=error.public_error_code,
            )
            raise
        except Exception as error:
            # An unclassified adapter fault: it is not known whether anything
            # left the machine, so this is recorded and re-raised, never retried
            # transparently.
            await self.__record(
                reservation.reservation_id,
                request,
                transmitted,
                AttemptOutcome.FAILED_KNOWN,
                duration_ms=_elapsed_ms(started),
                public_error_code="provider_unclassified_error",
            )
            raise ProviderError("provider_unclassified_error") from error

        await self.__record(
            reservation.reservation_id,
            request,
            transmitted,
            AttemptOutcome.SUCCEEDED,
            duration_ms=_elapsed_ms(started),
        )
        return response

    async def __record(
        self,
        reservation_id: str,
        request: EgressRequest,
        transmitted: TransmittedUsage,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> None:
        try:
            await self.__ledger.record_attempt(
                reservation_id,
                request.route,
                transmitted,
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


__all__ = ["NoAdapterForRoute", "PolicyBoundTransport"]
