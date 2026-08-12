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
from specpilot.runtime import WorkerQueueFull, WorkerUnavailable
from specpilot.sessions.tokens import SessionClaims, SessionTokenError

_DELIVERY_FAILURE = "queue_delivery_failed"
_DELIVERY_PLACEHOLDER = "queue-delivery"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_SERVICE_UNAVAILABLE = "service_unavailable"
_LIFECYCLE_UNAVAILABLE = "api_lifecycle_unavailable"


class ApiLifecycleUnavailable(RuntimeError):
    """Stable lifecycle failure without dependency exception context."""

    def __init__(self) -> None:
        super().__init__(_LIFECYCLE_UNAVAILABLE)


def create_app(*, runtime: ApiRuntime | None = None) -> FastAPI:
    if runtime is None:
        return _unconfigured_app()

    lifespan_lock = asyncio.Lock()
    lifespan_users = 0
    lifecycle_state = "new"
    started_hooks = 0

    async def close_worker() -> None:
        nonlocal lifecycle_state, started_hooks

        async def close_owned() -> None:
            nonlocal started_hooks
            first_error = False
            while started_hooks:
                hook = runtime.lifecycle_hooks[started_hooks - 1]
                try:
                    await hook.aclose()
                except Exception:
                    first_error = True
                started_hooks -= 1
            try:
                await runtime.worker.aclose()
            except Exception:
                first_error = True
            if first_error:
                raise ApiLifecycleUnavailable() from None

        close_task = asyncio.create_task(close_owned())
        cancelled = False
        close_failed = False
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            cancelled = True
            try:
                await close_task
            except Exception:
                close_failed = True
        except Exception:
            close_failed = True
        lifecycle_state = "closed"
        if cancelled:
            raise asyncio.CancelledError
        if close_failed:
            raise ApiLifecycleUnavailable() from None

    async def start_runtime() -> None:
        nonlocal lifecycle_state, started_hooks
        failed = False
        try:
            await runtime.store.reconcile_expired()
            await runtime.worker.start()
            for hook in runtime.lifecycle_hooks:
                await hook.start()
                started_hooks += 1
        except asyncio.CancelledError:
            await close_worker()
            raise
        except Exception:
            failed = True
        if failed:
            await close_worker()
            raise ApiLifecycleUnavailable() from None
        lifecycle_state = "active"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal lifecycle_state, lifespan_users
        async with lifespan_lock:
            if lifespan_users == 0:
                if lifecycle_state == "closed":
                    raise ApiLifecycleUnavailable() from None
                await start_runtime()
            lifespan_users += 1
        try:
            yield
        finally:
            async with lifespan_lock:
                lifespan_users -= 1
                if lifespan_users == 0:
                    lifecycle_state = "closing"
                    await close_worker()

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
            code = str(error)
        except Exception:
            raise _service_unavailable() from None
        raise HTTPException(status_code=401, detail=code) from None

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
            raise _service_unavailable() from None
        except Exception:
            raise _service_unavailable() from None
        run_id = uuid4()
        created = False
        delivered = False
        service_failed = False
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
            delivered = True
        except BaseException as error:
            if created:
                await _finish_failed_delivery(runtime, run_id)
            if isinstance(error, asyncio.CancelledError):
                raise
            service_failed = True
        finally:
            if not delivered:
                cancel_task = asyncio.create_task(permit.cancel())
                try:
                    await asyncio.shield(cancel_task)
                except asyncio.CancelledError:
                    with suppress(Exception):
                        await cancel_task
                    raise
                except Exception:
                    service_failed = True
        if service_failed:
            raise _service_unavailable()
        return ChatAccepted(run_id=run_id)

    @app.get("/runs/{run_id}")
    async def read_run(
        run_id: UUID,
        session: SessionClaims = Depends(dependency=claims),  # noqa: B008
    ) -> object:
        try:
            view = await runtime.store.read_owned(run_id, session.session_id)
        except Exception:
            raise _service_unavailable() from None
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
            try:
                token = demo_issuer.issue(
                    session_id=f"demo-{uuid4()}", profile="fixture", ttl_seconds=300
                )
            except Exception:
                raise _service_unavailable() from None
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


async def _finish_failed_delivery(runtime: ApiRuntime, run_id: UUID) -> None:
    task = asyncio.create_task(
        runtime.store.fail_delivery(
            run_id,
            TerminalEvent(
                sequence=1,
                status=RunStatus.INTERRUPTED,
                reason=_DELIVERY_FAILURE,
            ),
        )
    )
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
    except Exception:
        return


def _service_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail=_SERVICE_UNAVAILABLE)


__all__ = ["ApiLifecycleUnavailable", "create_app"]
