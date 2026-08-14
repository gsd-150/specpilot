"""Explicit API assembly; deployment-specific provider/corpus state has no default."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from specpilot.api.contracts import ChatRequest
from specpilot.checkpoints.contracts import CheckpointBinding, RunCheckpoint
from specpilot.checkpoints.postgres import CheckpointResumeResult
from specpilot.runs.contracts import RunEventPage, RunRecord, RunView, TerminalEvent
from specpilot.runtime import RunJob
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier


class ApiRunStore(Protocol):
    async def create(self, run: RunRecord) -> RunRecord: ...

    async def read_owned(self, run_id: UUID, session_id: str) -> RunView | None: ...

    async def read_events_owned(
        self,
        run_id: UUID,
        session_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> RunEventPage | None: ...

    async def fail_delivery(self, run_id: UUID, event: TerminalEvent) -> bool: ...

    async def reconcile_expired(self) -> int: ...


class ApiCheckpointStore(Protocol):
    async def begin_resume(
        self,
        run_id: UUID,
        session_id: str,
        query_hash: str,
        resume_key: str,
        *,
        lease_owner: str,
        lease_seconds: int,
        binding: CheckpointBinding,
    ) -> CheckpointResumeResult: ...

    async def read(self, run_id: UUID) -> RunCheckpoint | None: ...

    async def fail_resume_delivery(
        self, run_id: UUID, attempt: int, *, lease_owner: str
    ) -> bool: ...


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
JobFactory = Callable[
    [UUID, str, ChatRequest, RunCheckpoint | None], RunJob | Awaitable[RunJob]
]


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
    compliance_prompt_hash: str = "f" * 64
    verifier_prompt_hash: str = "0" * 64
    resume_lease_owner: str = "api-worker"
    resume_lease_seconds: int = 30

    @property
    def checkpoint_binding(self) -> CheckpointBinding:
        return CheckpointBinding(
            source_manifest_id=self.source_manifest_id,
            corpus_manifest_id=self.corpus_manifest_id,
            policy_hash=self.policy_hash,
            configuration_hash=self.configuration_hash,
            compliance_prompt_hash=self.compliance_prompt_hash,
            verifier_prompt_hash=self.verifier_prompt_hash,
            provider_id=self.provider_id,
            model_id=self.model_id,
        )


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
    checkpoint_store: ApiCheckpointStore | None = None


__all__ = ["ApiCheckpointStore", "ApiRunBinding", "ApiRuntime"]
