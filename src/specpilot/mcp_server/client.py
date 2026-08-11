"""Narrow Streamable HTTP client boundary used by the Evidence Agent."""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Protocol, Self

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult


class McpEvidenceClient(Protocol):
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult: ...


class StreamableMcpClient:
    """An initialized MCP session over the SDK's Streamable HTTP transport."""

    def __init__(
        self,
        endpoint: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._http_client = http_client
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        stack = AsyncExitStack()
        try:
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    self._endpoint,
                    http_client=self._http_client,
                    terminate_on_close=False,
                )
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._exit_stack = stack
        self._session = session
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        stack = self._exit_stack
        self._exit_stack = None
        self._session = None
        if stack is not None:
            await stack.__aexit__(exc_type, exc_value, traceback)

    async def list_tools(self) -> ListToolsResult:
        return await self._active_session().list_tools()

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        return await self._active_session().call_tool(name, arguments)

    def _active_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("StreamableMcpClient must be used as an async context")
        return self._session
