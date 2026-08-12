"""Explicit API assembly; deployment-specific provider/corpus state has no default."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from specpilot.api.contracts import ChatRequest
from specpilot.runs.contracts import RunRecord, RunView, TerminalEvent
from specpilot.runtime import RunJob
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier


class ApiRunStore(Protocol):
    async def create(self, run: RunRecord) -> RunRecord: ...

    async def read_owned(self, run_id: UUID, session_id: str) -> RunView | None: ...

    async def fail_delivery(self, run_id: UUID, event: TerminalEvent) -> bool: ...

    async def reconcile_expired(self) -> int: ...


class DeliveryPermit(Protocol):
    async def deliver(self, job: RunJob) -> None: ...

    async def cancel(self) -> None: ...


class ApiWorker(Protocol):
    async def start(self) -> None: ...

    async def reserve(self) -> DeliveryPermit: ...

    async def aclose(self) -> None: ...


class ApiLifecycleHook(Protocol):
    async def start(self) -> None: ...

    async def aclose(self) -> None: ...


HealthProbe = Callable[[], Awaitable[bool]]
JobFactory = Callable[[UUID, str, ChatRequest], RunJob]


@dataclass(frozen=True, slots=True)
class ApiRunBinding:
    profile: str
    source_manifest_id: str
    corpus_manifest_id: str
    policy_hash: str
    configuration_hash: str
    prompt_id: str
    prompt_hash: str
    provider_id: str
    model_id: str
    build_job: JobFactory


@dataclass(frozen=True, slots=True)
class ApiRuntime:
    store: ApiRunStore
    worker: ApiWorker
    verifier: SessionVerifier
    binding: ApiRunBinding
    bind_host: str
    postgres_health: HealthProbe
    mcp_health: HealthProbe
    demo_issuer: SessionIssuer | None = None
    lifecycle_hooks: tuple[ApiLifecycleHook, ...] = ()


__all__ = ["ApiRunBinding", "ApiRuntime"]
