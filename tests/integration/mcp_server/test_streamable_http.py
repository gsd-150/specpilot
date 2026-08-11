from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import httpx
import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.tool_metadata import build_rfc_tool_metadata
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.mcp_server.app import create_app
from specpilot.mcp_server.client import StreamableMcpClient
from specpilot.mcp_server.services import McpToolServices, SearchBackendHit
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit
from tests.helpers import rfc_factory
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML

FIXTURE_CORPUS_ID = "a" * 64
FIXTURE_DOCUMENT_ID = "ietf-rfc-9999"


@dataclass(frozen=True)
class FixtureSearchBackend:
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
        index = Bm25Index.build(self.corpus.indexable())
        return tuple(
            SearchBackendHit(
                locator=locator_for_unit(
                    corpus_manifest_id, self.corpus.get_clause(hit.unit_id)
                ),
                score=hit.score,
            )
            for hit in index.search(query, self.corpus.unit_count())
        )


def _fixture_services(tmp_path: Path) -> McpToolServices:
    path = rfc_factory.write(tmp_path, "mcp-tools.xml", TOOL_RFC_XML)
    verified = load_verified_rfc(path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    metadata = build_rfc_tool_metadata(
        corpus_manifest_id=FIXTURE_CORPUS_ID,
        documents=documents,
        units=corpus.units(),
        rfc_limits=RfcLimits(),
    )
    return McpToolServices(
        corpus=corpus,
        search_backend=FixtureSearchBackend(corpus),
        tool_metadata=metadata,
    )


@asynccontextmanager
async def _mcp_asgi_client(
    services: McpToolServices,
) -> AsyncIterator[StreamableMcpClient]:
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as http_client,
        StreamableMcpClient(
            "http://127.0.0.1/mcp", http_client=http_client
        ) as client,
    ):
        yield client


@pytest.mark.integration
@pytest.mark.anyio
async def test_all_tools_round_trip_over_real_streamable_http_protocol(
    tmp_path: Path,
) -> None:
    services = _fixture_services(tmp_path)
    expandable = next(
        unit for unit in services.corpus.units() if unit.text.startswith("A sender")
    )

    async with _mcp_asgi_client(services) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "search_clauses",
            "get_clause",
            "get_toc",
            "expand_references",
            "lookup_term",
        }

        calls = (
            (
                "search_clauses",
                {
                    "query": "retry",
                    "corpus_manifest_id": FIXTURE_CORPUS_ID,
                    "document_ids": [FIXTURE_DOCUMENT_ID],
                    "normative_levels": [],
                    "limit": 5,
                },
            ),
            (
                "get_clause",
                {
                    "corpus_manifest_id": FIXTURE_CORPUS_ID,
                    "document_id": FIXTURE_DOCUMENT_ID,
                    "clause_id": expandable.unit_id,
                },
            ),
            (
                "get_toc",
                {
                    "corpus_manifest_id": FIXTURE_CORPUS_ID,
                    "document_id": FIXTURE_DOCUMENT_ID,
                    "limit": 2,
                },
            ),
            (
                "expand_references",
                {
                    "corpus_manifest_id": FIXTURE_CORPUS_ID,
                    "document_id": FIXTURE_DOCUMENT_ID,
                    "clause_ids": [expandable.unit_id],
                },
            ),
            (
                "lookup_term",
                {
                    "corpus_manifest_id": FIXTURE_CORPUS_ID,
                    "document_id": FIXTURE_DOCUMENT_ID,
                    "term": "retry token",
                },
            ),
        )
        results = [await client.call_tool(name, arguments) for name, arguments in calls]

    assert all(result.isError is False for result in results)
    search, clause, toc, references, term = (
        result.structuredContent for result in results
    )
    assert search is not None and 1 <= len(search["hits"]) <= 5
    assert all("text" not in hit for hit in search["hits"])
    assert clause is not None and clause["clause_id"] == expandable.unit_id
    assert clause["document_id"] == FIXTURE_DOCUMENT_ID
    assert clause["text"].startswith("A sender")
    assert toc is not None and len(toc["nodes"]) == 2
    assert references is not None and 1 <= len(references["clause_ids"]) <= 3
    assert expandable.unit_id not in references["clause_ids"]
    assert term is not None and len(term["definition_clause_ids"]) == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_validation_and_service_errors_are_stable_and_sanitized(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = _fixture_services(tmp_path)
    caplog.set_level(logging.DEBUG)

    async with _mcp_asgi_client(services) as client:
        invalid = await client.call_tool(
            "search_clauses",
            {
                "query": "secret-validation-query",
                "corpus_manifest_id": FIXTURE_CORPUS_ID,
                "document_ids": [FIXTURE_DOCUMENT_ID],
                "normative_levels": [],
                "limit": 0,
            },
        )
        closed = await client.call_tool(
            "search_clauses",
            {
                "query": "secret-service-query",
                "corpus_manifest_id": "b" * 64,
                "document_ids": [FIXTURE_DOCUMENT_ID],
                "normative_levels": [],
                "limit": 5,
            },
        )
        missing = await client.call_tool(
            "search_clauses",
            {
                "query": "secret-missing-field-query",
                "document_ids": [FIXTURE_DOCUMENT_ID],
                "normative_levels": [],
                "limit": 5,
            },
        )
        extra = await client.call_tool(
            "search_clauses",
            {
                "query": "retry",
                "corpus_manifest_id": FIXTURE_CORPUS_ID,
                "document_ids": [FIXTURE_DOCUMENT_ID],
                "normative_levels": [],
                "limit": 5,
                "unexpected_secret": "secret-extra-value",
            },
        )
        unknown = await client.call_tool(
            "unknown_secret_tool",
            {"unexpected_secret": "secret-unknown-value"},
        )

    invalid_text = invalid.content[0].text
    closed_text = closed.content[0].text
    missing_text = missing.content[0].text
    extra_text = extra.content[0].text
    unknown_text = unknown.content[0].text
    assert invalid.isError is True
    assert closed.isError is True
    assert '"code":"invalid_argument"' in invalid_text
    assert '"field":"arguments"' in invalid_text
    assert '"code":"invalid_argument"' in closed_text
    assert '"field":"corpus_manifest_id"' in closed_text
    assert "secret-validation-query" not in invalid_text
    assert "secret-service-query" not in closed_text
    assert "ValidationError" not in invalid_text
    assert missing.isError is True
    assert '"code":"invalid_argument"' in missing_text
    assert '"field":"arguments"' in missing_text
    assert "secret-missing-field-query" not in missing_text
    assert "validation error" not in missing_text.lower()
    assert extra.isError is True
    assert '"code":"invalid_argument"' in extra_text
    assert '"field":"arguments"' in extra_text
    assert "secret-extra-value" not in extra_text
    assert unknown.isError is True
    assert '"code":"invalid_argument"' in unknown_text
    assert '"field":"tool"' in unknown_text
    assert "unknown_secret_tool" not in unknown_text
    assert "secret-unknown-value" not in unknown_text
    server_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith("mcp.client.")
    )
    assert "unknown_secret_tool" not in server_logs
    assert "secret-unknown-value" not in server_logs


@pytest.mark.integration
@pytest.mark.anyio
async def test_malformed_envelope_and_unexpected_tool_errors_never_reach_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = _fixture_services(tmp_path)
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    caplog.set_level(logging.DEBUG)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as http_client,
    ):
        malformed = await http_client.post(
            "/mcp",
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": {"bad": "secret-envelope-name"},
                    "arguments": ["/private/secret-envelope-path"],
                },
            },
        )

    assert malformed.status_code == 400
    assert "secret-envelope-name" not in malformed.text
    assert "/private/secret-envelope-path" not in malformed.text
    assert "secret-envelope-name" not in caplog.text
    assert "/private/secret-envelope-path" not in caplog.text
    assert "validation error" not in caplog.text.lower()

    exploding = Mock(spec=McpToolServices)
    exploding.get_toc.side_effect = RuntimeError(
        "secret-tool-failure /private/secret-tool-path"
    )
    caplog.clear()
    async with _mcp_asgi_client(cast(McpToolServices, exploding)) as client:
        failed = await client.call_tool(
            "get_toc",
            {
                "corpus_manifest_id": FIXTURE_CORPUS_ID,
                "document_id": FIXTURE_DOCUMENT_ID,
                "limit": 2,
            },
        )

    failed_text = failed.content[0].text
    assert failed.isError is True
    assert '"code":"backend_unavailable"' in failed_text
    assert "secret-tool-failure" not in failed_text
    assert "/private/secret-tool-path" not in failed_text
    assert "secret-tool-failure" not in caplog.text
    assert "/private/secret-tool-path" not in caplog.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_streamable_client_rejects_overlapping_context_entry(
    tmp_path: Path,
) -> None:
    services = _fixture_services(tmp_path)
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as http_client,
        StreamableMcpClient(
            "http://127.0.0.1/mcp", http_client=http_client
        ) as client,
    ):
        with pytest.raises(RuntimeError, match="already active"):
            await client.__aenter__()
