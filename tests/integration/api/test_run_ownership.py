from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from specpilot.api.app import create_app
from specpilot.api.dependencies import ApiRunBinding, ApiRuntime
from specpilot.runs.contracts import RunRecord, RunStatus
from specpilot.runs.postgres import PostgresRunStore
from specpilot.runtime import RunJob, WorkerUnavailable
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier

pytestmark = pytest.mark.integration

POLICY = "a" * 64
CORPUS = "b" * 64
SOURCE = "c" * 64
NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


@dataclass
class Permit:
    worker: Worker
    used: bool = False

    async def deliver(self, job: RunJob) -> None:
        self.used = True
        if self.worker.fail_delivery:
            raise WorkerUnavailable("worker_closed")

    async def cancel(self) -> None:
        self.used = True


@dataclass
class Worker:
    fail_delivery: bool = False

    async def start(self) -> None:
        pass

    async def reserve(self) -> Permit:
        return Permit(self)

    async def aclose(self) -> None:
        pass


async def _seed_bindings(dsn: str) -> None:
    import psycopg

    ledger_id = UUID("00000000-0000-0000-0000-000000000003")
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        await connection.execute(
            "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
            "VALUES (%s, 'egress-policy/v1')",
            (POLICY,),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger "
            "(corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
            "unique_excerpts, unique_tokens, unique_bytes) "
            "VALUES (%s, %s, %s, '{}', 0, 0, 0)",
            (ledger_id, CORPUS, POLICY),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger_head "
            "(corpus_manifest_id, corpus_ledger_id) VALUES (%s, %s)",
            (CORPUS, ledger_id),
        )
        await connection.commit()


def _runtime(dsn: str, clock: Clock, worker: Worker | None = None) -> ApiRuntime:
    store = PostgresRunStore(dsn, clock=clock, queue_lease_seconds=5)
    secret = b"s" * 32
    issuer = SessionIssuer(secret=secret, audience="specpilot-api", clock=clock)
    return ApiRuntime(
        store=store,
        worker=worker or Worker(),
        verifier=SessionVerifier(
            secret=secret, audience="specpilot-api", profile="fixture", clock=clock
        ),
        binding=ApiRunBinding(
            profile="fixture",
            source_manifest_id=SOURCE,
            corpus_manifest_id=CORPUS,
            policy_hash=POLICY,
            configuration_hash="d" * 64,
            prompt_id="l1-answer-v1",
            prompt_hash="e" * 64,
            provider_id="provider-a",
            model_id="model-a",
            build_job=lambda run_id, question, request: RunJob(
                run_id=run_id,
                question=question,
                planner_context=object(),
                corpus_manifest_id=request.corpus_manifest_id,
                answer_context={},
            ),
        ),
        bind_host="127.0.0.1",
        demo_issuer=issuer,
        postgres_health=_healthy,
        mcp_health=_healthy,
    )


async def _healthy() -> bool:
    return True


def _payload() -> dict[str, str]:
    return {
        "question": "never persist this question",
        "request_id": str(uuid4()),
        "evaluation_root_id": "root-1",
        "task_level": "L1",
        "source_manifest_id": SOURCE,
        "corpus_manifest_id": CORPUS,
    }


@pytest.mark.anyio
async def test_postgres_owner_boundary_and_sanitized_trace(clean_ledger: str) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    runtime = _runtime(clean_ledger, clock)
    issuer = runtime.demo_issuer
    assert issuer is not None
    owner = issuer.issue(session_id="owner-a", profile="fixture", ttl_seconds=300)
    foreign = issuer.issue(session_id="owner-b", profile="fixture", ttl_seconds=300)
    app = create_app(runtime=runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        created = await client.post(
            "/chat", headers={"Authorization": f"Bearer {owner}"}, json=_payload()
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        foreign_view = await client.get(
            f"/runs/{run_id}", headers={"Authorization": f"Bearer {foreign}"}
        )
        owned = await client.get(
            f"/runs/{run_id}", headers={"Authorization": f"Bearer {owner}"}
        )
    assert foreign_view.status_code == 404
    assert owned.status_code == 200
    assert owned.json()["status"] == "queued"
    assert "never persist this question" not in owned.text


@pytest.mark.anyio
async def test_delivery_failure_is_interrupted_without_provider_state(
    clean_ledger: str,
) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    runtime = _runtime(clean_ledger, clock, Worker(fail_delivery=True))
    issuer = runtime.demo_issuer
    assert issuer is not None
    token = issuer.issue(session_id="owner-a", profile="fixture", ttl_seconds=300)
    app = create_app(runtime=runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        response = await client.post(
            "/chat", headers={"Authorization": f"Bearer {token}"}, json=_payload()
        )
        assert response.status_code == 503
        import psycopg

        async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
            row = await (
                await connection.execute(
                    "SELECT status, terminal_reason FROM specpilot_run"
                )
            ).fetchone()
    assert row == (RunStatus.INTERRUPTED.value, "queue_delivery_failed")


@pytest.mark.anyio
async def test_store_delivery_failure_rejects_any_other_terminal(
    clean_ledger: str,
) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    run = RunRecord(
        run_id=uuid4(), request_id=uuid4(), session_id="owner-a", task_level="L1",
        profile="fixture", source_manifest_id=SOURCE, corpus_manifest_id=CORPUS,
        policy_hash=POLICY, configuration_hash="d" * 64,
        prompt_id="l1-answer-v1", prompt_hash="e" * 64,
        provider_id="provider-a", model_id="model-a", query_hash="f" * 64,
        status=RunStatus.QUEUED, terminal_reason=None, created_at=clock(),
        started_at=None, completed_at=None, lease_owner="placeholder",
        lease_expires_at=clock() + timedelta(seconds=1), last_heartbeat_at=None,
    )
    await store.create(run)
    from specpilot.runs.contracts import TerminalEvent
    from specpilot.runs.postgres import RunStoreValidationError

    with pytest.raises(RunStoreValidationError, match="invalid_run_data"):
        await store.fail_delivery(
            run.run_id,
            TerminalEvent(
                sequence=1,
                status=RunStatus.FAILED,
                reason="provider_timeout",
            ),
        )


@pytest.mark.anyio
async def test_startup_reconciliation_persists_expired_queued_run(
    clean_ledger: str,
) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    runtime = _runtime(clean_ledger, clock)
    run = RunRecord(
        run_id=uuid4(), request_id=uuid4(), session_id="owner-a", task_level="L1",
        profile="fixture", source_manifest_id=SOURCE, corpus_manifest_id=CORPUS,
        policy_hash=POLICY, configuration_hash="d" * 64,
        prompt_id="l1-answer-v1", prompt_hash="e" * 64,
        provider_id="provider-a", model_id="model-a", query_hash="f" * 64,
        status=RunStatus.QUEUED, terminal_reason=None, created_at=clock(),
        started_at=None, completed_at=None, lease_owner="placeholder",
        lease_expires_at=clock() + timedelta(seconds=1), last_heartbeat_at=None,
    )
    await runtime.store.create(run)
    clock.value += timedelta(seconds=6)
    app = create_app(runtime=runtime)
    async with app.router.lifespan_context(app):
        pass

    view = await runtime.store.read_owned(run.run_id, "owner-a")
    assert view is not None
    assert view.status is RunStatus.INTERRUPTED
    assert view.reason == "lease_expired"
