"""Fail-closed deployment assembly for the asynchronous L1 API."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
from fastapi import FastAPI
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from specpilot.agents.evidence import EvidenceAgent
from specpilot.agents.planner import Planner, PlannerContext
from specpilot.api.app import create_app
from specpilot.api.dependencies import ApiRunBinding, ApiRuntime
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.manifests.store import ManifestStore
from specpilot.mcp_server.client import StreamableMcpClient
from specpilot.mcp_server.runtime import load_runtime_config as load_mcp_config
from specpilot.mcp_server.runtime import load_runtime_services
from specpilot.providers.base import _ProviderAdapter
from specpilot.providers.fake import FakeProvider
from specpilot.providers.http import MAIN_ROUTE, HttpChatAdapter, resolve_credential
from specpilot.providers.transport import PolicyBoundTransport
from specpilot.runs.postgres import PostgresRunStore
from specpilot.runtime import RunJob, RunWorker
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier

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
        return self


def load_runtime_config() -> ApiRuntimeConfig:
    """Read only named environment inputs without opening a dependency."""

    return ApiRuntimeConfig.model_validate(
        {
            "profile": os.environ.get("SPECPILOT_API_PROFILE"),
            "dsn": os.environ.get("SPECPILOT_API_DSN"),
            "mcp_url": os.environ.get("SPECPILOT_API_MCP_URL"),
            "session_secret": os.environ.get("SPECPILOT_API_SESSION_SECRET"),
            "session_audience": os.environ.get(
                "SPECPILOT_API_SESSION_AUDIENCE"
            ),
            "bind_host": os.environ.get("SPECPILOT_API_BIND_HOST"),
            "configuration_hash": os.environ.get(
                "SPECPILOT_API_CONFIGURATION_HASH"
            ),
            "prompt_id": os.environ.get("SPECPILOT_API_PROMPT_ID"),
            "prompt_hash": os.environ.get("SPECPILOT_API_PROMPT_HASH"),
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


def _assemble_runtime(config: ApiRuntimeConfig) -> ApiRuntime:
    mcp_config = load_mcp_config()
    if len(mcp_config.sources) != 1:
        raise ValueError("L1 API requires exactly one source binding")
    services = load_runtime_services(mcp_config)
    source_store = ManifestStore(mcp_config.source_manifest_dir)
    source = source_store.read_source(mcp_config.sources[0].manifest_id)
    route = source.provider_route_binding
    if route is None:
        raise ValueError("source route is not authorized")
    if config.profile == "real":
        _require_real_route(route.provider_id, route.endpoint_purpose)

    policy = EgressPolicy.load()
    adapter, provider_hook = _provider(config.profile, route.provider_id)
    transport = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(policy, manifests=source_store),
        ledger=PostgresEgressLedger(
            config.dsn, policy=policy, manifests=source_store
        ),
        adapters=(adapter,),
    )
    http_client = httpx.AsyncClient(trust_env=False)
    mcp_client = StreamableMcpClient(config.mcp_url, http_client=http_client)
    mcp_hook = _McpClientHook(http_client, mcp_client)
    store = PostgresRunStore(config.dsn)
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

    def build_job(run_id: Any, question: str, request: Any) -> RunJob:
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
            ),
            corpus_manifest_id=corpus_id,
            answer_context={
                "model_id": adapter.model_id,
                "source_manifest": source,
                "corpus_manifest_id": corpus_id,
                "evaluation_root_id": request.evaluation_root_id,
                "run_id": str(run_id),
                "idempotency_key": f"{run_id}-answer",
            },
        )

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
            provider_id=adapter.provider_id,
            model_id=adapter.model_id,
            build_job=build_job,
        ),
        bind_host=config.bind_host,
        postgres_health=lambda: _postgres_health(config.dsn),
        mcp_health=lambda: _mcp_health(config.mcp_url),
        demo_issuer=issuer,
        lifecycle_hooks=hooks,
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


def _require_real_route(provider_id: str, endpoint_purpose: str) -> None:
    endpoint = MAIN_ROUTE.endpoint
    if (provider_id, endpoint_purpose) != (
        endpoint.provider_id,
        MAIN_ROUTE.endpoint_purpose,
    ):
        raise ValueError("source route does not match the real main endpoint")


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
