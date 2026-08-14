"""Loopback-only browser fixture with the real API/MCP/ledger worker path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg

from specpilot.api.app import create_app
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
from tests.helpers import rfc_factory
from tests.helpers.corpus_manifest_factory import corpus_draft
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML
from tests.unit.manifests.test_source_manifest import assessment

_DATABASE = "specpilot_w3_browser_test"
_DOCUMENT_ID = "ietf-rfc-9110"


@dataclass(slots=True)
class _FixtureCleanup:
    temporary: tempfile.TemporaryDirectory[str]
    dsn: str

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        self.temporary.cleanup()
        await asyncio.to_thread(_clear_database, self.dsn)


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
        try:
            await asyncio.shield(self._ready)
        except BaseException:
            self._owner.cancel()
            with suppress(BaseException):
                await self._owner
            raise

    async def aclose(self) -> None:
        if self._owner is None or self._close is None:
            return
        if not self._close.done():
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


def _require_browser_dsn() -> str:
    dsn = os.environ.get("SPECPILOT_BROWSER_DSN", "")
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError("browser fixture DSN is required")
    if (
        parsed.path != f"/{_DATABASE}"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("browser fixture DSN must name the dedicated local database")
    return dsn


def _migrate_fresh(dsn: str) -> None:
    migrations = sorted(Path("migrations").glob("[0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise RuntimeError("browser fixture migrations are unavailable")
    with psycopg.connect(dsn) as connection:
        existing = connection.execute(
            "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
        ).fetchone()
        if existing != (0,):
            raise RuntimeError("browser fixture database is not fresh")
        for migration in migrations:
            connection.execute(migration.read_text(encoding="utf-8"))


def _clear_database(dsn: str) -> None:
    """Clear only the allowlisted browser database after the server closes."""

    _require_browser_dsn()
    with psycopg.connect(dsn) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")


def _seed_epoch(dsn: str, corpus_id: str, policy_hash: str) -> None:
    usage = CorpusUsage(corpus_manifest_id=corpus_id, policy_hash=policy_hash)
    with psycopg.connect(dsn) as connection:
        ledger_id = uuid4()
        connection.execute(
            "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
            "VALUES (%s, 'egress-policy/v1')",
            (policy_hash,),
        )
        connection.execute(
            "INSERT INTO egress_corpus_ledger "
            "(corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
            "unique_excerpts, unique_tokens, unique_bytes) "
            "VALUES (%s, %s, %s, %s, 0, 0, 0)",
            (ledger_id, corpus_id, policy_hash, usage.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO egress_corpus_ledger_head "
            "(corpus_manifest_id, corpus_ledger_id) VALUES (%s, %s)",
            (corpus_id, ledger_id),
        )


def create_fixture_app() -> Any:
    dsn = _require_browser_dsn()
    _migrate_fresh(dsn)
    scratch_root = Path("tmp/browser").resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(
        prefix="specpilot-browser-", dir=scratch_root
    )
    fixture_root = Path(temporary.name)
    xml_path = rfc_factory.write(
        fixture_root,
        "browser.xml",
        TOOL_RFC_XML.replace('number="9999"', 'number="9110"'),
    )
    verified = load_verified_rfc(xml_path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    source_dir = fixture_root / "source-manifests"
    source_store = ManifestStore(source_dir)
    initial = source_store.create_source_v2(
        RfcSourceManifestDraft(
            document_id=_DOCUMENT_ID,
            document_version="2026-08",
            text_url="https://example.test/rfc9110.txt",
            xml_url="https://example.test/rfc9110.xml",
            text_sha256="f" * 64,
            xml_sha256=hashlib.sha256(xml_path.read_bytes()).hexdigest(),
            downloaded_at=datetime(2026, 8, 6, tzinfo=UTC),
            created_at=datetime(2026, 8, 6, 1, tzinfo=UTC),
        )
    )
    route = ProviderRouteBinding(
        provider_id="fixture-provider",
        endpoint_purpose="fixture-browser",
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
    corpus_dir = fixture_root / "corpus-manifests"
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
        manifest = corpus_store.create(draft, lease=lease)
    ready_dir = fixture_root / "ready"
    ready = ReadyMarker.create(
        source_manifest_ids=manifest.source_manifest_ids,
        corpus_manifest_id=manifest.manifest_id,
        collection_name=manifest.collection_name,
        point_count=manifest.point_count,
        inventory_root_sha256=manifest.inventory_root_sha256,
        mode="fixture",
    )
    ReadyMarkerStore(ready_dir).publish(ready)
    services = McpToolServices(
        corpus=corpus,
        search_backend=_Search(corpus),
        tool_metadata=build_rfc_tool_metadata(
            corpus_manifest_id=manifest.manifest_id,
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
    policy = EgressPolicy.load()
    _seed_epoch(dsn, manifest.manifest_id, policy.policy_hash)
    environment = {
        "SPECPILOT_API_PROFILE": "fixture",
        "SPECPILOT_API_DSN": dsn,
        "SPECPILOT_API_MCP_URL": "http://127.0.0.1:8080/mcp",
        "SPECPILOT_API_SESSION_SECRET": (
            "browser-session-secret-material-at-least-32-bytes"
        ),
        "SPECPILOT_API_SESSION_AUDIENCE": "specpilot-api",
        "SPECPILOT_API_BIND_HOST": "127.0.0.1",
        "SPECPILOT_API_CONFIGURATION_HASH": "d" * 64,
        "SPECPILOT_API_PROMPT_ID": "l1-answer-v1",
        "SPECPILOT_API_PROMPT_HASH": "e" * 64,
        "SPECPILOT_MCP_CORPUS_MANIFEST_DIR": str(corpus_dir),
        "SPECPILOT_MCP_CORPUS_MANIFEST_ID": manifest.manifest_id,
        "SPECPILOT_MCP_SOURCE_MANIFEST_DIR": str(source_dir),
        "SPECPILOT_MCP_READY_DIR": str(ready_dir),
        "SPECPILOT_MCP_READY_ID": ready.ready_id,
        "SPECPILOT_MCP_MODE": "fixture",
        "SPECPILOT_MCP_SOURCES_JSON": json.dumps(
            [{"manifest_id": source.manifest_id, "xml_path": str(xml_path)}]
        ),
    }
    os.environ.update(environment)
    runtime = _assemble_runtime(load_runtime_config(), mcp_http_client=mcp_http)
    hook = _McpHook(mcp_app, runtime.lifecycle_hooks[0])
    object.__setattr__(
        runtime, "lifecycle_hooks", (_FixtureCleanup(temporary, dsn), hook)
    )
    return create_app(runtime=runtime)


__all__ = ["create_fixture_app"]
