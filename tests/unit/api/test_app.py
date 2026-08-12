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
        if self.worker.delivery_error:
            raise WorkerUnavailable("worker_closed")
        self.worker.jobs.append(job)

    async def cancel(self) -> None:
        self.used = True


@dataclass
class FakeWorker:
    full: bool = False
    delivery_error: bool = False
    cancel_delivery: bool = False
    jobs: list[RunJob] = field(default_factory=list)
    starts: int = 0
    closes: int = 0

    async def start(self) -> None:
        self.starts += 1

    async def reserve(self) -> FakePermit:
        if self.full:
            raise WorkerQueueFull("worker_queue_full")
        return FakePermit(self)

    async def aclose(self) -> None:
        self.closes += 1


@dataclass
class FakeStore:
    runs: dict[UUID, tuple[str, RunView]] = field(default_factory=dict)
    creates: int = 0
    reconciles: int = 0
    delivery_failures: int = 0
    create_entered: asyncio.Event = field(default_factory=asyncio.Event)
    create_release: asyncio.Event | None = None

    async def create(self, run: RunRecord) -> RunRecord:
        self.create_entered.set()
        if self.create_release is not None:
            await self.create_release.wait()
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
        stored = self.runs.get(run_id)
        return stored[1] if stored is not None and stored[0] == session_id else None

    async def reconcile_expired(self) -> int:
        self.reconciles += 1
        return 0

    async def fail_delivery(self, run_id: UUID, event: object) -> bool:
        self.delivery_failures += 1
        return True


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
