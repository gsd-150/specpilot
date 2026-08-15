"""Fail-closed deployment assembly for the asynchronous L1 API."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
import psycopg
from fastapi import FastAPI
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from specpilot.agents.compliance import ComplianceAgent, ComplianceContext
from specpilot.agents.evidence import EvidenceAgent
from specpilot.agents.planner import Planner, PlannerContext
from specpilot.answer.evidence import Evidence, build_evidence_from_unit
from specpilot.api.app import create_app
from specpilot.api.contracts import ChatRequest
from specpilot.api.dependencies import ApiRunBinding, ApiRuntime
from specpilot.api.static import install_trace_routes
from specpilot.checkpoints.contracts import RunCheckpoint
from specpilot.checkpoints.postgres import PostgresCheckpointStore
from specpilot.demo.scenarios import scenario_for
from specpilot.deployment.ready import require_ready_corpus
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore
from specpilot.mcp_server.client import StreamableMcpClient
from specpilot.mcp_server.runtime import load_runtime_config as load_mcp_config
from specpilot.mcp_server.runtime import load_runtime_services
from specpilot.providers.base import _ProviderAdapter
from specpilot.providers.cache import CacheLinkage, CacheNamespace, LocalResponseCache
from specpilot.providers.fake import FakeProvider
from specpilot.providers.http import MAIN_ROUTE, HttpChatAdapter, resolve_credential
from specpilot.providers.transport import PolicyBoundTransport
from specpilot.runs.contracts import RunRecord, RunStatus
from specpilot.runs.postgres import PostgresRunStore
from specpilot.runtime import L2JobFactory, RunJob, RuntimeJobBuilder, RunWorker
from specpilot.runtime.l2 import L2RunContext
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier
from specpilot.verifier.deterministic import verify_candidate
from specpilot.verifier.recovery import (
    RecoveryOutcome,
    RecoveryRequest,
    execute_recovery,
    select_recovery,
)
from specpilot.verifier.semantic import SemanticContext, SemanticVerifier

_Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ApiRuntimeConfig(BaseModel):
    """Every deployment-owned API value; none has a runtime default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["fixture", "real"]
    dsn: Annotated[str, StringConstraints(min_length=1, max_length=2_048)]
    mcp_url: Annotated[str, StringConstraints(min_length=1, max_length=2_048)]
    session_secret: Annotated[str, StringConstraints(min_length=32, max_length=4_096)]
    session_audience: _Identifier
    bind_host: Annotated[str, StringConstraints(min_length=1, max_length=253)]
    configuration_hash: _Sha256
    prompt_id: _Identifier
    prompt_hash: _Sha256
    compliance_prompt_hash: _Sha256
    verifier_prompt_hash: _Sha256
    cache_directory: Path | None = None
    cache_ttl_seconds: Annotated[int, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _validate_exact_external_identities(self) -> Self:
        exact = (
            self.dsn,
            self.mcp_url,
            self.session_secret,
            self.session_audience,
            self.bind_host,
            self.prompt_id,
        )
        if any(value != value.strip() for value in exact):
            raise ValueError("runtime values must be exact")
        try:
            conninfo_to_dict(self.dsn)
        except psycopg.Error as error:
            raise ValueError("API DSN is invalid") from error
        parsed = urlsplit(self.mcp_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/mcp"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MCP URL must be an exact HTTP /mcp endpoint")
        if self.profile == "fixture" and self.bind_host not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("fixture API must bind to loopback")
        if (self.cache_directory is None) != (self.cache_ttl_seconds is None):
            raise ValueError("cache directory and positive TTL are configured together")
        if self.cache_directory is not None and not self.cache_directory.is_absolute():
            raise ValueError("cache directory must be absolute")
        return self


def load_runtime_config() -> ApiRuntimeConfig:
    """Read only named environment inputs without opening a dependency."""

    return ApiRuntimeConfig.model_validate(
        {
            "profile": os.environ.get("SPECPILOT_API_PROFILE"),
            "dsn": os.environ.get("SPECPILOT_API_DSN"),
            "mcp_url": os.environ.get("SPECPILOT_API_MCP_URL"),
            "session_secret": os.environ.get("SPECPILOT_API_SESSION_SECRET"),
            "session_audience": os.environ.get("SPECPILOT_API_SESSION_AUDIENCE"),
            "bind_host": os.environ.get("SPECPILOT_API_BIND_HOST"),
            "configuration_hash": os.environ.get("SPECPILOT_API_CONFIGURATION_HASH"),
            "prompt_id": os.environ.get("SPECPILOT_API_PROMPT_ID"),
            "prompt_hash": os.environ.get("SPECPILOT_API_PROMPT_HASH"),
            "compliance_prompt_hash": os.environ.get(
                "SPECPILOT_API_COMPLIANCE_PROMPT_HASH"
            ),
            "verifier_prompt_hash": os.environ.get(
                "SPECPILOT_API_VERIFIER_PROMPT_HASH"
            ),
            "cache_directory": os.environ.get("SPECPILOT_API_CACHE_DIR"),
            "cache_ttl_seconds": os.environ.get("SPECPILOT_API_CACHE_TTL_SECONDS"),
        }
    )


@dataclass(slots=True)
class _McpClientHook:
    http_client: httpx.AsyncClient
    client: StreamableMcpClient
    _owner: asyncio.Task[None] | None = None
    _ready: asyncio.Future[BaseException | None] | None = None
    _close: asyncio.Future[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        async with self._lock:
            if self._owner is not None:
                raise RuntimeError("MCP client hook already started")
            loop = asyncio.get_running_loop()
            ready: asyncio.Future[BaseException | None] = loop.create_future()
            close: asyncio.Future[None] = loop.create_future()
            owner = asyncio.create_task(self._run(ready, close))
            self._owner = owner
            self._ready = ready
            self._close = close
        try:
            error = await asyncio.shield(ready)
        except BaseException:
            owner.cancel()
            with suppress(BaseException):
                await owner
            await self._reset(owner)
            raise
        if error is not None:
            await self._reset(owner)
            raise error

    async def aclose(self) -> None:
        async with self._lock:
            owner = self._owner
            close = self._close
        if owner is None or close is None:
            return
        if not close.done():
            close.set_result(None)
        cancelled = False
        try:
            await asyncio.shield(owner)
        except asyncio.CancelledError:
            # The contexts belong to ``owner``.  A lifespan cancellation must
            # not orphan that task (or move __aexit__ into the caller); wait
            # for its same-task cleanup before propagating cancellation.
            cancelled = True
            with suppress(BaseException):
                await owner
        finally:
            await self._reset(owner)
        if cancelled:
            raise asyncio.CancelledError

    async def _run(
        self,
        ready: asyncio.Future[BaseException | None],
        close: asyncio.Future[None],
    ) -> None:
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(self.http_client)
            await stack.enter_async_context(self.client)
        except BaseException as primary:
            with suppress(BaseException):
                await stack.aclose()
            if not ready.done():
                ready.set_result(primary)
            return
        ready.set_result(None)
        try:
            await close
        finally:
            await stack.aclose()

    async def _reset(self, owner: asyncio.Task[None]) -> None:
        async with self._lock:
            if self._owner is owner:
                self._owner = None
                self._ready = None
                self._close = None


@dataclass(slots=True)
class _ProviderHook:
    close: Callable[[], Awaitable[None]]

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        await self.close()


def _unavailable_app() -> FastAPI:
    app = FastAPI(title="SpecPilot", version="0.1.0")
    install_trace_routes(app)

    @app.get("/health", status_code=503)
    async def health() -> dict[str, str]:
        return {"status": "unavailable", "postgres": "down", "mcp": "down"}

    return app


def create_runtime_app() -> FastAPI:
    """Build the deployment app or expose one sanitized unavailable surface."""

    try:
        return create_app(runtime=_assemble_runtime(load_runtime_config()))
    except Exception:
        return _unavailable_app()


def _assemble_runtime(
    config: ApiRuntimeConfig,
    *,
    mcp_http_client: httpx.AsyncClient | None = None,
) -> ApiRuntime:
    mcp_config = load_mcp_config()
    if len(mcp_config.sources) != 1:
        raise ValueError("L1 API requires exactly one source binding")
    services = load_runtime_services(mcp_config)
    if mcp_config.ready_dir is None or mcp_config.ready_id is None:
        raise ValueError("API runtime requires an exact ready marker")
    corpus_manifest = CorpusManifestStore(mcp_config.corpus_manifest_dir).read(
        mcp_config.corpus_manifest_id
    )
    require_ready_corpus(
        ready_dir=mcp_config.ready_dir,
        ready_id=mcp_config.ready_id,
        corpus=corpus_manifest,
        source_manifest_ids=tuple(item.manifest_id for item in mcp_config.sources),
        mode=config.profile,
    )
    source_store = ManifestStore(mcp_config.source_manifest_dir)
    source = source_store.read_source(mcp_config.sources[0].manifest_id)
    route = source.provider_route_binding
    if route is None:
        raise ValueError("source route is not authorized")
    if config.profile == "real":
        _require_real_route(route.provider_id, route.endpoint_purpose)

    policy = _policy_for_profile(config.profile)
    adapter, provider_hook = _provider(config.profile, route.provider_id)
    cache = (
        None
        if config.cache_directory is None
        else LocalResponseCache(
            config.cache_directory,
            ttl_seconds=cast(int, config.cache_ttl_seconds),
        )
    )
    cache_namespace = (
        None
        if cache is None
        else CacheNamespace(
            configuration_hash=config.configuration_hash,
            prompt_id=config.prompt_id,
            prompt_hash=config.prompt_hash,
            compliance_prompt_hash=config.compliance_prompt_hash,
            verifier_prompt_hash=config.verifier_prompt_hash,
            source_manifest_id=source.manifest_id,
            corpus_manifest_id=mcp_config.corpus_manifest_id,
        )
    )
    transport = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(policy, manifests=source_store),
        ledger=PostgresEgressLedger(config.dsn, policy=policy, manifests=source_store),
        adapters=(adapter,),
        cache=cache,
        cache_namespace=cache_namespace,
    )
    http_client = mcp_http_client or httpx.AsyncClient(trust_env=False)
    mcp_client = StreamableMcpClient(config.mcp_url, http_client=http_client)
    mcp_hook = _McpClientHook(http_client, mcp_client)
    store = PostgresRunStore(config.dsn)
    checkpoint_store = PostgresCheckpointStore(config.dsn)
    worker = RunWorker(
        store=store,
        planner=Planner(transport),
        evidence_agent=EvidenceAgent(mcp_client, services.corpus),
        answer_transport=transport,
        worker_id="api-worker",
        queue_capacity=16,
        lease_seconds=30,
        heartbeat_interval_seconds=10,
    )
    secret = config.session_secret.encode("utf-8")
    verifier = SessionVerifier(
        secret=secret,
        audience=config.session_audience,
        profile=config.profile,
        clock=lambda: datetime.now(tz=UTC),
    )
    issuer = (
        SessionIssuer(
            secret=secret,
            audience=config.session_audience,
            clock=lambda: datetime.now(tz=UTC),
        )
        if config.profile == "fixture"
        else None
    )
    corpus_id = mcp_config.corpus_manifest_id

    def l1_job(run: RunRecord, question: str) -> RunJob:
        return RunJob(
            run_id=run.run_id,
            question=question,
            planner_context=PlannerContext(
                source_manifest=source,
                corpus_manifest_id=corpus_id,
                evaluation_root_id=run.evaluation_root_id or "invalid-root",
                run_id=str(run.run_id),
                model_id=adapter.model_id,
                idempotency_key=f"{run.run_id}-planning",
                cache_linkage=CacheLinkage(
                    run_id=str(run.run_id), session_id=run.session_id
                ),
            ),
            corpus_manifest_id=corpus_id,
            answer_context={
                "model_id": adapter.model_id,
                "source_manifest": source,
                "corpus_manifest_id": corpus_id,
                "evaluation_root_id": run.evaluation_root_id,
                "run_id": str(run.run_id),
                "idempotency_key": f"{run.run_id}-answer",
                "cache_linkage": CacheLinkage(
                    run_id=str(run.run_id), session_id=run.session_id
                ),
            },
        )

    def restore_evidence(refs: tuple[object, ...]) -> tuple[Evidence, ...]:
        restored: list[Evidence] = []
        for ref in refs:
            clause_id = getattr(ref, "clause_id", None)
            if not isinstance(clause_id, str):
                return ()
            unit = services.corpus.resolve(clause_id)
            if unit is None:
                return ()
            item = build_evidence_from_unit(unit, corpus_manifest_id=corpus_id)
            if (
                item.excerpt.content_hash != getattr(ref, "content_hash", None)
                or item.excerpt.quote_hash != getattr(ref, "quote_hash", None)
                or item.disclosed.document_id != getattr(ref, "document_id", None)
                or item.disclosed.document_version
                != getattr(ref, "document_version", None)
                or item.excerpt.span.paragraph_start
                != getattr(ref, "paragraph_start", None)
                or item.excerpt.span.paragraph_end
                != getattr(ref, "paragraph_end", None)
                or item.excerpt.span.token_start != getattr(ref, "token_start", None)
                or item.excerpt.span.token_end != getattr(ref, "token_end", None)
            ):
                return ()
            restored.append(item)
        return tuple(restored)

    async def recover(
        candidate: object,
        evidence: tuple[Evidence, ...],
        reasons: tuple[str, ...],
        attempts_used: int,
    ) -> RecoveryOutcome:
        from specpilot.contracts.verdict import IdentifiedCandidate, SemanticReason
        from specpilot.verifier.deterministic import DeterministicFault

        if not isinstance(candidate, IdentifiedCandidate):
            return RecoveryOutcome(evidence, (), attempts_used)
        closed: list[DeterministicFault | SemanticReason] = []
        for reason in reasons:
            try:
                closed.append(DeterministicFault(reason))
            except ValueError:
                try:
                    closed.append(SemanticReason(reason))
                except ValueError:
                    continue
        clause_source = next(
            (
                item.disclosed.clause_id
                for item in evidence
                if item.excerpt.content_hash in candidate.candidate.evidence_ids
            ),
            None,
        )
        selected = select_recovery(
            closed,
            source_clause_id=clause_source,
            remaining_attempts=8 - attempts_used,
        )
        if selected is None:
            return RecoveryOutcome(evidence, (), attempts_used)
        return await execute_recovery(
            RecoveryRequest(
                kind=selected.kind,
                claim_id=candidate.claim_id,
                reason_code=selected.reason_code,
                source_clause_id=(
                    clause_source if selected.kind.value != "scoped_search" else None
                ),
                corpus_manifest_id=corpus_id,
                allowed_document_ids=(source.document_id,),
                remaining_attempts=8 - attempts_used,
            ),
            claim_text=candidate.candidate.claim,
            client=mcp_client,
            corpus=services.corpus,
            existing_evidence=evidence,
            existing_calls=(),
            attempts_used=attempts_used,
        )

    def l2_context(
        run: RunRecord,
        question: str,
        checkpoint: RunCheckpoint | None,
        first: Any,
    ) -> L2RunContext:
        del first
        return L2RunContext(
            run_id=str(run.run_id),
            question=question,
            planner=Planner(transport),
            planner_context=PlannerContext(
                source_manifest=source,
                corpus_manifest_id=corpus_id,
                evaluation_root_id=run.evaluation_root_id or "invalid-root",
                run_id=str(run.run_id),
                model_id=adapter.model_id,
                idempotency_key=f"{run.run_id}-planning-initial-g0",
                cache_linkage=CacheLinkage(
                    run_id=str(run.run_id), session_id=run.session_id
                ),
            ),
            evidence_agent=cast(Any, EvidenceAgent(mcp_client, services.corpus)),
            compliance_agent=ComplianceAgent(transport),
            compliance_context=ComplianceContext(
                source_manifest=source,
                corpus_manifest_id=corpus_id,
                evaluation_root_id=run.evaluation_root_id or "invalid-root",
                run_id=str(run.run_id),
                model_id=adapter.model_id,
                idempotency_key="initial",
                reconstruction_generation=0,
                cache_linkage=CacheLinkage(
                    run_id=str(run.run_id), session_id=run.session_id
                ),
            ),
            semantic_verifier=SemanticVerifier(transport),
            semantic_context=SemanticContext(
                source_manifest=source,
                corpus_manifest_id=corpus_id,
                evaluation_root_id=run.evaluation_root_id or "invalid-root",
                run_id=str(run.run_id),
                model_id=adapter.model_id,
                idempotency_key="initial",
                reconstruction_generation=0,
                cache_linkage=CacheLinkage(
                    run_id=str(run.run_id), session_id=run.session_id
                ),
            ),
            deterministic_verifier=lambda candidate, evidence: verify_candidate(
                candidate,
                evidence,
                services.corpus,
                corpus_manifest_id=corpus_id,
                allowed_document_ids=frozenset({source.document_id}),
            ),
            recovery_runner=recover,
            checkpoint=checkpoint,
            evidence_restorer=restore_evidence,
            plan_restorer=lambda _plan_id, _plan_hash: None,
        )

    delivery = RuntimeJobBuilder(
        l1_builder=l1_job,
        l2_factory=L2JobFactory(cast(Any, checkpoint_store), l2_context),
    )

    async def build_job(
        run_id: UUID,
        question: str,
        session_id: str,
        request: ChatRequest,
        checkpoint: RunCheckpoint | None,
        query_hash: str,
        recovery_phase: str,
    ) -> RunJob:
        _register_demo_script(
            adapter,
            str(run_id),
            request,
            checkpoint,
            recovery_phase=recovery_phase,
        )
        if request.task_level == "L1":
            return RunJob(
                run_id=run_id,
                question=question,
                planner_context=PlannerContext(
                    source_manifest=source,
                    corpus_manifest_id=corpus_id,
                    evaluation_root_id=request.evaluation_root_id,
                    run_id=str(run_id),
                    model_id=adapter.model_id,
                    idempotency_key=f"{run_id}-planning",
                    cache_linkage=CacheLinkage(
                        run_id=str(run_id), session_id=session_id
                    ),
                ),
                corpus_manifest_id=corpus_id,
                answer_context={
                    "model_id": adapter.model_id,
                    "source_manifest": source,
                    "corpus_manifest_id": corpus_id,
                    "evaluation_root_id": request.evaluation_root_id,
                    "run_id": str(run_id),
                    "idempotency_key": f"{run_id}-answer",
                    "cache_linkage": CacheLinkage(
                        run_id=str(run_id), session_id=session_id
                    ),
                },
            )
        run = _ephemeral_delivery_run(
            run_id,
            request,
            config,
            source,
            policy,
            adapter,
            checkpoint,
            query_hash=query_hash,
            session_id=session_id,
        )
        job = await delivery.build(
            run,
            question,
            acquired_attempt=None if checkpoint is None else checkpoint.attempt,
        )
        if (
            recovery_phase == "semantic_failed"
            and checkpoint is not None
            and not checkpoint.recovery_attempted
            and job.l2_context is not None
        ):
            return replace(
                job,
                l2_context=replace(job.l2_context, pending_semantic_recovery=True),
            )
        return job

    hooks: tuple[Any, ...] = (
        (mcp_hook,) if provider_hook is None else (mcp_hook, provider_hook)
    )
    return ApiRuntime(
        store=store,
        worker=worker,
        verifier=verifier,
        binding=ApiRunBinding(
            profile=config.profile,
            source_manifest_id=source.manifest_id,
            corpus_manifest_id=corpus_id,
            policy_hash=policy.policy_hash,
            configuration_hash=config.configuration_hash,
            prompt_id=config.prompt_id,
            prompt_hash=config.prompt_hash,
            compliance_prompt_hash=config.compliance_prompt_hash,
            verifier_prompt_hash=config.verifier_prompt_hash,
            provider_id=adapter.provider_id,
            model_id=adapter.model_id,
            build_job=build_job,
        ),
        bind_host=config.bind_host,
        postgres_health=lambda: _postgres_health(config.dsn),
        mcp_health=lambda: _mcp_health(config.mcp_url),
        demo_issuer=issuer,
        lifecycle_hooks=hooks,
        checkpoint_store=checkpoint_store,
    )


def _register_demo_script(
    adapter: _ProviderAdapter,
    run_id: str,
    request: ChatRequest,
    checkpoint: RunCheckpoint | None,
    *,
    recovery_phase: str = "none",
) -> None:
    """Select a fixture script from the private registry, never client content."""
    if request.scenario_id is None:
        return
    if not isinstance(adapter, FakeProvider):
        raise ValueError("invalid_demo_scenario")
    script_version = scenario_for(request.scenario_id).script_version
    adapter.register_demo_script(
        run_id,
        script_version,
        recovery_consumed=(
            recovery_phase != "none"
            or (checkpoint is not None and checkpoint.recovery_attempted)
        ),
    )


def _provider(
    profile: Literal["fixture", "real"], provider_id: str
) -> tuple[_ProviderAdapter, _ProviderHook | None]:
    if profile == "fixture":
        return cast(
            _ProviderAdapter,
            FakeProvider(provider_id=provider_id, model_id="fixture-model-v1"),
        ), None
    endpoint = MAIN_ROUTE.endpoint
    if provider_id != endpoint.provider_id:
        raise ValueError("source route does not match the real main provider")
    adapter = HttpChatAdapter(endpoint, api_key=resolve_credential(endpoint))
    return cast(_ProviderAdapter, adapter), _ProviderHook(adapter.aclose)


def _ephemeral_delivery_run(
    run_id: UUID,
    request: ChatRequest,
    config: ApiRuntimeConfig,
    source: Any,
    policy: EgressPolicy,
    adapter: _ProviderAdapter,
    checkpoint: RunCheckpoint | None,
    *,
    query_hash: str,
    session_id: str,
) -> RunRecord:
    """Provide the job builder only immutable run bindings, never client state.

    The durable row was already created (or leased by ``begin_resume``).  This
    short-lived value is intentionally not persisted and exists only because
    ``RuntimeJobBuilder`` uses the closed RunRecord binding interface.
    """
    now = datetime.now(tz=UTC)
    is_l2 = request.task_level == "L2"
    return RunRecord(
        run_id=run_id,
        request_id=request.request_id,
        session_id=session_id,
        task_level=request.task_level,
        evaluation_root_id=request.evaluation_root_id if is_l2 else None,
        profile=config.profile,
        source_manifest_id=source.manifest_id,
        corpus_manifest_id=request.corpus_manifest_id,
        policy_hash=policy.policy_hash,
        configuration_hash=config.configuration_hash,
        prompt_id=config.prompt_id,
        prompt_hash=config.prompt_hash,
        compliance_prompt_hash=config.compliance_prompt_hash if is_l2 else None,
        verifier_prompt_hash=config.verifier_prompt_hash if is_l2 else None,
        provider_id=adapter.provider_id,
        model_id=adapter.model_id,
        query_hash=query_hash,
        status=RunStatus.QUEUED,
        terminal_reason=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        lease_owner="queue-delivery",
        lease_expires_at=now + timedelta(seconds=30),
        last_heartbeat_at=None,
    )


def _require_real_route(provider_id: str, endpoint_purpose: str) -> None:
    endpoint = MAIN_ROUTE.endpoint
    if (provider_id, endpoint_purpose) != (
        endpoint.provider_id,
        MAIN_ROUTE.endpoint_purpose,
    ):
        raise ValueError("source route does not match the real main endpoint")


def _policy_for_profile(profile: Literal["fixture", "real"]) -> EgressPolicy:
    return EgressPolicy.load_fixture() if profile == "fixture" else EgressPolicy.load()


async def _postgres_health(dsn: str) -> bool:
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as connection:
            return await (await connection.execute("SELECT 1")).fetchone() == (1,)
    except Exception:
        return False


async def _mcp_health(mcp_url: str) -> bool:
    parsed = urlsplit(mcp_url)
    health_url = urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(health_url)
        return response.status_code == 200
    except Exception:
        return False


__all__ = ["ApiRuntimeConfig", "create_runtime_app", "load_runtime_config"]
