"""FastMCP transport wrappers for the five read-only corpus services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ContentBlock
from pydantic import BaseModel, ValidationError

from specpilot.mcp_server.contracts import (
    ExpandReferencesRequest,
    ExpandReferencesResult,
    GetClauseRequest,
    GetClauseResult,
    GetTocRequest,
    GetTocResult,
    LookupTermRequest,
    LookupTermResult,
    McpToolError,
    McpToolErrorCode,
    SearchClausesRequest,
    SearchClausesResult,
)
from specpilot.mcp_server.services import McpToolServices


class _SanitizingFastMCP(FastMCP):
    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as error:
            cause = error.__cause__
            if isinstance(cause, McpToolError):
                public_error = cause
            elif isinstance(cause, ValidationError):
                public_error = McpToolError(
                    McpToolErrorCode.INVALID_ARGUMENT,
                    "arguments",
                    "Use the listed fields and their documented bounds.",
                )
            else:
                public_error = McpToolError(
                    McpToolErrorCode.BACKEND_UNAVAILABLE,
                    "tool",
                    "Retry after the local tool service is available.",
                )
            raise ToolError(str(public_error)) from None


def _validated_request[RequestT: BaseModel](
    model: type[RequestT], values: dict[str, Any]
) -> RequestT:
    try:
        return model.model_validate(values)
    except ValidationError:
        raise McpToolError(
            McpToolErrorCode.INVALID_ARGUMENT,
            "arguments",
            "Use the listed fields and their documented bounds.",
        ) from None


def create_mcp_server(services: McpToolServices) -> FastMCP:
    """Register transport-only wrappers around the local corpus services."""
    mcp = _SanitizingFastMCP(
        "SpecPilot",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1",
                "127.0.0.1:*",
                "localhost",
                "localhost:*",
                "[::1]",
                "[::1]:*",
            ],
            allowed_origins=[
                "http://127.0.0.1",
                "http://127.0.0.1:*",
                "http://localhost",
                "http://localhost:*",
                "http://[::1]",
                "http://[::1]:*",
            ],
        ),
    )

    @mcp.tool()
    async def search_clauses(
        query: str,
        corpus_manifest_id: str,
        document_ids: list[str],
        normative_levels: list[str],
        limit: int,
    ) -> SearchClausesResult:
        """Search bounded clause metadata in the selected local corpus."""
        request = _validated_request(
            SearchClausesRequest,
            {
                "query": query,
                "corpus_manifest_id": corpus_manifest_id,
                "document_ids": document_ids,
                "normative_levels": normative_levels,
                "limit": limit,
            },
        )
        return services.search_clauses(request)

    @mcp.tool()
    async def get_clause(
        corpus_manifest_id: str,
        document_id: str,
        clause_id: str,
    ) -> GetClauseResult:
        """Read one exact clause from the selected local corpus."""
        request = _validated_request(
            GetClauseRequest,
            {
                "corpus_manifest_id": corpus_manifest_id,
                "document_id": document_id,
                "clause_id": clause_id,
            },
        )
        return services.get_clause(request)

    @mcp.tool()
    async def get_toc(
        corpus_manifest_id: str,
        document_id: str,
        limit: int,
    ) -> GetTocResult:
        """Read a bounded table of contents from the selected document."""
        request = _validated_request(
            GetTocRequest,
            {
                "corpus_manifest_id": corpus_manifest_id,
                "document_id": document_id,
                "limit": limit,
            },
        )
        return services.get_toc(request)

    @mcp.tool()
    async def expand_references(
        corpus_manifest_id: str,
        document_id: str,
        clause_ids: list[str],
    ) -> ExpandReferencesResult:
        """Expand one bounded hop of local clause references."""
        request = _validated_request(
            ExpandReferencesRequest,
            {
                "corpus_manifest_id": corpus_manifest_id,
                "document_id": document_id,
                "clause_ids": clause_ids,
            },
        )
        return services.expand_references(request)

    @mcp.tool()
    async def lookup_term(
        corpus_manifest_id: str,
        document_id: str,
        term: str,
    ) -> LookupTermResult:
        """Locate definition clauses for a term in the selected document."""
        request = _validated_request(
            LookupTermRequest,
            {
                "corpus_manifest_id": corpus_manifest_id,
                "document_id": document_id,
                "term": term,
            },
        )
        return services.lookup_term(request)

    return mcp
