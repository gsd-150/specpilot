"""FastMCP transport wrappers for the five read-only corpus services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ContentBlock
from pydantic import BaseModel, Field, ValidationError

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

_LOOPBACK_HOSTS = (
    "127.0.0.1",
    "localhost",
    "[::1]",
    "127.0.0.1:8080",
    "localhost:8080",
    "[::1]:8080",
)
_LOOPBACK_ORIGINS = (
    "http://127.0.0.1",
    "http://localhost",
    "http://[::1]",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "http://[::1]:8080",
)
TOOL_NAMES = frozenset(
    {"search_clauses", "get_clause", "get_toc", "expand_references", "lookup_term"}
)
_RequestModel = type[BaseModel]
_ToolWrapper = Callable[[Any], Awaitable[BaseModel]]


def _public_error(
    code: McpToolErrorCode,
    field: str,
    correction: str,
) -> McpToolError:
    return McpToolError(code, field, correction)


def _exact_transport_identities(
    allowed_hosts: Sequence[str], allowed_origins: Sequence[str]
) -> tuple[list[str], list[str]]:
    hosts = list(allowed_hosts)
    origins = list(allowed_origins)
    if not hosts or not origins or len(set(hosts)) != len(hosts):
        raise ValueError("transport identities must be non-empty and unique")
    if len(set(origins)) != len(origins):
        raise ValueError("transport identities must be non-empty and unique")
    if any("*" in host or host != host.strip() or "/" in host for host in hosts):
        raise ValueError("allowed hosts must be exact identities")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            "*" in origin
            or origin != origin.strip()
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("allowed origins must be exact HTTP origins")
    return hosts, origins


class _ValidatedTool(Tool):
    """A FastMCP Tool that validates the complete raw argument mapping."""

    request_model: _RequestModel = Field(exclude=True)

    async def run(
        self,
        arguments: dict[str, Any],
        context: Any | None = None,
        convert_result: bool = False,
    ) -> Any:
        del context
        try:
            request = self.request_model.model_validate(arguments)
        except ValidationError:
            public_error = _public_error(
                McpToolErrorCode.INVALID_ARGUMENT,
                "arguments",
                "Use the listed fields and their documented bounds.",
            )
            raise ToolError(str(public_error)) from public_error

        try:
            result = await self.fn(request)
            if convert_result:
                return self.fn_metadata.convert_result(result)
            return result
        except McpToolError as error:
            raise ToolError(str(error)) from error
        except Exception:
            public_error = _public_error(
                McpToolErrorCode.BACKEND_UNAVAILABLE,
                "tool",
                "Retry after the local tool service is available.",
            )
            raise ToolError(str(public_error)) from public_error


def _validated_tool(
    wrapper: _ToolWrapper,
    *,
    name: str,
    description: str,
    request_model: _RequestModel,
) -> Tool:
    base = Tool.from_function(
        wrapper,
        name=name,
        description=description,
        structured_output=True,
    )
    return _ValidatedTool(
        fn=base.fn,
        name=base.name,
        title=base.title,
        description=base.description,
        parameters=request_model.model_json_schema(),
        fn_metadata=base.fn_metadata,
        is_async=base.is_async,
        context_kwarg=base.context_kwarg,
        annotations=base.annotations,
        icons=base.icons,
        meta=base.meta,
        request_model=request_model,
    )


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
            else:
                public_error = _public_error(
                    McpToolErrorCode.INVALID_ARGUMENT,
                    "tool",
                    "Use a tool from the listed catalog.",
                )
            raise ToolError(str(public_error)) from None


def create_mcp_server(
    services: McpToolServices,
    *,
    allowed_hosts: Sequence[str] = _LOOPBACK_HOSTS,
    allowed_origins: Sequence[str] = _LOOPBACK_ORIGINS,
) -> FastMCP:
    """Register typed transport-only wrappers around local corpus services."""

    async def search_clauses(
        request: SearchClausesRequest,
    ) -> SearchClausesResult:
        return services.search_clauses(request)

    async def get_clause(request: GetClauseRequest) -> GetClauseResult:
        return services.get_clause(request)

    async def get_toc(request: GetTocRequest) -> GetTocResult:
        return services.get_toc(request)

    async def expand_references(
        request: ExpandReferencesRequest,
    ) -> ExpandReferencesResult:
        return services.expand_references(request)

    async def lookup_term(request: LookupTermRequest) -> LookupTermResult:
        return services.lookup_term(request)

    tools = [
        _validated_tool(
            search_clauses,
            name="search_clauses",
            description="Search bounded clause metadata in the selected local corpus.",
            request_model=SearchClausesRequest,
        ),
        _validated_tool(
            get_clause,
            name="get_clause",
            description="Read one exact clause from the selected local corpus.",
            request_model=GetClauseRequest,
        ),
        _validated_tool(
            get_toc,
            name="get_toc",
            description="Read a bounded table of contents from the selected document.",
            request_model=GetTocRequest,
        ),
        _validated_tool(
            expand_references,
            name="expand_references",
            description="Expand one bounded hop of local clause references.",
            request_model=ExpandReferencesRequest,
        ),
        _validated_tool(
            lookup_term,
            name="lookup_term",
            description=(
                "Locate definition clauses for a term in the selected document."
            ),
            request_model=LookupTermRequest,
        ),
    ]
    hosts, origins = _exact_transport_identities(allowed_hosts, allowed_origins)
    return _SanitizingFastMCP(
        "SpecPilot",
        tools=tools,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        ),
    )
