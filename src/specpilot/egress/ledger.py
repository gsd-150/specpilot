from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from specpilot.contracts.egress import (
    CorpusUsage,
    ReservationRequest,
    TokenCounter,
    UsageSnapshot,
)
from specpilot.contracts.manifests import Identifier, ProviderRouteBinding, Sha256


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LedgerError(Exception):
    """Base class for every ledger condition that must stop a send."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LedgerUnavailable(LedgerError):
    """The ledger could not be read or written, so no budget state is known."""

    def __init__(
        self,
        message: str = "egress ledger is unavailable",
        *,
        code: str = "ledger_unavailable",
    ) -> None:
        super().__init__(code, message)


class ReservationAmbiguous(LedgerError):
    """A reservation's committed state is unknown, so it must be treated as spent.

    This is the connection-lost-during-commit case. The transport may not send,
    and may not retry under the same idempotency key until the state is
    reconciled, because the reservation may or may not exist.
    """

    def __init__(
        self,
        message: str = "reservation commit outcome is unknown",
        *,
        code: str = "reservation_ambiguous",
    ) -> None:
        super().__init__(code, message)


class RunSealed(LedgerError):
    """The run is closed to further sends until a human reconciles it.

    Sealing is what happens when a send provably left the machine but its
    accounting could not be written. Usage is then unknown, and continuing would
    spend a budget nobody can measure.
    """

    def __init__(
        self,
        message: str = "run is sealed pending reconciliation",
        *,
        code: str = "run_sealed",
    ) -> None:
        super().__init__(code, message)


class ReservationState(StrEnum):
    RESERVED = "reserved"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED_KNOWN = "failed_known"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED_KNOWN = "failed_known"


class RequestSize(_FrozenModel):
    """The measured size of one request that actually went out.

    **Not the same quantity as ``transmitted_*`` anywhere else in this package,
    and the two must never share a name again.** ``transmitted`` is corpus
    content counted with repetition — what §3.2's transmitted ledger bounds at
    four times the unique cap, computed by the enforcer at reserve time, before
    anything is sent. This is the whole request as observed afterwards: system
    prompt, reply contract, attribution line, question, excerpt labels and
    quotes together. For one real call those were 1,144 and 2,432 bytes.

    Both numbers are wanted and neither substitutes for the other. Measuring the
    cap against this one would make "four times the unique cap" describe
    nothing, since prompt overhead is not disclosure; reporting the cap figure as
    what left would understate the wire by half.

    Recorded and never enforced: no cap reads it.
    """

    request_tokens: Annotated[int, Field(ge=0)]
    request_bytes: Annotated[int, Field(ge=0)]


class Reservation(_FrozenModel):
    """A committed, budget-checked permission to send exactly once.

    ``replayed`` marks an idempotency-key hit. A replay returns the stored
    reservation unchanged and consumes no additional budget: the caps were
    already applied when it was first committed, so re-applying them would
    double-charge transmitted usage for a request that never reached a provider.
    """

    schema_version: Literal["egress-reservation-record/v1"] = (
        "egress-reservation-record/v1"
    )
    reservation_id: Identifier
    idempotency_key: Identifier
    evaluation_root_id: Identifier
    run_id: Identifier
    policy_hash: Sha256
    corpus_manifest_id: Sha256
    route: ProviderRouteBinding
    state: ReservationState
    usage: UsageSnapshot
    corpus_usage: CorpusUsage
    replayed: bool = False


class Attempt(_FrozenModel):
    """One recorded send attempt against a reservation."""

    # v2: `transmitted_usage` became `request_size`. Nothing stores this shape —
    # `egress_attempt` is explicit columns, not a document — so no data needs
    # migrating, and the bump is here because a version string that survives a
    # field rename stops describing anything.
    schema_version: Literal["egress-attempt/v2"] = "egress-attempt/v2"
    attempt_id: Identifier
    reservation_id: Identifier
    route: ProviderRouteBinding
    outcome: AttemptOutcome
    request_size: RequestSize
    duration_ms: Annotated[int, Field(ge=0)]
    public_error_code: str | None = None


class EgressLedger(Protocol):
    """The only durable authority on how much budget has been spent.

    Implementations must fail closed: any unreadable state, ambiguous commit,
    policy-hash change, or cap violation raises rather than returning a
    reservation, and the transport treats every raise as no-send.
    """

    async def check_and_reserve(
        self,
        request: ReservationRequest,
        counter: TokenCounter,
        *,
        idempotency_key: str,
    ) -> Reservation: ...

    async def seal_run(
        self,
        evaluation_root_id: str,
        run_id: str,
        reason: str,
    ) -> None: ...

    async def record_attempt(
        self,
        reservation_id: str,
        route: ProviderRouteBinding,
        request_size: RequestSize,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> Attempt: ...
