"""Fixture-level L2 HTTP acceptance tests with no live provider calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from specpilot.api.app import create_app
from specpilot.contracts.egress import L2AtomicClaimPayload, L2DesignPayload
from specpilot.providers.base import ProviderResponse
from specpilot.runs.contracts import RunStatus
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
    assert [event["kind"] for event in body["events"]].index("compliance_summary") < [
        event["kind"] for event in body["events"]
    ].index("semantic_summary")


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
