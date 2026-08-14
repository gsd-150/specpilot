"""Owner-authenticated asynchronous L1 HTTP boundary."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
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
    ResumeAccepted,
    ResumeRequest,
)
from specpilot.api.dependencies import ApiRuntime
from specpilot.api.sse import (
    BoundedStreamingResponse,
    RunEventStreamConfig,
    parse_last_event_id,
    stream_owned_events,
)
from specpilot.api.static import install_trace_routes
from specpilot.checkpoints.contracts import RunCheckpoint
from specpilot.runs.contracts import (
    ResumeDisposition,
    RunRecord,
    RunStatus,
    TerminalEvent,
)
from specpilot.runs.postgres import RunStoreValidationError
from specpilot.runtime import RunJob, WorkerQueueFull, WorkerUnavailable
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
    stream_config = RunEventStreamConfig()

    async def close_worker() -> None:
        nonlocal lifecycle_state, started_hooks

        async def close_owned() -> BaseException | None:
            nonlocal started_hooks
            first_error: BaseException | None = None
            while started_hooks:
                hook = runtime.lifecycle_hooks[started_hooks - 1]
                try:
                    await hook.aclose()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                started_hooks -= 1
            try:
                await runtime.worker.aclose()
            except BaseException as error:
                if first_error is None:
                    first_error = error
            return first_error

        close_task = asyncio.create_task(close_owned())
        cancelled = False
        try:
            cleanup_error = await asyncio.shield(close_task)
        except asyncio.CancelledError:
            cancelled = True
            cleanup_error = await close_task
        lifecycle_state = "closed"
        if cancelled:
            raise asyncio.CancelledError
        if cleanup_error is not None and not isinstance(cleanup_error, Exception):
            raise cleanup_error
        if cleanup_error is not None:
            raise ApiLifecycleUnavailable() from None

    async def start_runtime() -> None:
        nonlocal lifecycle_state, started_hooks
        ordinary_failed = False
        try:
            await runtime.store.reconcile_expired()
            await runtime.worker.start()
            for hook in runtime.lifecycle_hooks:
                await hook.start()
                started_hooks += 1
        except BaseException as error:
            with suppress(BaseException):
                await close_worker()
            if not isinstance(error, Exception):
                raise error
            ordinary_failed = True
        if ordinary_failed:
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
    install_trace_routes(
        app,
        source_manifest_id=runtime.binding.source_manifest_id,
        corpus_manifest_id=runtime.binding.corpus_manifest_id,
    )

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
        original_error: BaseException | None = None
        cleanup_error: BaseException | None = None
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
            job = await _build_job(runtime, run_id, request.question, request, None)
            await permit.deliver(job)
            delivered = True
        except BaseException as error:
            original_error = error
            if created:
                await _finish_failed_delivery(runtime, run_id)
            service_failed = isinstance(error, Exception)
        finally:
            if not delivered:
                cancel_task = asyncio.create_task(_capture_cleanup(permit.cancel()))
                try:
                    cleanup_error = await asyncio.shield(cancel_task)
                except asyncio.CancelledError:
                    cleanup_error = await cancel_task
                    if original_error is None:
                        raise
        if original_error is not None and not isinstance(original_error, Exception):
            raise original_error
        if original_error is not None:
            service_failed = True
            cleanup_error = None
        if cleanup_error is not None and not isinstance(cleanup_error, Exception):
            raise cleanup_error
        if cleanup_error is not None:
            service_failed = True
        if service_failed:
            raise _service_unavailable()
        return ChatAccepted(run_id=run_id)

    @app.post(
        "/runs/{run_id}/resume",
        response_model=ResumeAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def resume_run(
        run_id: UUID,
        request: ResumeRequest,
        session: SessionClaims = Depends(dependency=claims),  # noqa: B008
    ) -> ResumeAccepted:
        checkpoint_store = runtime.checkpoint_store
        if checkpoint_store is None:
            raise _service_unavailable()
        query_hash = hashlib.sha256(request.question.encode("utf-8")).hexdigest()
        try:
            decision = await checkpoint_store.begin_resume(
                run_id,
                session.session_id,
                query_hash,
                request.resume_key,
                lease_owner=runtime.binding.resume_lease_owner,
                lease_seconds=runtime.binding.resume_lease_seconds,
                binding=runtime.binding.checkpoint_binding,
            )
        except Exception:
            raise _service_unavailable() from None
        disposition = decision.disposition
        if disposition in {ResumeDisposition.NOT_FOUND, ResumeDisposition.NOT_OWNER}:
            raise HTTPException(status_code=404, detail="run_not_found")
        if disposition is ResumeDisposition.REPLAY:
            assert decision.attempt is not None
            return ResumeAccepted(run_id=run_id, attempt=decision.attempt)
        if disposition is ResumeDisposition.LEASED:
            raise HTTPException(status_code=409, detail="run_already_leased")
        if disposition is ResumeDisposition.QUERY_MISMATCH:
            raise HTTPException(status_code=409, detail="resume_query_mismatch")
        if disposition is ResumeDisposition.BINDING_MISMATCH:
            raise HTTPException(status_code=409, detail="resume_binding_mismatch")
        if disposition is ResumeDisposition.NOT_INTERRUPTED:
            raise HTTPException(status_code=409, detail="run_not_resumable")
        if disposition is ResumeDisposition.CHECKPOINT_MISSING:
            raise HTTPException(status_code=422, detail="resume_checkpoint_missing")
        if disposition is ResumeDisposition.CHECKPOINT_INVALID:
            raise HTTPException(status_code=422, detail="resume_checkpoint_invalid")
        if disposition is not ResumeDisposition.ACQUIRED or decision.attempt is None:
            raise _service_unavailable()

        delivered = False
        permit = None
        try:
            checkpoint = await checkpoint_store.read(run_id)
            if checkpoint is None or checkpoint.attempt != decision.attempt:
                raise ValueError("resume_checkpoint_unavailable")
            reconstructed = _resume_chat_request(request, checkpoint)
            job = await _build_job(
                runtime, run_id, request.question, reconstructed, checkpoint
            )
            permit = await runtime.worker.reserve()
            await permit.deliver(job)
            delivered = True
        except BaseException as error:
            if permit is not None and not delivered:
                with suppress(BaseException):
                    await permit.cancel()
            with suppress(BaseException):
                await checkpoint_store.fail_resume_delivery(
                    run_id,
                    decision.attempt,
                    lease_owner=runtime.binding.resume_lease_owner,
                )
            if not isinstance(error, Exception):
                raise error
            raise _service_unavailable() from None
        return ResumeAccepted(run_id=run_id, attempt=decision.attempt)

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

    @app.get("/runs/{run_id}/events")
    async def read_run_events(
        run_id: UUID,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        session: SessionClaims = Depends(dependency=claims),  # noqa: B008
    ) -> BoundedStreamingResponse:
        try:
            cursor = parse_last_event_id(last_event_id)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="invalid_last_event_id"
            ) from None
        try:
            page = await runtime.store.read_events_owned(
                run_id,
                session.session_id,
                after_sequence=cursor,
                limit=stream_config.page_size,
            )
        except RunStoreValidationError:
            raise HTTPException(
                status_code=422, detail="invalid_last_event_id"
            ) from None
        except Exception:
            raise _service_unavailable() from None
        if page is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return BoundedStreamingResponse(
            stream_owned_events(
                runtime.store,
                run_id,
                session.session_id,
                after_sequence=cursor,
                config=stream_config,
            ),
            max_connection_seconds=stream_config.max_connection_seconds,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

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
    install_trace_routes(app)

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
        task_level=request.task_level,
        evaluation_root_id=(
            request.evaluation_root_id if request.task_level == "L2" else None
        ),
        profile=binding.profile,
        source_manifest_id=binding.source_manifest_id,
        corpus_manifest_id=binding.corpus_manifest_id,
        policy_hash=binding.policy_hash,
        configuration_hash=binding.configuration_hash,
        prompt_id=binding.prompt_id,
        prompt_hash=binding.prompt_hash,
        compliance_prompt_hash=(
            binding.compliance_prompt_hash if request.task_level == "L2" else None
        ),
        verifier_prompt_hash=(
            binding.verifier_prompt_hash if request.task_level == "L2" else None
        ),
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


async def _finish_failed_delivery(
    runtime: ApiRuntime, run_id: UUID
) -> BaseException | None:
    task = asyncio.create_task(
        _capture_cleanup(
            runtime.store.fail_delivery(
                run_id,
                TerminalEvent(
                    sequence=1,
                    status=RunStatus.INTERRUPTED,
                    reason=_DELIVERY_FAILURE,
                ),
            )
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _capture_cleanup(operation: Awaitable[object]) -> BaseException | None:
    """Turn cleanup failure into data so a child task cannot stop the event loop."""
    try:
        await operation
    except BaseException as error:
        return error
    return None


async def _build_job(
    runtime: ApiRuntime,
    run_id: UUID,
    question: str,
    request: ChatRequest,
    checkpoint: RunCheckpoint | None,
) -> RunJob:
    job = runtime.binding.build_job(run_id, question, request, checkpoint)
    if inspect.isawaitable(job):
        return await job
    return job


def _resume_chat_request(
    request: ResumeRequest, checkpoint: RunCheckpoint
) -> ChatRequest:
    """Rebuild only server-bound request identities for the job factory."""
    return ChatRequest(
        question=request.question,
        request_id=checkpoint.run_id,
        evaluation_root_id=checkpoint.evaluation_root_id,
        task_level="L2",
        source_manifest_id=checkpoint.source_manifest_id,
        corpus_manifest_id=checkpoint.corpus_manifest_id,
    )


def _service_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail=_SERVICE_UNAVAILABLE)


__all__ = ["ApiLifecycleUnavailable", "create_app"]
