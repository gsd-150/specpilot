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

from specpilot.api.app import create_app
from specpilot.api.dependencies import ApiRuntime
from specpilot.api.runtime import _assemble_runtime, load_runtime_config
from specpilot.contracts.corpus_manifest import Bm25Binding, ParseQaEvidence
from specpilot.contracts.egress import CorpusUsage
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    ProviderUse,
    RfcSourceManifestDraft,
)
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.dense_inventory import derived_corpus_sha256
from specpilot.corpus.tool_metadata import build_rfc_tool_metadata
from specpilot.deployment.ready import ReadyMarker, ReadyMarkerStore
from specpilot.egress.policy import EgressPolicy
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore
from specpilot.mcp_server.app import create_app as create_mcp_app
from specpilot.mcp_server.services import McpToolServices, SearchBackendHit
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit
from specpilot.sessions.tokens import SessionIssuer
from tests.helpers import rfc_factory
from tests.helpers.corpus_manifest_factory import corpus_draft
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML
from tests.unit.manifests.test_source_manifest import assessment

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NOW = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
DOCUMENT_ID = "ietf-rfc-9110"
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


@dataclass(slots=True)
class _McpHook:
    app: Any
    client: Any
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
        async with self.app.router.lifespan_context(self.app):
            await self.client.start()
            self._ready.set_result(None)
            try:
                await self._close
            finally:
                await self.client.aclose()


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
    dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[ApiRuntime, SessionIssuer]]:
    xml_path = rfc_factory.write(
        tmp_path, "e2e.xml", TOOL_RFC_XML.replace('number="9999"', 'number="9110"')
    )
    verified = load_verified_rfc(xml_path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    corpus_id = hashlib.sha256(
        "\n".join(unit.unit_id for unit in corpus.units()).encode()
    ).hexdigest()
    source_dir = tmp_path / "source-manifests"
    source_store = ManifestStore(source_dir)
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
            expires_at=datetime(2026, 11, 8, tzinfo=UTC),
        ),
        route_binding=route,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )
    bm25 = Bm25Index.build(corpus.indexable())
    corpus_dir = tmp_path / "corpus-manifests"
    corpus_store = CorpusManifestStore(corpus_dir)
    draft = corpus_draft(
        source_manifest_ids=(source.manifest_id,),
        bm25=Bm25Binding(
            tokenizer_version=bm25.tokenizer_version,
            k1=bm25.parameters.k1,
            b=bm25.parameters.b,
            index_fingerprint=bm25.fingerprint,
        ),
        point_count=corpus.unit_count(),
        derived_corpus_sha256=derived_corpus_sha256(corpus.units()),
        parse_qa=(
            ParseQaEvidence(
                source_manifest_id=source.manifest_id,
                evidence_sha256="1" * 64,
            ),
        ),
    )
    with corpus_store.acquire_freeze_lease(draft.collection_name) as lease:
        corpus_manifest = corpus_store.create(draft, lease=lease)
    corpus_id = corpus_manifest.manifest_id
    ready_dir = tmp_path / "ready"
    ready = ReadyMarker.create(
        source_manifest_ids=corpus_manifest.source_manifest_ids,
        corpus_manifest_id=corpus_id,
        collection_name=corpus_manifest.collection_name,
        point_count=corpus_manifest.point_count,
        inventory_root_sha256=corpus_manifest.inventory_root_sha256,
        mode="fixture",
    )
    ReadyMarkerStore(ready_dir).publish(ready)
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
    policy = EgressPolicy.load_fixture()
    await _seed_epoch(dsn, corpus_id, policy.policy_hash)
    secret = "e2e-session-secret-material-at-least-32-bytes"
    environment = {
        "SPECPILOT_API_PROFILE": "fixture",
        "SPECPILOT_API_DSN": dsn,
        "SPECPILOT_API_MCP_URL": "http://127.0.0.1:8080/mcp",
        "SPECPILOT_API_SESSION_SECRET": secret,
        "SPECPILOT_API_SESSION_AUDIENCE": "specpilot-api",
        "SPECPILOT_API_BIND_HOST": "127.0.0.1",
        "SPECPILOT_API_CONFIGURATION_HASH": "d" * 64,
        "SPECPILOT_API_PROMPT_ID": "l1-answer-v1",
        "SPECPILOT_API_PROMPT_HASH": "e" * 64,
        "SPECPILOT_API_COMPLIANCE_PROMPT_HASH": "f" * 64,
        "SPECPILOT_API_VERIFIER_PROMPT_HASH": "0" * 64,
        "SPECPILOT_MCP_CORPUS_MANIFEST_DIR": str(corpus_dir),
        "SPECPILOT_MCP_CORPUS_MANIFEST_ID": corpus_id,
        "SPECPILOT_MCP_SOURCE_MANIFEST_DIR": str(source_dir),
        "SPECPILOT_MCP_READY_DIR": str(ready_dir),
        "SPECPILOT_MCP_READY_ID": ready.ready_id,
        "SPECPILOT_MCP_MODE": "fixture",
        "SPECPILOT_MCP_SOURCES_JSON": json.dumps(
            [{"manifest_id": source.manifest_id, "xml_path": str(xml_path)}]
        ),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    runtime = _assemble_runtime(load_runtime_config(), mcp_http_client=mcp_http)
    hook = _McpHook(mcp_app, runtime.lifecycle_hooks[0])
    object.__setattr__(runtime, "lifecycle_hooks", (hook,))
    assert runtime.demo_issuer is not None
    issuer = runtime.demo_issuer
    yield runtime, issuer


async def _health(dsn: str) -> bool:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (await connection.execute("SELECT 1")).fetchone()
    return row == (1,)


async def test_l1_api_runs_real_planner_mcp_ledger_and_verifier(
    clean_ledger: str,
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fixture keeps the complete integration gate explicit about Qdrant.
    # This API path deliberately uses the deterministic local search backend;
    # the Qdrant integration cases in the same suite consume the configured URL.
    del qdrant_url
    async with _runtime(clean_ledger, tmp_path, monkeypatch) as (runtime, issuer):
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
                "scenario_id": "l1_answered",
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


async def test_registered_evidence_refusal_is_terminal_and_sanitized_over_sse(
    clean_ledger: str,
    tmp_path: Path,
    qdrant_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qdrant_url
    async with _runtime(clean_ledger, tmp_path, monkeypatch) as (runtime, issuer):
        owner = "evidence-refused-owner"
        token = issuer.issue(session_id=owner, profile="fixture", ttl_seconds=300)
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(runtime=runtime)
        private_marker = "client-must-not-select-fixture-script"
        request = {
            "question": private_marker,
            "request_id": str(uuid4()),
            "evaluation_root_id": "evidence-refused-root",
            "task_level": "L2",
            "scenario_id": "evidence_refused",
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
            accepted = await client.post("/chat", headers=headers, json=request)
            assert accepted.status_code == 202
            run_id = accepted.json()["run_id"]
            snapshot = None
            for _ in range(200):
                snapshot = await client.get(f"/runs/{run_id}", headers=headers)
                if snapshot.json()["status"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(0.01)
            stream = await client.get(f"/runs/{run_id}/events", headers=headers)

    assert snapshot is not None
    body = snapshot.json()
    assert body["task_level"] == "L1"
    assert body["status"] == "refused"
    assert body["reason"] == "evidence_insufficient"
    expected_kinds = {
        "tool_finished",
        "egress_summary",
        "verifier_summary",
        "answer_outcome",
        "terminal",
    }
    assert expected_kinds <= {
        event["kind"] for event in body["events"]
    }
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert private_marker not in snapshot.text
    assert private_marker not in stream.text
