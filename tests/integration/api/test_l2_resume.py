"""Real API/store/worker joins for owner-assisted L2 process recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg import sql

from specpilot.api.app import create_app
from specpilot.checkpoints.contracts import CheckpointStage, RunCheckpoint
from specpilot.contracts.egress import L2AtomicClaimPayload
from specpilot.providers.base import ProviderResponse
from specpilot.providers.fake import FakeProvider
from specpilot.runs.contracts import RunStatus
from specpilot.runtime import RunWorker
from tests.integration.api import test_l1_end_to_end as l1_fixture

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

_QUESTION_SENTINEL = "resume-sentinel-question-must-never-enter-postgres"
_EXCERPT_SENTINEL = "resume-sentinel-excerpt-must-leave-but-never-persist"
_NONTERMINAL = (
    CheckpointStage.PLANNED,
    CheckpointStage.EVIDENCE_COLLECTED,
    CheckpointStage.CANDIDATE_BUILT,
    CheckpointStage.DETERMINISTIC_VERIFIED,
    CheckpointStage.RECOVERY_COMPLETED,
    CheckpointStage.SEMANTIC_VERIFIED,
)
_PERSISTED_SCOPES = (
    ("specpilot_run", "run_id = %s"),
    ("specpilot_run_event", "run_id = %s"),
    ("specpilot_run_checkpoint", "run_id = %s"),
    ("specpilot_run_attempt", "run_id = %s"),
    (
        "egress_policy_snapshot",
        "policy_hash = (SELECT policy_hash FROM specpilot_run WHERE run_id = %s)",
    ),
    (
        "egress_evaluation_root",
        "evaluation_root_id = (SELECT evaluation_root_id FROM specpilot_run "
        "WHERE run_id = %s)",
    ),
    (
        "egress_corpus_ledger",
        "corpus_manifest_id = (SELECT corpus_manifest_id FROM specpilot_run "
        "WHERE run_id = %s)",
    ),
    (
        "egress_corpus_ledger_head",
        "corpus_manifest_id = (SELECT corpus_manifest_id FROM specpilot_run "
        "WHERE run_id = %s)",
    ),
    ("egress_reservation", "run_id = %s::text"),
    (
        "egress_reservation_disclosure",
        "reservation_id IN (SELECT reservation_id FROM egress_reservation "
        "WHERE run_id = %s::text)",
    ),
    (
        "egress_route_disclosure",
        "corpus_manifest_id = (SELECT corpus_manifest_id FROM specpilot_run "
        "WHERE run_id = %s)",
    ),
    (
        "egress_attempt",
        "reservation_id IN (SELECT reservation_id FROM egress_reservation "
        "WHERE run_id = %s::text)",
    ),
    ("egress_run_seal", "run_id = %s::text"),
)


class _WorkerStoppedAfterCheckpoint(RuntimeError):
    pass


async def _wait_checkpoint(
    store: Any, run_id: UUID, stage: CheckpointStage
) -> RunCheckpoint:
    for _ in range(300):
        checkpoint = await store.read(run_id)
        if checkpoint is not None and checkpoint.stage is stage:
            return checkpoint
        await asyncio.sleep(0.01)
    raise AssertionError(f"worker did not persist {stage.value}")


async def _wait_terminal(runtime: Any, run_id: UUID, owner: str) -> Any:
    for _ in range(300):
        view = await runtime.store.read_owned(run_id, owner)
        if view is not None and view.status not in {
            RunStatus.QUEUED,
            RunStatus.RUNNING,
        }:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("resumed worker did not reach a terminal state")


def _fixture_adapter(runtime: Any) -> FakeProvider:
    transport = runtime.worker._answer_transport
    adapters = transport._PolicyBoundTransport__adapters
    adapter = next(iter(adapters.values()))
    assert isinstance(adapter, FakeProvider)
    return adapter


def _force_one_semantic_recovery(runtime: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _fixture_adapter(runtime)
    original = adapter.send
    semantic_calls = 0

    async def send(payload: Any) -> ProviderResponse:
        nonlocal semantic_calls
        response = await original(payload)
        if not isinstance(payload, L2AtomicClaimPayload):
            return response
        semantic_calls += 1
        if semantic_calls != 1:
            return response
        return response.model_copy(
            update={
                "content": json.dumps(
                    {
                        "supports_verdict": False,
                        "evidence": [
                            {
                                "evidence_id": item.content_hash,
                                "supports": False,
                            }
                            for item in payload.evidence_excerpts
                        ],
                        "reason": "exception_missing",
                        "rationale": "ephemeral fixture rationale",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            }
        )

    monkeypatch.setattr(adapter, "send", send)


async def _restart_worker(runtime: Any) -> RunWorker:
    stopped = runtime.worker
    await stopped.aclose()
    restarted = RunWorker(
        store=stopped._store,
        planner=stopped._planner,
        evidence_agent=stopped._evidence_agent,
        answer_transport=stopped._answer_transport,
        worker_id=stopped._worker_id,
        queue_capacity=stopped._queue_capacity,
        lease_seconds=stopped._lease_seconds,
        heartbeat_interval_seconds=stopped._heartbeat_interval,
        answer_runner=stopped._answer_runner,
        l2_runner=stopped._l2_runner,
    )
    object.__setattr__(runtime, "worker", restarted)
    await restarted.start()
    return restarted


async def _assert_sentinels_absent(dsn: str, run_id: UUID) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        for table, predicate in _PERSISTED_SCOPES:
            statement = sql.SQL(
                "SELECT to_jsonb(persisted)::text FROM {} AS persisted WHERE "
            ).format(sql.Identifier(table)) + sql.SQL(predicate)
            rows = await (await connection.execute(statement, (run_id,))).fetchall()
            serialized = "\n".join(row[0] for row in rows)
            assert _QUESTION_SENTINEL not in serialized, table
            assert _EXCERPT_SENTINEL not in serialized, table


async def _reservation_keys(dsn: str, run_id: UUID) -> tuple[tuple[str, str], ...]:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        rows = await (
            await connection.execute(
                "SELECT idempotency_key, evaluation_root_id "
                "FROM egress_reservation WHERE run_id = %s "
                "ORDER BY idempotency_key",
                (str(run_id),),
            )
        ).fetchall()
    return tuple((row[0], row[1]) for row in rows)


@pytest.mark.parametrize("stop_stage", _NONTERMINAL, ids=lambda stage: stage.value)
async def test_owner_resume_preserves_checkpoint_accounting_across_worker_loss(
    stop_stage: CheckpointStage,
    clean_ledger: str,
    qdrant_url: str,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qdrant_url
    instrumented_xml = l1_fixture.TOOL_RFC_XML.replace(
        "record it.", f"record it. {_EXCERPT_SENTINEL}"
    )
    assert _EXCERPT_SENTINEL in instrumented_xml
    monkeypatch.setattr(l1_fixture, "TOOL_RFC_XML", instrumented_xml)
    async with l1_fixture._runtime(clean_ledger, tmp_path, monkeypatch) as (
        runtime,
        issuer,
    ):
        assert runtime.checkpoint_store is not None
        checkpoint_store = runtime.checkpoint_store
        original_write = checkpoint_store.write
        if stop_stage is CheckpointStage.RECOVERY_COMPLETED:
            _force_one_semantic_recovery(runtime, monkeypatch)

        async def stop_after_target(
            previous_version: int | None, checkpoint: RunCheckpoint
        ) -> RunCheckpoint:
            saved = await original_write(previous_version, checkpoint)
            if saved.stage is stop_stage:
                raise _WorkerStoppedAfterCheckpoint(stop_stage.value)
            return saved

        monkeypatch.setattr(checkpoint_store, "write", stop_after_target)
        owner = "l2-resume-owner"
        token = issuer.issue(session_id=owner, profile="fixture", ttl_seconds=300)
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        payload = {
            "question": f"Which retry requirement applies? {_QUESTION_SENTINEL}",
            "request_id": str(uuid4()),
            "evaluation_root_id": "l2-resume-root",
            "task_level": "L2",
            "scenario_id": "l2_answered",
            "source_manifest_id": runtime.binding.source_manifest_id,
            "corpus_manifest_id": runtime.binding.corpus_manifest_id,
        }
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
            ) as client,
        ):
            accepted = await client.post("/chat", headers=headers, json=payload)
            assert accepted.status_code == 202
            run_id = UUID(accepted.json()["run_id"])
            interrupted_checkpoint = await _wait_checkpoint(
                checkpoint_store, run_id, stop_stage
            )
            restarted_worker = await _restart_worker(runtime)

            # The injected exception models worker-process loss. Expiration and
            # reconciliation, not the exception itself, make the run resumable.
            future = datetime.now(tz=UTC) + timedelta(seconds=60)
            assert await runtime.store.reconcile_expired(future) == 1
            interrupted = await runtime.store.read_owned(run_id, owner)
            assert interrupted is not None
            assert interrupted.status is RunStatus.INTERRUPTED
            assert interrupted.reason == "lease_expired"
            keys_before_resume = await _reservation_keys(clean_ledger, run_id)

            monkeypatch.setattr(checkpoint_store, "write", original_write)
            original_runner = restarted_worker._l2_runner
            resume_started = asyncio.Event()
            release_resume = asyncio.Event()
            runner_errors: list[BaseException] = []

            async def gated_runner(context: Any) -> Any:
                resume_started.set()
                await release_resume.wait()
                try:
                    return await original_runner(context)
                except BaseException as error:
                    runner_errors.append(error)
                    raise

            monkeypatch.setattr(restarted_worker, "_l2_runner", gated_runner)
            resume_body = {
                "question": f"Which retry requirement applies? {_QUESTION_SENTINEL}",
                "resume_key": f"resume-{stop_stage.value}",
            }
            resumed = await client.post(
                f"/runs/{run_id}/resume", headers=headers, json=resume_body
            )
            assert resumed.status_code == 202
            assert resumed.json() == {
                "run_id": str(run_id),
                "attempt": 2,
                "status": "queued",
            }
            await asyncio.wait_for(resume_started.wait(), timeout=2)

            replay = await client.post(
                f"/runs/{run_id}/resume", headers=headers, json=resume_body
            )
            leased = await client.post(
                f"/runs/{run_id}/resume",
                headers=headers,
                json={
                    "question": (
                        f"Which retry requirement applies? {_QUESTION_SENTINEL}"
                    ),
                    "resume_key": f"other-{stop_stage.value}",
                },
            )
            assert replay.status_code == 202
            assert replay.json() == resumed.json()
            assert leased.status_code == 409
            assert leased.json() == {"detail": "run_already_leased"}

            resumed_checkpoint = await checkpoint_store.read(run_id)
            assert resumed_checkpoint is not None
            assert resumed_checkpoint.attempt == 2
            assert resumed_checkpoint.evaluation_root_id == "l2-resume-root"
            assert (
                resumed_checkpoint.tool_attempts_used
                == interrupted_checkpoint.tool_attempts_used
            )
            assert (
                resumed_checkpoint.recovery_attempted
                is interrupted_checkpoint.recovery_attempted
            )
            assert (
                resumed_checkpoint.reconstruction_generations
                == interrupted_checkpoint.reconstruction_generations
            )
            await _assert_sentinels_absent(clean_ledger, run_id)

            release_resume.set()
            for _ in range(300):
                if runner_errors:
                    raise runner_errors[0]
                terminal = await runtime.store.read_owned(run_id, owner)
                if terminal is not None and terminal.status not in {
                    RunStatus.QUEUED,
                    RunStatus.RUNNING,
                }:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("resumed worker did not reach a terminal state")
            assert terminal.status in {RunStatus.ANSWERED, RunStatus.REFUSED}
            keys_after_resume = await _reservation_keys(clean_ledger, run_id)
            assert set(keys_before_resume).issubset(keys_after_resume)
            assert len(keys_after_resume) == len(set(keys_after_resume))
            assert all(root == "l2-resume-root" for _, root in keys_after_resume)
            disclosed_quotes = tuple(
                excerpt.quote
                for call in _fixture_adapter(runtime).calls
                for excerpt in getattr(call, "evidence_excerpts", ())
            )
            assert any(_EXCERPT_SENTINEL in quote for quote in disclosed_quotes)
            await _assert_sentinels_absent(clean_ledger, run_id)
