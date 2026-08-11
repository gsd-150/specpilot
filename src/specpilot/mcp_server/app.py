"""ASGI composition for injected tests and fail-closed runtime startup."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from mcp.server.streamable_http_manager import DEFAULT_MAX_REQUEST_BODY_SIZE
from mcp.types import (
    ClientNotification,
    ClientRequest,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
)
from pydantic import ValidationError
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from specpilot.mcp_server.contracts import McpToolError, McpToolErrorCode
from specpilot.mcp_server.runtime import (
    LOOPBACK_HOSTS,
    LOOPBACK_ORIGINS,
    load_runtime_config,
    load_runtime_services,
)
from specpilot.mcp_server.server import TOOL_NAMES, create_mcp_server
from specpilot.mcp_server.services import McpToolServices


class _SanitizedJsonRpcBoundary:
    """Bound and validate client JSON-RPC before MCP can log caller values."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_size: int = DEFAULT_MAX_REQUEST_BODY_SIZE,
        allowed_hosts: Sequence[str] = LOOPBACK_HOSTS,
        allowed_origins: Sequence[str] = LOOPBACK_ORIGINS,
    ) -> None:
        self._app = app
        self._max_body_size = max_body_size
        self._allowed_hosts = frozenset(allowed_hosts)
        self._allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self._app(scope, receive, send)
            return

        headers = {name.lower(): value for name, value in scope["headers"]}
        host = headers.get(b"host", b"").decode("latin-1")
        if host not in self._allowed_hosts:
            await Response("Invalid Host header", status_code=421)(
                scope, receive, send
            )
            return
        origin = headers.get(b"origin")
        if (
            origin is not None
            and origin.decode("latin-1") not in self._allowed_origins
        ):
            await Response("Invalid Origin header", status_code=403)(
                scope, receive, send
            )
            return

        declared_size = _content_length(scope)
        if declared_size is not None and declared_size > self._max_body_size:
            await _body_too_large_response()(scope, receive, send)
            return

        received_body = bytearray()
        body_complete = False
        trailing_message: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing_message = message
                break
            body = message.get("body", b"")
            if len(received_body) + len(body) > self._max_body_size:
                await _body_too_large_response()(scope, receive, send)
                return
            received_body.extend(body)
            if not message.get("more_body", False):
                body_complete = True
                break

        try:
            payload = json.loads(received_body)
            envelope = JSONRPCMessage.model_validate(payload)
            _validate_client_message(payload, envelope)
        except (UnicodeDecodeError, ValueError, ValidationError):
            await _invalid_envelope_response()(scope, receive, send)
            return

        if (
            isinstance(payload, dict)
            and isinstance(envelope.root, JSONRPCRequest)
            and envelope.root.method == "tools/call"
        ):
            params = cast(dict[str, Any], payload["params"])
            if params["name"] not in TOOL_NAMES:
                await _unknown_tool_response(envelope.root.id)(scope, receive, send)
                return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": bytes(received_body),
                    "more_body": not body_complete,
                }
            if trailing_message is not None:
                return trailing_message
            return await receive()

        await self._app(scope, replay, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _validate_client_message(payload: object, message: JSONRPCMessage) -> None:
    if isinstance(payload, dict) and "id" in payload:
        request_id = payload["id"]
        if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
            raise ValueError("invalid JSON-RPC id")
    dumped = message.root.model_dump(by_alias=True, mode="json", exclude_none=True)
    if isinstance(message.root, JSONRPCRequest):
        ClientRequest.model_validate(dumped)
    elif isinstance(message.root, JSONRPCNotification):
        ClientNotification.model_validate(dumped)


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


def _body_too_large_response() -> Response:
    return Response("Request body too large", status_code=413)


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
            "id": request_id,
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
    mcp_app.add_middleware(
        _SanitizedJsonRpcBoundary,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )

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
