from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from specpilot.mcp_server.server import create_mcp_server
from specpilot.mcp_server.services import McpToolServices


def create_app(services: McpToolServices) -> FastAPI:
    """Create the health host and mount the Streamable HTTP MCP application."""
    mcp = create_mcp_server(services)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="SpecPilot MCP", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/", mcp_app)
    return app
