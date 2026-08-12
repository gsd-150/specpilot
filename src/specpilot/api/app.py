"""Owner-authenticated asynchronous L1 HTTP boundary."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from specpilot.api.contracts import (
    ChatAccepted,
    ChatRequest,
    DemoSessionCreated,
    HealthView,
)
from specpilot.api.dependencies import ApiRuntime
from specpilot.runs.contracts import RunRecord, RunStatus, TerminalEvent
from specpilot.runs.postgres import RunStoreError
from specpilot.runtime import WorkerQueueFull, WorkerUnavailable
from specpilot.sessions.tokens import SessionClaims, SessionTokenError

_DELIVERY_FAILURE = "queue_delivery_failed"
_DELIVERY_PLACEHOLDER = "queue-delivery"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def create_app(*, runtime: ApiRuntime | None = None) -> FastAPI:
    if runtime is None:
        return _unconfigured_app()

    lifespan_lock = asyncio.Lock()
    lifespan_users = 0

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal lifespan_users
        async with lifespan_lock:
            if lifespan_users == 0:
                await runtime.store.reconcile_expired()
                await runtime.worker.start()
            lifespan_users += 1
        try:
            yield
        finally:
            async with lifespan_lock:
                lifespan_users -= 1
                if lifespan_users == 0:
                    await runtime.worker.aclose()

    app = FastAPI(title="SpecPilot", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: object, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid_request"})

    async def claims(
        authorization: str | None = Header(default=None),
        specpilot_session: str | None = Cookie(default=None),
    ) -> SessionClaims:
        token = _credential(authorization, specpilot_session)
        try:
            return runtime.verifier.verify(token)
        except SessionTokenError as error:
            raise HTTPException(status_code=401, detail=str(error)) from None

    @app.post(
        "/chat", response_model=ChatAccepted, status_code=status.HTTP_202_ACCEPTED
    )
    async def chat(
        request: ChatRequest, session: SessionClaims = Depends(dependency=claims)  # noqa: B008
    ) -> ChatAccepted:
        _require_binding(request, runtime)
        try:
            permit = await runtime.worker.reserve()
        except (WorkerQueueFull, WorkerUnavailable):
            raise HTTPException(status_code=503, detail="service_unavailable") from None
        run_id = uuid4()
        created = False
        try:
            run = _new_run(run_id, request, session, runtime)
            create_task = asyncio.create_task(runtime.store.create(run))
            try:
                await asyncio.shield(create_task)
            except asyncio.CancelledError:
                # Determine whether persistence committed before propagating
                # cancellation; otherwise a queued row can be orphaned.
                await create_task
                created = True
                raise
            created = True
            job = runtime.binding.build_job(run_id, request.question, request)
            await permit.deliver(job)
        except BaseException as error:
            if created:
                with suppress(RunStoreError):
                    await asyncio.shield(
                        runtime.store.fail_delivery(
                            run_id,
                            TerminalEvent(
                                sequence=1,
                                status=RunStatus.INTERRUPTED,
                                reason=_DELIVERY_FAILURE,
                            ),
                        )
                    )
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, (WorkerUnavailable, RunStoreError)):
                raise HTTPException(
                    status_code=503, detail="service_unavailable"
                ) from None
            raise
        finally:
            await permit.cancel()
        return ChatAccepted(run_id=run_id)

    @app.get("/runs/{run_id}")
    async def read_run(
        run_id: UUID,
        session: SessionClaims = Depends(dependency=claims),  # noqa: B008
    ) -> object:
        try:
            view = await runtime.store.read_owned(run_id, session.session_id)
        except RunStoreError:
            raise HTTPException(status_code=503, detail="service_unavailable") from None
        if view is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return view

    @app.get("/health", response_model=HealthView)
    async def health() -> HealthView:
        postgres = await _probe(runtime.postgres_health)
        mcp = await _probe(runtime.mcp_health)
        return HealthView(
            status="ok" if postgres and mcp else "degraded",
            postgres="ok" if postgres else "down",
            mcp="ok" if mcp else "down",
        )

    demo_issuer = runtime.demo_issuer
    if (
        runtime.binding.profile == "fixture"
        and runtime.bind_host in _LOOPBACK_HOSTS
        and demo_issuer is not None
    ):

        @app.post(
            "/sessions/demo",
            response_model=DemoSessionCreated,
            status_code=status.HTTP_201_CREATED,
        )
        async def demo_session(response: Response) -> DemoSessionCreated:
            token = demo_issuer.issue(
                session_id=f"demo-{uuid4()}", profile="fixture", ttl_seconds=300
            )
            response.set_cookie(
                "specpilot_session",
                token,
                max_age=300,
                httponly=True,
                secure=True,
                samesite="strict",
                path="/",
            )
            return DemoSessionCreated()

    return app


def _unconfigured_app() -> FastAPI:
    app = FastAPI(title="SpecPilot", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "degraded", "postgres": "down", "mcp": "down"}

    return app


def _credential(authorization: str | None, cookie: str | None) -> str:
    if authorization is not None:
        prefix = "Bearer "
        if not authorization.startswith(prefix) or not authorization[len(prefix) :]:
            raise HTTPException(status_code=401, detail="invalid_session")
        return authorization[len(prefix) :]
    if cookie is not None:
        return cookie
    raise HTTPException(status_code=401, detail="invalid_session")


def _require_binding(request: ChatRequest, runtime: ApiRuntime) -> None:
    binding = runtime.binding
    if (
        request.source_manifest_id != binding.source_manifest_id
        or request.corpus_manifest_id != binding.corpus_manifest_id
    ):
        raise HTTPException(status_code=422, detail="invalid_run_binding")


def _new_run(
    run_id: UUID,
    request: ChatRequest,
    session: SessionClaims,
    runtime: ApiRuntime,
) -> RunRecord:
    binding = runtime.binding
    now = datetime.now(tz=UTC)
    return RunRecord(
        run_id=run_id,
        request_id=request.request_id,
        session_id=session.session_id,
        task_level="L1",
        profile=binding.profile,
        source_manifest_id=binding.source_manifest_id,
        corpus_manifest_id=binding.corpus_manifest_id,
        policy_hash=binding.policy_hash,
        configuration_hash=binding.configuration_hash,
        prompt_id=binding.prompt_id,
        prompt_hash=binding.prompt_hash,
        provider_id=binding.provider_id,
        model_id=binding.model_id,
        query_hash=hashlib.sha256(request.question.encode("utf-8")).hexdigest(),
        status=RunStatus.QUEUED,
        terminal_reason=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        lease_owner=_DELIVERY_PLACEHOLDER,
        lease_expires_at=now + timedelta(seconds=30),
        last_heartbeat_at=None,
    )


async def _probe(probe: Callable[[], Awaitable[bool]]) -> bool:
    try:
        return bool(await probe())
    except Exception:
        return False


__all__ = ["create_app"]
