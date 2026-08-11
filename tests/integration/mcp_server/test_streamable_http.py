from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

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
    assert all(result.structuredContent is not None for result in results)


@pytest.mark.integration
@pytest.mark.anyio
async def test_validation_and_service_errors_are_stable_and_sanitized(
    tmp_path: Path,
) -> None:
    services = _fixture_services(tmp_path)

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

    invalid_text = invalid.content[0].text
    closed_text = closed.content[0].text
    missing_text = missing.content[0].text
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
