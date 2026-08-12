from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from specpilot.api.app import create_app
from specpilot.api.dependencies import ApiRunBinding, ApiRuntime
from specpilot.runs.contracts import RunRecord, RunStatus, RunView
from specpilot.runtime import RunJob, WorkerQueueFull, WorkerUnavailable
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 12, tzinfo=UTC)
HASHES = {name: character * 64 for name, character in zip(
    ("source", "corpus", "policy", "configuration", "prompt"), "abcde", strict=True
)}


@dataclass
class FakePermit:
    worker: FakeWorker
    used: bool = False

    async def deliver(self, job: RunJob) -> None:
        if self.used:
            raise WorkerUnavailable("delivery_permit_used")
        self.used = True
        if self.worker.cancel_delivery:
            raise asyncio.CancelledError
        if self.worker.delivery_base_error is not None:
            raise self.worker.delivery_base_error
        if self.worker.delivery_error:
            raise WorkerUnavailable("worker_closed")
        self.worker.jobs.append(job)

    async def cancel(self) -> None:
        self.used = True
        self.worker.permit_cancels += 1
        if self.worker.permit_cancel_error is not None:
            raise self.worker.permit_cancel_error


@dataclass
class FakeWorker:
    full: bool = False
    delivery_error: bool = False
    cancel_delivery: bool = False
    jobs: list[RunJob] = field(default_factory=list)
    starts: int = 0
    closes: int = 0
    start_error: BaseException | None = None
    close_error: BaseException | None = None
    close_entered: asyncio.Event = field(default_factory=asyncio.Event)
    close_release: asyncio.Event | None = None
    reserve_error: BaseException | None = None
    permit_cancel_error: BaseException | None = None
    delivery_base_error: BaseException | None = None
    permit_cancels: int = 0

    async def start(self) -> None:
        self.starts += 1
        if self.start_error is not None:
            raise self.start_error

    async def reserve(self) -> FakePermit:
        if self.reserve_error is not None:
            raise self.reserve_error
        if self.full:
            raise WorkerQueueFull("worker_queue_full")
        return FakePermit(self)

    async def aclose(self) -> None:
        self.close_entered.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


@dataclass
class FakeStore:
    runs: dict[UUID, tuple[str, RunView]] = field(default_factory=dict)
    creates: int = 0
    reconciles: int = 0
    delivery_failures: int = 0
    create_entered: asyncio.Event = field(default_factory=asyncio.Event)
    create_release: asyncio.Event | None = None
    create_error: BaseException | None = None
    read_error: BaseException | None = None
    reconcile_error: BaseException | None = None
    fail_delivery_error: BaseException | None = None
    fail_delivery_attempts: int = 0

    async def create(self, run: RunRecord) -> RunRecord:
        self.create_entered.set()
        if self.create_release is not None:
            await self.create_release.wait()
        if self.create_error is not None:
            raise self.create_error
        self.creates += 1
        view = RunView(
            run_id=run.run_id, request_id=run.request_id, task_level="L1",
            profile=run.profile, corpus_manifest_id=run.corpus_manifest_id,
            status=RunStatus.QUEUED, reason=None, created_at=NOW,
            started_at=None, completed_at=None, events=(),
        )
        self.runs[run.run_id] = (run.session_id, view)
        return run

    async def read_owned(self, run_id: UUID, session_id: str) -> RunView | None:
        if self.read_error is not None:
            raise self.read_error
        stored = self.runs.get(run_id)
        return stored[1] if stored is not None and stored[0] == session_id else None

    async def reconcile_expired(self) -> int:
        self.reconciles += 1
        if self.reconcile_error is not None:
            raise self.reconcile_error
        return 0

    async def fail_delivery(self, run_id: UUID, event: object) -> bool:
        self.fail_delivery_attempts += 1
        self.delivery_failures += 1
        if self.fail_delivery_error is not None:
            raise self.fail_delivery_error
        return True


@dataclass
class FakeHook:
    start_error: BaseException | None = None
    close_error: BaseException | None = None
    starts: int = 0
    closes: int = 0

    async def start(self) -> None:
        self.starts += 1
        if self.start_error is not None:
            raise self.start_error

    async def aclose(self) -> None:
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


@dataclass
class HostileIssuer:
    marker: str

    def issue(self, *, session_id: str, profile: str, ttl_seconds: int) -> str:
        raise RuntimeError(self.marker)


@dataclass
class HostileVerifier:
    marker: str

    def verify(self, token: str) -> object:
        raise RuntimeError(self.marker)


def runtime(*, profile: str = "fixture", host: str = "127.0.0.1") -> ApiRuntime:
    secret = b"s" * 32
    store = FakeStore()
    worker = FakeWorker()
    issuer = SessionIssuer(secret=secret, audience="specpilot-api", clock=lambda: NOW)
    verifier = SessionVerifier(
        secret=secret, audience="specpilot-api", profile=profile, clock=lambda: NOW
    )
    binding = ApiRunBinding(
        profile=profile, source_manifest_id=HASHES["source"],
        corpus_manifest_id=HASHES["corpus"], policy_hash=HASHES["policy"],
        configuration_hash=HASHES["configuration"], prompt_id="l1-answer-v1",
        prompt_hash=HASHES["prompt"], provider_id="provider-a", model_id="model-a",
        build_job=lambda run_id, question, request: RunJob(
            run_id=run_id, question=question, planner_context=object(),
            corpus_manifest_id=request.corpus_manifest_id, answer_context={}
        ),
    )
    return ApiRuntime(
        store=store, worker=worker, verifier=verifier, binding=binding,
        bind_host=host, demo_issuer=issuer if profile == "fixture" else None,
        postgres_health=lambda: _health(True), mcp_health=lambda: _health(True),
    )


async def _health(value: bool) -> bool:
    return value


async def client_for(made: ApiRuntime) -> Any:
    app = create_app(runtime=made)
    return app, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    )


def payload() -> dict[str, str]:
    return {
        "question": "private question", "request_id": str(uuid4()),
        "evaluation_root_id": "root-1", "task_level": "L1",
        "source_manifest_id": HASHES["source"],
        "corpus_manifest_id": HASHES["corpus"],
    }


async def test_chat_returns_202_and_foreign_trace_is_same_404_as_unknown() -> None:
    made = runtime()
    app, client = await client_for(made)
    issuer = made.demo_issuer
    assert issuer is not None
    owner = issuer.issue(session_id="owner-a", profile="fixture", ttl_seconds=300)
    foreign = issuer.issue(session_id="owner-b", profile="fixture", ttl_seconds=300)
    async with app.router.lifespan_context(app), client:
        created = await client.post(
            "/chat", headers={"Authorization": f"Bearer {owner}"}, json=payload()
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        foreign_response = await client.get(
            f"/runs/{run_id}", headers={"Authorization": f"Bearer {foreign}"}
        )
        unknown = await client.get(
            f"/runs/{uuid4()}", headers={"Authorization": f"Bearer {foreign}"}
        )
        assert foreign_response.status_code == unknown.status_code == 404
        assert foreign_response.json() == unknown.json() == {"detail": "run_not_found"}
        owned = await client.get(
            f"/runs/{run_id}", headers={"Authorization": f"Bearer {owner}"}
        )
    assert owned.status_code == 200
    assert "private question" not in owned.text


async def test_bearer_precedes_cookie_and_both_use_identical_verification() -> None:
    made = runtime()
    app, client = await client_for(made)
    issuer = made.demo_issuer
    assert issuer is not None
    valid = issuer.issue(session_id="owner-a", profile="fixture", ttl_seconds=300)
    async with app.router.lifespan_context(app), client:
        client.cookies.set("specpilot_session", valid)
        cookie_only = await client.post("/chat", json=payload())
        bad_bearer = await client.post(
            "/chat", headers={"Authorization": "Bearer invalid"},
            json=payload()
        )
    assert cookie_only.status_code == 202
    assert bad_bearer.status_code == 401
    assert bad_bearer.json() == {"detail": "invalid_session"}


async def test_valid_bearer_precedes_a_different_valid_cookie_owner() -> None:
    made = runtime()
    app, client = await client_for(made)
    issuer = made.demo_issuer
    assert issuer is not None
    bearer = issuer.issue(session_id="bearer-owner", profile="fixture", ttl_seconds=300)
    cookie = issuer.issue(session_id="cookie-owner", profile="fixture", ttl_seconds=300)
    client.cookies.set("specpilot_session", cookie)
    async with app.router.lifespan_context(app), client:
        created = await client.post(
            "/chat", headers={"Authorization": f"Bearer {bearer}"}, json=payload()
        )
    run_id = UUID(created.json()["run_id"])
    assert made.store.runs[run_id][0] == "bearer-owner"


async def test_expired_session_is_stable_and_does_not_fall_back_to_cookie() -> None:
    secret = b"s" * 32
    issuer = SessionIssuer(secret=secret, audience="specpilot-api", clock=lambda: NOW)
    expired = issuer.issue(session_id="owner-a", profile="fixture", ttl_seconds=1)
    made = runtime()
    object.__setattr__(
        made,
        "verifier",
        SessionVerifier(
            secret=secret,
            audience="specpilot-api",
            profile="fixture",
            clock=lambda: datetime(2026, 8, 12, 0, 0, 2, tzinfo=UTC),
        ),
    )
    valid_cookie = issuer.issue(
        session_id="cookie-owner", profile="fixture", ttl_seconds=300
    )
    app, client = await client_for(made)
    client.cookies.set("specpilot_session", valid_cookie)
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {expired}"}, json=payload()
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "expired_session"}


async def test_backpressure_happens_before_creation_and_is_sanitized() -> None:
    made = runtime()
    made.worker.full = True
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert made.store.creates == 0
    assert "private question" not in response.text


async def test_delivery_failure_after_creation_terminalizes_without_a_job() -> None:
    made = runtime()
    made.worker.delivery_error = True
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
        )
    assert response.status_code == 503
    assert made.store.creates == made.store.delivery_failures == 1
    assert made.worker.jobs == []


async def test_demo_route_is_fixture_loopback_only_and_sets_secure_cookie() -> None:
    fixture = runtime()
    app, client = await client_for(fixture)
    async with app.router.lifespan_context(app), client:
        response = await client.post("/sessions/demo")
    assert response.status_code == 201
    cookie = response.headers["set-cookie"]
    assert (
        "HttpOnly" in cookie
        and "SameSite=strict" in cookie
        and "Max-Age=300" in cookie
        and "Path=/" in cookie
        and "Secure" in cookie
    )
    assert response.json() == {"status": "created"}

    for made in (runtime(profile="real"), runtime(host="0.0.0.0")):
        app, client = await client_for(made)
        async with app.router.lifespan_context(app), client:
            assert (await client.post("/sessions/demo")).status_code == 404


async def test_health_and_lifespan_are_closed_and_sanitized() -> None:
    made = runtime()
    app, client = await client_for(made)
    async with app.router.lifespan_context(app), client:
        response = await client.get("/health")
    assert response.json() == {"status": "ok", "postgres": "ok", "mcp": "ok"}
    assert made.store.reconciles == made.worker.starts == made.worker.closes == 1
    assert set(response.json()) == {"status", "postgres", "mcp"}


async def test_health_probe_exception_is_degraded_and_marker_free() -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()

    async def hostile() -> bool:
        raise RuntimeError(marker)

    object.__setattr__(made, "postgres_health", hostile)
    app, client = await client_for(made)
    async with app.router.lifespan_context(app), client:
        response = await client.get("/health")
    assert response.json() == {
        "status": "degraded",
        "postgres": "down",
        "mcp": "ok",
    }
    assert marker not in response.text


async def test_demo_issuer_exception_is_stable_and_marker_free() -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()
    object.__setattr__(made, "demo_issuer", HostileIssuer(marker))
    app, client = await client_for(made)
    async with app.router.lifespan_context(app), client:
        response = await client.post("/sessions/demo")
    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert marker not in response.text


async def test_session_verifier_exception_is_stable_and_marker_free() -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()

    object.__setattr__(made, "verifier", HostileVerifier(marker))
    app, client = await client_for(made)
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": "Bearer opaque"}, json=payload()
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert marker not in response.text


@pytest.mark.parametrize("boundary", ["create", "read", "build", "deliver"])
async def test_generic_api_exception_is_stable_marker_free_and_terminalizes(
    boundary: str,
) -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()
    if boundary == "create":
        made.store.create_error = RuntimeError(marker)
    elif boundary == "read":
        made.store.read_error = RuntimeError(marker)
    elif boundary == "build":
        binding = made.binding

        def hostile_build(run_id: UUID, question: str, request: object) -> RunJob:
            raise RuntimeError(marker)

        object.__setattr__(made, "binding", ApiRunBinding(
            profile=binding.profile,
            source_manifest_id=binding.source_manifest_id,
            corpus_manifest_id=binding.corpus_manifest_id,
            policy_hash=binding.policy_hash,
            configuration_hash=binding.configuration_hash,
            prompt_id=binding.prompt_id,
            prompt_hash=binding.prompt_hash,
            provider_id=binding.provider_id,
            model_id=binding.model_id,
            build_job=hostile_build,
        ))
    else:
        made.worker.delivery_error = True
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        if boundary == "read":
            response = await client.get(
                f"/runs/{uuid4()}", headers={"Authorization": f"Bearer {token}"}
            )
        else:
            response = await client.post(
                "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
            )
    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert marker not in response.text
    if boundary in {"build", "deliver"}:
        assert made.store.delivery_failures == 1
    else:
        assert made.store.delivery_failures == 0


async def test_permit_cancel_exception_is_stable_and_marker_free() -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()
    made.worker.permit_cancel_error = RuntimeError(marker)
    made.store.create_error = RuntimeError("sanitized-in-test")
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
        )
    assert response.status_code == 503
    assert marker not in response.text


async def test_invalid_body_never_echoes_question_or_validation_input() -> None:
    made = runtime()
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    private = "question-must-never-return"
    invalid = {**payload(), "question": private, "task_level": "L2"}
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=invalid
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert private not in response.text


async def test_cancelled_delivery_terminalizes_created_run_and_propagates() -> None:
    made = runtime()
    made.worker.cancel_delivery = True
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
            )
    assert made.store.creates == made.store.delivery_failures == 1
    assert made.worker.jobs == []


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(17)])
async def test_non_exception_base_error_cleans_then_propagates_original(
    error: BaseException,
) -> None:
    made = runtime()
    made.worker.delivery_base_error = error
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        with pytest.raises(type(error)) as caught:
            await client.post(
                "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
            )
    assert caught.value is error
    assert made.store.creates == made.store.delivery_failures == 1
    assert made.worker.permit_cancels == 1
    assert made.worker.jobs == []


@pytest.mark.parametrize("original", [KeyboardInterrupt(), SystemExit(29)])
async def test_original_control_flow_wins_over_both_cleanup_failures(
    original: BaseException,
) -> None:
    made = runtime()
    made.worker.delivery_base_error = original
    made.store.fail_delivery_error = SystemExit(31)
    made.worker.permit_cancel_error = KeyboardInterrupt()
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        with pytest.raises(type(original)) as caught:
            await client.post(
                "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
            )
    assert caught.value is original
    assert made.store.fail_delivery_attempts == 1
    assert made.worker.permit_cancels == 1


async def test_ordinary_failure_stays_503_when_both_cleanups_fail() -> None:
    made = runtime()
    made.worker.delivery_base_error = RuntimeError("primary-private")
    made.store.fail_delivery_error = SystemExit(37)
    made.worker.permit_cancel_error = KeyboardInterrupt()
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert made.store.fail_delivery_attempts == made.worker.permit_cancels == 1


async def test_cancelled_create_waits_for_commit_then_terminalizes_run() -> None:
    made = runtime()
    made.store.create_release = asyncio.Event()
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    async with app.router.lifespan_context(app), client:
        request = asyncio.create_task(
            client.post(
                "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
            )
        )
        await made.store.create_entered.wait()
        request.cancel()
        made.store.create_release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
    assert made.store.creates == made.store.delivery_failures == 1
    assert made.worker.jobs == []


async def test_concurrent_lifespans_share_one_worker_lifecycle() -> None:
    made = runtime()
    app = create_app(runtime=made)
    first = app.router.lifespan_context(app)
    second = app.router.lifespan_context(app)
    await first.__aenter__()
    await second.__aenter__()
    assert made.store.reconciles == made.worker.starts == 1
    await first.__aexit__(None, None, None)
    assert made.worker.closes == 0
    await second.__aexit__(None, None, None)
    assert made.worker.closes == 1


async def test_cancelled_last_shutdown_finishes_close_before_propagating() -> None:
    made = runtime()
    made.worker.close_release = asyncio.Event()
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    closing = asyncio.create_task(context.__aexit__(None, None, None))
    await made.worker.close_entered.wait()
    closing.cancel()
    contender = app.router.lifespan_context(app)
    entering = asyncio.create_task(contender.__aenter__())
    await asyncio.sleep(0)
    assert not entering.done()
    made.worker.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with pytest.raises(RuntimeError, match="api_lifecycle_unavailable"):
        await entering
    assert made.worker.closes == 1


async def test_shutdown_cancellation_wins_over_cleanup_base_error() -> None:
    made = runtime()
    made.worker.close_release = asyncio.Event()
    cleanup_error = SystemExit(41)
    made.worker.close_error = cleanup_error
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    closing = asyncio.create_task(context.__aexit__(None, None, None))
    await made.worker.close_entered.wait()
    closing.cancel()
    made.worker.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert made.worker.closes == 1
    contender = app.router.lifespan_context(app)
    with pytest.raises(RuntimeError, match="api_lifecycle_unavailable"):
        await contender.__aenter__()


async def test_close_first_ordinary_error_wins_over_later_system_exit() -> None:
    made = runtime()
    first = FakeHook(close_error=SystemExit(43))
    second = FakeHook(close_error=RuntimeError("private-first"))
    object.__setattr__(made, "lifecycle_hooks", (first, second))
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    with pytest.raises(RuntimeError, match="api_lifecycle_unavailable") as caught:
        await context.__aexit__(None, None, None)
    assert caught.value.__context__ is None
    assert first.closes == second.closes == made.worker.closes == 1


async def test_close_first_system_exit_wins_over_later_ordinary_error() -> None:
    made = runtime()
    original = SystemExit(47)
    first = FakeHook(close_error=RuntimeError("private-later"))
    second = FakeHook(close_error=original)
    object.__setattr__(made, "lifecycle_hooks", (first, second))
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    with pytest.raises(SystemExit) as caught:
        await context.__aexit__(None, None, None)
    assert caught.value is original
    assert first.closes == second.closes == made.worker.closes == 1


@pytest.mark.parametrize("boundary", ["reconcile", "start", "close"])
async def test_lifespan_failures_are_stable_and_cleanup_owned_resources(
    boundary: str,
) -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()
    if boundary == "reconcile":
        made.store.reconcile_error = RuntimeError(marker)
    elif boundary == "start":
        made.worker.start_error = RuntimeError(marker)
    else:
        made.worker.close_error = RuntimeError(marker)
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    if boundary == "close":
        await context.__aenter__()
        operation = context.__aexit__(None, None, None)
    else:
        operation = context.__aenter__()
    with pytest.raises(RuntimeError, match="api_lifecycle_unavailable") as caught:
        await operation
    assert marker not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    if boundary == "start":
        assert made.worker.closes == 1


async def test_cancelled_start_cleans_worker_and_propagates() -> None:
    made = runtime()
    release = asyncio.Event()
    entered = asyncio.Event()

    async def blocked_start() -> None:
        made.worker.starts += 1
        entered.set()
        await release.wait()

    made.worker.start = blocked_start  # type: ignore[method-assign]
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    starting = asyncio.create_task(context.__aenter__())
    await entered.wait()
    starting.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert made.worker.closes == 1


async def test_lifespan_closes_started_hooks_on_partial_start() -> None:
    marker = "private-question token provider model /private/path postgresql://dsn"
    made = runtime()
    started = FakeHook()
    failed = FakeHook(start_error=RuntimeError(marker))
    object.__setattr__(made, "lifecycle_hooks", (started, failed))
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    with pytest.raises(RuntimeError, match="api_lifecycle_unavailable") as caught:
        await context.__aenter__()
    assert started.starts == started.closes == 1
    assert failed.starts == 1
    assert failed.closes == 0
    assert made.worker.closes == 1
    assert marker not in repr(caught.value)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(19)])
async def test_lifespan_start_base_error_cleans_then_propagates_original(
    error: BaseException,
) -> None:
    made = runtime()
    started = FakeHook()
    failed = FakeHook(start_error=error)
    object.__setattr__(made, "lifecycle_hooks", (started, failed))
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    with pytest.raises(type(error)) as caught:
        await context.__aenter__()
    assert caught.value is error
    assert started.closes == 1
    assert made.worker.closes == 1


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(23)])
async def test_lifespan_close_base_error_finishes_all_cleanup_then_propagates(
    error: BaseException,
) -> None:
    made = runtime()
    first = FakeHook()
    failed = FakeHook(close_error=error)
    object.__setattr__(made, "lifecycle_hooks", (first, failed))
    app = create_app(runtime=made)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    with pytest.raises(type(error)) as caught:
        await context.__aexit__(None, None, None)
    assert caught.value is error
    assert failed.closes == first.closes == 1
    assert made.worker.closes == 1
    contender = app.router.lifespan_context(app)
    with pytest.raises(RuntimeError, match="api_lifecycle_unavailable"):
        await contender.__aenter__()


async def test_capacity_one_concurrent_posts_create_exactly_one_run() -> None:
    made = runtime()
    made.worker.full = False
    app, client = await client_for(made)
    assert made.demo_issuer is not None
    token = made.demo_issuer.issue(
        session_id="owner-a", profile="fixture", ttl_seconds=300
    )
    # A real worker permit is covered independently; this fake makes the second
    # reservation observe the first as full until delivery completes.
    original = made.worker.reserve
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held_reserve() -> FakePermit:
        if entered.is_set():
            raise WorkerQueueFull("worker_queue_full")
        entered.set()
        permit = await original()
        original_deliver = permit.deliver

        async def deliver(job: RunJob) -> None:
            await release.wait()
            await original_deliver(job)

        permit.deliver = deliver  # type: ignore[method-assign]
        return permit

    made.worker.reserve = held_reserve  # type: ignore[method-assign]
    async with app.router.lifespan_context(app), client:
        first = asyncio.create_task(
            client.post(
                "/chat",
                headers={"Authorization": f"Bearer {token}"},
                json=payload(),
            )
        )
        await entered.wait()
        second = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=payload()
        )
        release.set()
        first_response = await first
    assert sorted((first_response.status_code, second.status_code)) == [202, 503]
    assert made.store.creates == 1
