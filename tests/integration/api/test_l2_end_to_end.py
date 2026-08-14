"""Fixture-level L2 HTTP acceptance tests with no live provider calls."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from specpilot.agents.evidence import EvidenceAgent
from specpilot.api.app import create_app
from specpilot.checkpoints.contracts import CheckpointStage, RunCheckpoint
from specpilot.contracts.egress import L2AtomicClaimPayload, L2DesignPayload
from specpilot.providers.base import ProviderResponse
from specpilot.runs.contracts import RunStatus
from specpilot.runtime import RunWorker
from tests.integration.api import test_l1_end_to_end as l1_fixture

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _wait_terminal(runtime: Any, run_id: UUID, owner: str) -> Any:
    for _ in range(300):
        view = await runtime.store.read_owned(run_id, owner)
        if view is not None and view.status not in {
            RunStatus.QUEUED,
            RunStatus.RUNNING,
        }:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("L2 fixture worker did not reach a terminal state")


def _l2_request(runtime: Any, *, root: str) -> dict[str, str]:
    return {
        "question": "Which retry requirement applies?",
        "request_id": str(uuid4()),
        "evaluation_root_id": root,
        "task_level": "L2",
        "source_manifest_id": runtime.binding.source_manifest_id,
        "corpus_manifest_id": runtime.binding.corpus_manifest_id,
    }


def _adapter(runtime: Any) -> Any:
    adapters = runtime.worker._answer_transport._PolicyBoundTransport__adapters
    return next(iter(adapters.values()))


def _egress_stages(trace: dict[str, Any]) -> list[str]:
    return [
        event["stage"] for event in trace["events"] if event["kind"] == "egress_summary"
    ]


class _ProcessLostAfterRecovery(RuntimeError):
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


async def _usage_snapshot(dsn: str, root: str) -> tuple[str, dict[str, Any]]:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (
            await connection.execute(
                "SELECT policy_hash, usage_snapshot FROM egress_evaluation_root "
                "WHERE evaluation_root_id = %s",
                (root,),
            )
        ).fetchone()
    assert row is not None and isinstance(row[1], dict)
    return row[0], row[1]


async def _reservations(
    dsn: str, run_id: UUID
) -> dict[UUID, tuple[str, str, str]]:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        rows = await (
            await connection.execute(
                "SELECT reservation_id, idempotency_key, stage, evaluation_root_id "
                "FROM egress_reservation WHERE run_id = %s",
                (str(run_id),),
            )
        ).fetchall()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


async def _disclosure_charge(dsn: str, reservation_ids: set[UUID]) -> tuple[int, int]:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (
            await connection.execute(
                "SELECT COALESCE(sum(token_count), 0), COALESCE(sum(byte_count), 0) "
                "FROM egress_reservation_disclosure "
                "WHERE reservation_id = ANY(%s)",
                (list(reservation_ids),),
            )
        ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


async def _disclosure_facts(
    dsn: str, reservation_ids: set[UUID]
) -> dict[str, tuple[int, int]]:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        rows = await (
            await connection.execute(
                "SELECT disclosure_id, token_count, byte_count "
                "FROM egress_reservation_disclosure "
                "WHERE reservation_id = ANY(%s)",
                (list(reservation_ids),),
            )
        ).fetchall()
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


async def _remove_egress_trace(dsn: str, run_id: UUID, reservation_id: UUID) -> None:
    """Model the old receipt/checkpoint crash gap before client resume."""
    async with (
        await psycopg.AsyncConnection.connect(dsn) as connection,
        connection.transaction(),
    ):
        deleted = await connection.execute(
            "DELETE FROM specpilot_run_event WHERE run_id = %s "
            "AND kind = 'egress_summary' "
            "AND payload ->> 'reservation_id' = %s",
            (run_id, str(reservation_id)),
        )
        assert deleted.rowcount == 1


async def test_l2_happy_path_records_every_outward_stage_in_owner_trace(
    clean_ledger: str,
    qdrant_url: str,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified L2 result carries planning, compliance, and semantic audit facts."""
    del qdrant_url
    async with l1_fixture._runtime(clean_ledger, tmp_path, monkeypatch) as (
        runtime,
        issuer,
    ):
        owner = "l2-e2e-owner"
        token = issuer.issue(session_id=owner, profile="fixture", ttl_seconds=300)
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        payload = _l2_request(runtime, root="l2-e2e-root")
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
            terminal = await _wait_terminal(runtime, run_id, owner)
            trace = await client.get(f"/runs/{run_id}", headers=headers)

    assert terminal.status is RunStatus.ANSWERED
    assert trace.status_code == 200
    body = trace.json()
    assert body["status"] == "answered"
    assert _egress_stages(body) == [
        "planning",
        "compliance",
        "verifier",
    ]
    kinds = [event["kind"] for event in body["events"]]
    tools = [event for event in body["events"] if event["kind"] == "tool_finished"]
    assert [event["tool"] for event in tools] == ["search_clauses", "get_clause"]
    assert all(set(event) == {
        "kind", "sequence", "step_id", "tool", "argument_keys",
        "result_count", "duration_ms", "retry_count", "error_code",
    } for event in tools)
    verifier = [
        event for event in body["events"] if event["kind"] == "verifier_summary"
    ]
    assert len(verifier) == 1
    assert verifier[0]["checks"] == [
        {
            "evidence_id": verifier[0]["checks"][0]["evidence_id"],
            "passed": True,
            "fault_code": None,
        }
    ]
    assert kinds.index("tool_finished") < kinds.index("compliance_summary")
    assert kinds.index("compliance_summary") < kinds.index("verifier_summary")
    assert kinds.index("verifier_summary") < kinds.index("semantic_summary")
    assert payload["question"] not in trace.text
    assert "A sender" not in trace.text


async def test_deterministic_mismatch_recovers_before_the_first_semantic_send(
    clean_ledger: str,
    qdrant_url: str,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad Compliance citation consumes recovery, not an unverified semantic call."""
    del qdrant_url
    async with l1_fixture._runtime(clean_ledger, tmp_path, monkeypatch) as (
        runtime,
        issuer,
    ):
        adapter = _adapter(runtime)
        original_send = adapter.send
        replaced = False

        async def send(payload: Any) -> ProviderResponse:
            nonlocal replaced
            response = await original_send(payload)
            if isinstance(payload, L2DesignPayload) and not replaced:
                replaced = True
                return response.model_copy(
                    update={
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "claim": (
                                            "The design satisfies the cited "
                                            "requirement."
                                        ),
                                        "proposed_verdict": "compliant",
                                        "evidence_ids": ["f" * 64],
                                        "rationale": "ephemeral fixture rationale",
                                    }
                                ]
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    }
                )
            return response

        monkeypatch.setattr(adapter, "send", send)
        runner_errors: list[BaseException] = []
        original_runner = runtime.worker._l2_runner

        async def observing_runner(context: Any) -> Any:
            try:
                return await original_runner(context)
            except BaseException as error:
                runner_errors.append(error)
                raise

        monkeypatch.setattr(runtime.worker, "_l2_runner", observing_runner)
        owner = "l2-deterministic-recovery-owner"
        token = issuer.issue(session_id=owner, profile="fixture", ttl_seconds=300)
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
            ) as client,
        ):
            accepted = await client.post(
                "/chat", headers=headers, json=_l2_request(runtime, root="l2-det-root")
            )
            assert accepted.status_code == 202
            run_id = UUID(accepted.json()["run_id"])
            for _ in range(300):
                if runner_errors:
                    raise runner_errors[0]
                view = await runtime.store.read_owned(run_id, owner)
                if view is not None and view.status not in {
                    RunStatus.QUEUED,
                    RunStatus.RUNNING,
                }:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("L2 fixture worker did not reach a terminal state")
            terminal = view
            trace = await client.get(f"/runs/{run_id}", headers=headers)

    assert terminal.status is RunStatus.ANSWERED
    body = trace.json()
    assert _egress_stages(body) == ["planning", "compliance", "verifier"]
    assert [
        event["supports"]
        for event in body["events"]
        if event["kind"] == "semantic_summary"
    ] == [True]
    assert (
        len([event for event in body["events"] if event["kind"] == "recovery_summary"])
        == 1
    )
    assert (
        len([call for call in adapter.calls if isinstance(call, L2AtomicClaimPayload)])
        == 1
    )
    verifier_indexes = [
        index
        for index, event in enumerate(body["events"])
        if event["kind"] == "verifier_summary"
    ]
    recovery_tool_indexes = [
        index
        for index, event in enumerate(body["events"])
        if event["kind"] == "tool_finished"
        and event["step_id"].startswith("recovery_")
    ]
    semantic_index = next(
        index
        for index, event in enumerate(body["events"])
        if event["kind"] == "semantic_summary"
    )
    assert len(verifier_indexes) == 2
    assert all(
        check["passed"] is False
        for check in body["events"][verifier_indexes[0]]["checks"]
    )
    assert all(
        check["passed"] is True
        for check in body["events"][verifier_indexes[1]]["checks"]
    )
    assert verifier_indexes[0] < min(recovery_tool_indexes)
    assert max(recovery_tool_indexes) < verifier_indexes[1] < semantic_index
    assert "ephemeral fixture rationale" not in trace.text


async def test_second_semantic_rejection_stays_insufficient_after_one_recovery(
    clean_ledger: str,
    qdrant_url: str,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A semantic distractor never receives a second directed retrieval attempt."""
    del qdrant_url
    async with l1_fixture._runtime(clean_ledger, tmp_path, monkeypatch) as (
        runtime,
        issuer,
    ):
        adapter = _adapter(runtime)
        original_send = adapter.send
        semantic_sends = 0

        async def send(payload: Any) -> ProviderResponse:
            nonlocal semantic_sends
            response = await original_send(payload)
            if not isinstance(payload, L2AtomicClaimPayload):
                return response
            semantic_sends += 1
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
        owner = "l2-semantic-recovery-owner"
        token = issuer.issue(session_id=owner, profile="fixture", ttl_seconds=300)
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        assert runtime.checkpoint_store is not None
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
            ) as client,
        ):
            accepted = await client.post(
                "/chat", headers=headers, json=_l2_request(runtime, root="l2-sem-root")
            )
            assert accepted.status_code == 202
            run_id = UUID(accepted.json()["run_id"])
            terminal = await _wait_terminal(runtime, run_id, owner)
            trace = await client.get(f"/runs/{run_id}", headers=headers)
            checkpoint = await runtime.checkpoint_store.read(run_id)

    assert terminal.status is RunStatus.ANSWERED
    assert checkpoint is not None
    assert checkpoint.completed_results[0].verdict.value == "insufficient_evidence"
    assert checkpoint.completed_results[0].reason_code == "exception_missing"
    assert semantic_sends == 2
    body = trace.json()
    assert _egress_stages(body) == [
        "planning",
        "compliance",
        "verifier",
        "verifier",
    ]
    assert (
        len([event for event in body["events"] if event["kind"] == "recovery_summary"])
        == 1
    )
    recovery = next(
        event for event in body["events"] if event["kind"] == "recovery_summary"
    )
    assert recovery["kind_name"] == "expand_references"
    assert recovery["reason"] == "exception_missing"
    verifier_indexes = [
        index
        for index, event in enumerate(body["events"])
        if event["kind"] == "verifier_summary"
    ]
    semantic_indexes = [
        index
        for index, event in enumerate(body["events"])
        if event["kind"] == "semantic_summary"
    ]
    recovery_index = next(
        index
        for index, event in enumerate(body["events"])
        if event["kind"] == "recovery_summary"
    )
    assert len(verifier_indexes) == 2
    assert len(semantic_indexes) == 2
    assert (
        verifier_indexes[0]
        < semantic_indexes[0]
        < recovery_index
        < verifier_indexes[1]
        < semantic_indexes[1]
    )
    assert "ephemeral fixture rationale" not in trace.text


async def test_client_resume_rebuilds_local_state_and_charges_lost_model_generation(
    clean_ledger: str,
    qdrant_url: str,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery survives process loss; only lost model results are sent again."""
    del qdrant_url
    question = "Which retry requirement applies? CLIENT-RESUME-PRIVATE-PROSE"
    collection_calls = 0
    original_collect = EvidenceAgent.collect

    async def counting_collect(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal collection_calls
        collection_calls += 1
        return await original_collect(self, *args, **kwargs)

    monkeypatch.setattr(EvidenceAgent, "collect", counting_collect)
    async with l1_fixture._runtime(clean_ledger, tmp_path, monkeypatch) as (
        runtime,
        issuer,
    ):
        assert runtime.checkpoint_store is not None
        checkpoint_store = runtime.checkpoint_store
        adapter = _adapter(runtime)
        original_send = adapter.send
        semantic_calls = 0

        async def reject_first_semantic(payload: Any) -> ProviderResponse:
            nonlocal semantic_calls
            response = await original_send(payload)
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

        monkeypatch.setattr(adapter, "send", reject_first_semantic)
        original_write = checkpoint_store.write

        async def lose_process_after_recovery(
            previous_version: int | None, checkpoint: RunCheckpoint
        ) -> RunCheckpoint:
            saved = await original_write(previous_version, checkpoint)
            if saved.stage is CheckpointStage.RECOVERY_COMPLETED:
                raise _ProcessLostAfterRecovery(saved.stage.value)
            return saved

        monkeypatch.setattr(checkpoint_store, "write", lose_process_after_recovery)
        owner = "l2-client-resume-e2e-owner"
        root = "l2-client-resume-e2e-root"
        token = issuer.issue(session_id=owner, profile="fixture", ttl_seconds=300)
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        request = _l2_request(runtime, root=root)
        request["question"] = question
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
            ) as client,
        ):
            accepted = await client.post("/chat", headers=headers, json=request)
            assert accepted.status_code == 202
            run_id = UUID(accepted.json()["run_id"])
            interrupted_checkpoint = await _wait_checkpoint(
                checkpoint_store, run_id, CheckpointStage.RECOVERY_COMPLETED
            )
            assert interrupted_checkpoint.recovery_attempted is True
            policy_before, usage_before = await _usage_snapshot(clean_ledger, root)
            reservations_before = await _reservations(clean_ledger, run_id)
            assert collection_calls == 1
            lost_trace_reservation = next(
                reservation_id
                for reservation_id, (_, stage, _) in reservations_before.items()
                if stage == "verifier"
            )
            await _remove_egress_trace(
                clean_ledger, run_id, lost_trace_reservation
            )

            await _restart_worker(runtime)
            future = datetime.now(tz=UTC) + timedelta(seconds=60)
            assert await runtime.store.reconcile_expired(future) == 1
            monkeypatch.setattr(checkpoint_store, "write", original_write)
            resumed = await client.post(
                f"/runs/{run_id}/resume",
                headers=headers,
                json={"question": question, "resume_key": "client-recovery-resume"},
            )
            assert resumed.status_code == 202
            assert resumed.json()["attempt"] == 2
            terminal = await _wait_terminal(runtime, run_id, owner)
            trace = await client.get(f"/runs/{run_id}", headers=headers)

            final_checkpoint = await checkpoint_store.read(run_id)
            assert final_checkpoint is not None
            assert final_checkpoint.stage is CheckpointStage.COMPLETED
            assert final_checkpoint.evaluation_root_id == root
            assert (
                final_checkpoint.tool_attempts_used
                == interrupted_checkpoint.tool_attempts_used
            )
            assert final_checkpoint.recovery_attempted is True
            assert collection_calls == 1

            policy_after, usage_after = await _usage_snapshot(clean_ledger, root)
            reservations_after = await _reservations(clean_ledger, run_id)

    assert terminal.status is RunStatus.ANSWERED
    assert trace.status_code == 200
    assert question not in trace.text
    assert policy_after == policy_before
    new_ids = set(reservations_after) - set(reservations_before)
    assert {reservations_after[item][1] for item in new_ids} == {
        "compliance",
        "verifier",
    }
    assert all(reservations_after[item][2] == root for item in new_ids)
    added_tokens, added_bytes = await _disclosure_charge(clean_ledger, new_ids)
    assert added_tokens > 0 and added_bytes > 0
    prior_facts = await _disclosure_facts(
        clean_ledger, set(reservations_before)
    )
    new_facts = await _disclosure_facts(clean_ledger, new_ids)
    novel_facts = {
        disclosure_id: size
        for disclosure_id, size in new_facts.items()
        if disclosure_id not in prior_facts
    }
    assert usage_after["root_unique_tokens"] == (
        usage_before["root_unique_tokens"]
        + sum(size[0] for size in novel_facts.values())
    )
    assert usage_after["root_unique_bytes"] == (
        usage_before["root_unique_bytes"]
        + sum(size[1] for size in novel_facts.values())
    )
    assert usage_after["root_transmitted_tokens"] == (
        usage_before["root_transmitted_tokens"] + added_tokens
    )
    assert usage_after["root_transmitted_bytes"] == (
        usage_before["root_transmitted_bytes"] + added_bytes
    )
    keys_before = {value[0] for value in reservations_before.values()}
    keys_after = {value[0] for value in reservations_after.values()}
    assert any(key.endswith("-compliance-initial-g0") for key in keys_before)
    assert any(key.endswith("-compliance-initial-g1") for key in keys_after)
    assert len(new_ids) == 2
    body = trace.json()
    checkpoint_versions = [
        event["checkpoint_version"]
        for event in body["events"]
        if event["kind"] == "checkpoint_summary"
    ]
    assert len(checkpoint_versions) == len(set(checkpoint_versions))
    assert max(checkpoint_versions) == final_checkpoint.checkpoint_version
    trace_reservations = [
        UUID(event["reservation_id"])
        for event in body["events"]
        if event["kind"] == "egress_summary" and event["admitted"]
    ]
    assert len(trace_reservations) == len(set(trace_reservations))
    assert set(trace_reservations) == set(reservations_after)
    recovery_events = [
        event for event in body["events"] if event["kind"] == "recovery_summary"
    ]
    recovery_tools = [
        event
        for event in body["events"]
        if event["kind"] == "tool_finished"
        and event["step_id"].startswith("recovery_")
    ]
    verifier_events = [
        event for event in body["events"] if event["kind"] == "verifier_summary"
    ]
    semantic_events = [
        event for event in body["events"] if event["kind"] == "semantic_summary"
    ]
    assert len(recovery_events) == 1
    assert recovery_tools
    for events in (recovery_events, recovery_tools, verifier_events):
        closed_payloads = [
            json.dumps(
                {key: value for key, value in event.items() if key != "sequence"},
                sort_keys=True,
            )
            for event in events
        ]
        assert len(closed_payloads) == len(set(closed_payloads))
    verifier_reservations = {
        reservation_id
        for reservation_id, (_, stage, _) in reservations_after.items()
        if stage == "verifier"
    }
    assert len(semantic_events) == len(verifier_reservations) == 2
    kinds = [event["kind"] for event in body["events"]]
    assert (
        kinds.index("semantic_summary")
        < kinds.index("recovery_summary")
        < len(kinds) - 1 - kinds[::-1].index("semantic_summary")
    )
