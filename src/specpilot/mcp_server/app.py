"""ASGI composition for injected tests and fail-closed runtime startup."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from specpilot.mcp_server.contracts import McpToolError, McpToolErrorCode
from specpilot.mcp_server.runtime import (
    LOOPBACK_HOSTS,
    LOOPBACK_ORIGINS,
    load_runtime_config,
    load_runtime_services,
)
from specpilot.mcp_server.server import TOOL_NAMES, create_mcp_server
from specpilot.mcp_server.services import McpToolServices


class _McpEnvelopeGuard(BaseHTTPMiddleware):
    """Reject malformed tool envelopes before MCP 1.29 logs their values."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "POST":
            try:
                payload = json.loads(await request.body())
            except (UnicodeDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                params = payload.get("params")
                if not isinstance(params, dict) or not isinstance(
                    params.get("name"), str
                ):
                    return _invalid_envelope_response()
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    return _invalid_envelope_response()
                if params["name"] not in TOOL_NAMES:
                    return _unknown_tool_response(payload.get("id"))
        return await call_next(request)


def _invalid_envelope_response() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32602,
                "message": "Invalid request parameters",
                "data": "",
            },
        },
    )


def _unknown_tool_response(request_id: object) -> JSONResponse:
    public_error = McpToolError(
        McpToolErrorCode.INVALID_ARGUMENT,
        "tool",
        "Use a tool from the listed catalog.",
    )
    return JSONResponse(
        status_code=200,
        content={
            "jsonrpc": "2.0",
            "id": request_id if isinstance(request_id, int) else None,
            "result": {
                "content": [{"type": "text", "text": str(public_error)}],
                "isError": True,
            },
        },
    )


def create_app(
    services: McpToolServices,
    *,
    allowed_hosts: Sequence[str] = LOOPBACK_HOSTS,
    allowed_origins: Sequence[str] = LOOPBACK_ORIGINS,
) -> FastAPI:
    """Create the health host and mount the Streamable HTTP MCP application."""
    mcp = create_mcp_server(
        services,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    mcp_app = mcp.streamable_http_app()
    mcp_app.add_middleware(_McpEnvelopeGuard)

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


def _unavailable_app() -> FastAPI:
    app = FastAPI(title="SpecPilot MCP", version="0.1.0")

    @app.get("/health", status_code=503)
    async def health() -> dict[str, str]:
        return {
            "status": "unavailable",
            "code": "mcp_runtime_config_invalid",
        }

    return app


def create_runtime_app() -> FastAPI:
    """Build the zero-argument deployment app from explicit frozen artifacts."""
    try:
        config = load_runtime_config()
        services = load_runtime_services(config)
    except Exception:
        return _unavailable_app()
    return create_app(
        services,
        allowed_hosts=config.allowed_hosts,
        allowed_origins=config.allowed_origins,
    )
