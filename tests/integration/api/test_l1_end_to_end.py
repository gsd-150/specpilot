from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest

from specpilot.agents.evidence import EvidenceAgent
from specpilot.agents.planner import Planner, PlannerContext
from specpilot.api.app import create_app
from specpilot.api.dependencies import ApiRunBinding, ApiRuntime
from specpilot.contracts.egress import (
    CorpusUsage,
    EgressPayload,
    L1OnlinePayload,
    L1PlanPayload,
)
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    ProviderUse,
    RfcSourceManifestDraft,
)
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.tool_metadata import build_rfc_tool_metadata
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.manifests.store import ManifestStore
from specpilot.mcp_server.app import create_app as create_mcp_app
from specpilot.mcp_server.client import StreamableMcpClient
from specpilot.mcp_server.services import McpToolServices, SearchBackendHit
from specpilot.providers.base import ProviderResponse, ResponseMetadata
from specpilot.providers.transport import PolicyBoundTransport
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit
from specpilot.runs.postgres import PostgresRunStore
from specpilot.runtime import RunJob, RunWorker
from specpilot.sessions.tokens import SessionIssuer, SessionVerifier
from tests.helpers import rfc_factory
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML
from tests.unit.manifests.test_source_manifest import assessment

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NOW = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
DOCUMENT_ID = "ietf-rfc-9999"
QUESTION = "Which retry requirement is stated?"


@dataclass(frozen=True, slots=True)
class _Search:
    corpus: LocalCorpus

    def search(
        self,
        query: str,
        *,
        corpus_manifest_id: str,
        document_ids: tuple[str, ...],
        normative_levels: tuple[str, ...],
        limit: int,
    ) -> Sequence[SearchBackendHit]:
        del normative_levels
        index = Bm25Index.build(self.corpus.indexable())
        return tuple(
            SearchBackendHit(
                locator=locator_for_unit(
                    corpus_manifest_id, self.corpus.get_clause(hit.unit_id)
                ),
                score=hit.score,
            )
            for hit in index.search(query, limit)
            if self.corpus.get_clause(hit.unit_id).document_id in document_ids
        )


class _Counter:
    provider_id = "fixture-provider"
    model_id = "fixture-model-v1"

    def count_tokens(self, text: str) -> int:
        return max(len(text.split()), 1)


class _TwoStageProvider:
    provider_id = "fixture-provider"
    model_id = "fixture-model-v1"

    def __init__(self) -> None:
        self.calls: list[EgressPayload] = []

    @property
    def token_counter(self) -> _Counter:
        return _Counter()

    async def send(self, payload: EgressPayload) -> ProviderResponse:
        self.calls.append(payload)
        if isinstance(payload, L1PlanPayload):
            content = json.dumps(
                {
                    "plan_id": "e2e-plan",
                    "steps": [
                        {
                            "step_id": "search",
                            "tool": "search_clauses",
                            "args": {
                                "query": payload.query,
                                "corpus_manifest_id": (
                                    payload.version.corpus_manifest_id
                                ),
                                "document_ids": [payload.version.document_id],
                                "normative_levels": [],
                                "limit": 3,
                            },
                            "depends_on": [],
                        },
                        {
                            "step_id": "read",
                            "tool": "get_clause",
                            "args": {
                                "corpus_manifest_id": (
                                    payload.version.corpus_manifest_id
                                ),
                                "document_id": payload.version.document_id,
                                "clauses": {
                                    "kind": "step_result",
                                    "step_id": "search",
                                    "take": 1,
                                },
                            },
                            "depends_on": ["search"],
                        },
                    ],
                },
                separators=(",", ":"),
            )
        else:
            assert isinstance(payload, L1OnlinePayload)
            content = json.dumps(
                {
                    "sufficient": True,
                    "answer": "The bounded fixture supports the answer.",
                    "citations": [
                        {"evidence_id": payload.evidence_excerpts[0].content_hash}
                    ],
                },
                separators=(",", ":"),
            )
        return ProviderResponse(
            provider_id=self.provider_id,
            model_id=self.model_id,
            content=content,
            metadata=ResponseMetadata(
                prompt_tokens=max(len(content.split()), 1),
                completion_tokens=max(len(content.split()), 1),
                finish_reason="stop",
                duration_ms=0,
                request_bytes=len(payload.model_dump_json().encode()),
            ),
        )


@dataclass(slots=True)
class _McpHook:
    app: Any
    client: StreamableMcpClient
    http_client: httpx.AsyncClient
    _owner: asyncio.Task[None] | None = None
    _ready: asyncio.Future[None] | None = None
    _close: asyncio.Future[None] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._close = loop.create_future()
        self._owner = asyncio.create_task(self._run())
        await asyncio.shield(self._ready)

    async def aclose(self) -> None:
        assert self._owner is not None
        assert self._close is not None
        self._close.set_result(None)
        await asyncio.shield(self._owner)

    async def _run(self) -> None:
        assert self._ready is not None
        assert self._close is not None
        async with (
            self.app.router.lifespan_context(self.app),
            self.http_client,
            self.client,
        ):
            self._ready.set_result(None)
            await self._close


def _policy() -> EgressPolicy:
    fields = EgressPolicy.load().model_dump(mode="json")
    fields["corpus_document_unique"][DOCUMENT_ID] = {
        "excerpts": 64,
        "tokens": 524_288,
        "bytes": 524_288,
    }
    return EgressPolicy.model_validate(fields)


async def _seed_epoch(dsn: str, corpus_id: str, policy_hash: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        ledger_id = uuid4()
        usage = CorpusUsage(
            corpus_manifest_id=corpus_id,
            policy_hash=policy_hash,
        )
        await connection.execute(
            "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
            "VALUES (%s, 'egress-policy/v1')",
            (policy_hash,),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger "
            "(corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
            "unique_excerpts, unique_tokens, unique_bytes) "
            "VALUES (%s, %s, %s, %s, 0, 0, 0)",
            (ledger_id, corpus_id, policy_hash, usage.model_dump_json()),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger_head "
            "(corpus_manifest_id, corpus_ledger_id) VALUES (%s, %s)",
            (corpus_id, ledger_id),
        )
        await connection.commit()


@asynccontextmanager
async def _runtime(
    dsn: str, tmp_path: Path
) -> AsyncIterator[tuple[ApiRuntime, SessionIssuer, _TwoStageProvider]]:
    xml_path = rfc_factory.write(tmp_path, "e2e.xml", TOOL_RFC_XML)
    verified = load_verified_rfc(xml_path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    corpus_id = hashlib.sha256(
        "\n".join(unit.unit_id for unit in corpus.units()).encode()
    ).hexdigest()
    source_store = ManifestStore(tmp_path / "source-manifests")
    initial = source_store.create_source_v2(
        RfcSourceManifestDraft(
            document_id=DOCUMENT_ID,
            document_version="2026-08",
            text_url="https://example.test/rfc9999.txt",
            xml_url="https://example.test/rfc9999.xml",
            text_sha256="f" * 64,
            xml_sha256=hashlib.sha256(xml_path.read_bytes()).hexdigest(),
            downloaded_at=datetime(2026, 8, 6, tzinfo=UTC),
            created_at=datetime(2026, 8, 6, 1, tzinfo=UTC),
        )
    )
    route = ProviderRouteBinding(
        provider_id="fixture-provider",
        endpoint_purpose="fixture-e2e",
        use=ProviderUse.ONLINE_MAIN,
    )
    source = source_store.create_successor_v2(
        initial,
        assessment=assessment(
            provider_id=route.provider_id,
            endpoint_purpose=route.endpoint_purpose,
            expires_at=datetime(2026, 8, 13, tzinfo=UTC),
        ),
        route_binding=route,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )
    services = McpToolServices(
        corpus=corpus,
        search_backend=_Search(corpus),
        tool_metadata=build_rfc_tool_metadata(
            corpus_manifest_id=corpus_id,
            documents=documents,
            units=corpus.units(),
            rfc_limits=RfcLimits(),
        ),
    )
    mcp_app = create_mcp_app(services)
    mcp_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app),
        base_url="http://127.0.0.1:8080",
    )
    mcp_client = StreamableMcpClient(
        "http://127.0.0.1:8080/mcp", http_client=mcp_http
    )
    hook = _McpHook(mcp_app, mcp_client, mcp_http)
    policy = _policy()
    await _seed_epoch(dsn, corpus_id, policy.policy_hash)
    provider = _TwoStageProvider()
    transport = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            policy, manifests=source_store, clock=lambda: NOW
        ),
        ledger=PostgresEgressLedger(
            dsn, policy=policy, manifests=source_store, clock=lambda: NOW
        ),
        adapters=(provider,),
    )
    store = PostgresRunStore(dsn)
    worker = RunWorker(
        store=store,
        planner=Planner(transport),
        evidence_agent=EvidenceAgent(mcp_client, corpus),
        answer_transport=transport,
        worker_id="e2e-worker",
        queue_capacity=2,
        lease_seconds=5,
        heartbeat_interval_seconds=0.1,
    )
    secret = b"e2e-session-secret-material-at-least-32-bytes"
    issuer = SessionIssuer(secret=secret, audience="specpilot-api", clock=lambda: NOW)
    runtime = ApiRuntime(
        store=store,
        worker=worker,
        verifier=SessionVerifier(
            secret=secret,
            audience="specpilot-api",
            profile="fixture",
            clock=lambda: NOW,
        ),
        binding=ApiRunBinding(
            profile="fixture",
            source_manifest_id=source.manifest_id,
            corpus_manifest_id=corpus_id,
            policy_hash=policy.policy_hash,
            configuration_hash="d" * 64,
            prompt_id="l1-answer-v1",
            prompt_hash="e" * 64,
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            build_job=lambda run_id, question, request: RunJob(
                run_id=run_id,
                question=question,
                planner_context=PlannerContext(
                    source_manifest=source,
                    corpus_manifest_id=corpus_id,
                    evaluation_root_id=request.evaluation_root_id,
                    run_id=str(run_id),
                    model_id=provider.model_id,
                    idempotency_key=f"{run_id}-planning",
                ),
                corpus_manifest_id=corpus_id,
                answer_context={
                    "model_id": provider.model_id,
                    "source_manifest": source,
                    "corpus_manifest_id": corpus_id,
                    "evaluation_root_id": request.evaluation_root_id,
                    "run_id": str(run_id),
                    "idempotency_key": f"{run_id}-answer",
                },
            ),
        ),
        bind_host="127.0.0.1",
        postgres_health=lambda: _health(dsn),
        mcp_health=lambda: _qdrant_health(),
        demo_issuer=issuer,
        lifecycle_hooks=(hook,),
    )
    yield runtime, issuer, provider


async def _health(dsn: str) -> bool:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (await connection.execute("SELECT 1")).fetchone()
    return row == (1,)


async def _qdrant_health() -> bool:
    url = "http://127.0.0.1:6334/healthz"
    async with httpx.AsyncClient(trust_env=False) as client:
        return (await client.get(url)).status_code == 200


async def test_l1_api_runs_real_planner_mcp_ledger_and_verifier(
    clean_ledger: str, tmp_path: Path, qdrant_url: str
) -> None:
    assert qdrant_url == "http://127.0.0.1:6334"
    async with _runtime(clean_ledger, tmp_path) as (runtime, issuer, provider):
        token = issuer.issue(
            session_id="e2e-owner", profile="fixture", ttl_seconds=300
        )
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
            ) as client,
        ):
            payload = {
                "question": QUESTION,
                "request_id": str(uuid4()),
                "evaluation_root_id": "e2e-root",
                "task_level": "L1",
                "source_manifest_id": runtime.binding.source_manifest_id,
                "corpus_manifest_id": runtime.binding.corpus_manifest_id,
            }
            accepted = await client.post("/chat", headers=headers, json=payload)
            assert accepted.status_code == 202
            run_id = accepted.json()["run_id"]
            response = None
            for _ in range(200):
                response = await client.get(f"/runs/{run_id}", headers=headers)
                if response.json()["status"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(0.01)

    assert response is not None
    assert response.status_code == 200
    trace = response.json()
    assert trace["status"] == "answered"
    assert trace["reason"] is None
    assert QUESTION not in response.text
    assert "A sender" not in response.text
    assert len(provider.calls) == 2
    kinds = [event["kind"] for event in trace["events"]]
    assert kinds.index("tool_finished") < kinds.index("verifier_summary")
    verifier = next(
        event for event in trace["events"] if event["kind"] == "verifier_summary"
    )
    assert verifier["checks"] and verifier["checks"][0]["passed"] is True
    egress = [event for event in trace["events"] if event["kind"] == "egress_summary"]
    assert [event["stage"] for event in egress] == ["planning", "evidence"]
    assert all(event["admitted"] is True for event in egress)
    assert all(event["reservation_id"] for event in egress)

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        reservations = await (
            await connection.execute(
                "SELECT stage FROM egress_reservation ORDER BY created_at, stage"
            )
        ).fetchall()
        attempts = await (
            await connection.execute("SELECT count(*) FROM egress_attempt")
        ).fetchone()
    assert {row[0] for row in reservations} == {"planning", "evidence"}
    assert len(reservations) == 2
    assert attempts == (2,)


async def test_mcp_lifecycle_hook_closes_without_cancelling_its_owner(
    tmp_path: Path,
) -> None:
    xml_path = rfc_factory.write(tmp_path, "hook.xml", TOOL_RFC_XML)
    verified = load_verified_rfc(xml_path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    services = McpToolServices(
        corpus=corpus,
        search_backend=_Search(corpus),
        tool_metadata=build_rfc_tool_metadata(
            corpus_manifest_id="c" * 64,
            documents=documents,
            units=corpus.units(),
            rfc_limits=RfcLimits(),
        ),
    )
    mcp_app = create_mcp_app(services)
    mcp_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app),
        base_url="http://127.0.0.1:8080",
    )
    hook = _McpHook(
        mcp_app,
        StreamableMcpClient(
            "http://127.0.0.1:8080/mcp", http_client=mcp_http
        ),
        mcp_http,
    )

    await hook.start()
    await hook.aclose()
    assert not asyncio.current_task().cancelled()
