"""ASGI composition for injected tests and fail-closed runtime startup."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from mcp.server.streamable_http_manager import DEFAULT_MAX_REQUEST_BODY_SIZE
from mcp.types import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    ClientNotification,
    ClientRequest,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)
from pydantic import BaseModel, ValidationError
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


def _sdk_union_methods(model: type[BaseModel]) -> frozenset[str]:
    """Derive supported method names from the installed SDK's public schema."""
    schema = model.model_json_schema()
    definitions = cast(dict[str, dict[str, Any]], schema["$defs"])
    variants = cast(list[dict[str, str]], schema["anyOf"])
    methods: set[str] = set()
    for variant in variants:
        definition_name = variant["$ref"].rsplit("/", maxsplit=1)[-1]
        method_schema = cast(
            dict[str, str], definitions[definition_name]["properties"]["method"]
        )
        methods.add(method_schema["const"])
    return frozenset(methods)


_CLIENT_REQUEST_METHODS = _sdk_union_methods(ClientRequest)


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

        # Freeze once, then relinquish each full representation before the SDK
        # receives the replayed bytes. The closure drops its reference on read.
        raw_body = bytes(received_body)
        del received_body
        if not body_complete:
            await _call_with_replayed_body(
                self._app,
                scope,
                receive,
                send,
                raw_body,
                body_complete=False,
                trailing_message=trailing_message,
            )
            return
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            await _jsonrpc_error_response(
                status_code=400,
                code=PARSE_ERROR,
                message="Parse error",
            )(scope, receive, send)
            return

        envelope, validation_response = _validate_client_message(payload)
        if validation_response is not None:
            await validation_response(scope, receive, send)
            return
        assert envelope is not None

        if (
            isinstance(payload, dict)
            and isinstance(envelope.root, JSONRPCRequest)
            and envelope.root.method == "tools/call"
        ):
            params = cast(dict[str, Any], payload["params"])
            if params["name"] not in TOOL_NAMES:
                await _unknown_tool_response(envelope.root.id)(scope, receive, send)
                return

        del payload, envelope
        await _call_with_replayed_body(
            self._app,
            scope,
            receive,
            send,
            raw_body,
            body_complete=True,
            trailing_message=trailing_message,
        )


async def _call_with_replayed_body(
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
    raw_body: bytes,
    *,
    body_complete: bool,
    trailing_message: Message | None,
) -> None:
    """Replay one frozen body and relinquish it as downstream receives it."""
    replay_body: bytes | None = raw_body
    del raw_body

    async def replay() -> Message:
        nonlocal replay_body
        if replay_body is not None:
            body = replay_body
            replay_body = None
            return {
                "type": "http.request",
                "body": body,
                "more_body": not body_complete,
            }
        if trailing_message is not None:
            return trailing_message
        return await receive()

    await app(scope, replay, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _validate_client_message(
    payload: object,
) -> tuple[JSONRPCMessage | None, Response | None]:
    """Classify with SDK types without retaining or serializing diagnostics."""
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return None, _invalid_request_response()

    if "id" not in payload:
        if "method" not in payload:
            return None, _invalid_request_response()
        try:
            envelope = JSONRPCMessage.model_validate(payload)
            if not isinstance(envelope.root, JSONRPCNotification):
                return None, _invalid_request_response()
            dumped = envelope.root.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
            ClientNotification.model_validate(dumped)
        except ValidationError:
            return None, _notification_accepted_response()
        return envelope, None

    request_id = payload["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
        return None, _invalid_request_response()

    try:
        envelope = JSONRPCMessage.model_validate(payload)
    except ValidationError:
        return None, _request_validation_error(payload, request_id)
    if isinstance(envelope.root, JSONRPCResponse | JSONRPCError):
        return None, _invalid_request_response()
    if not isinstance(envelope.root, JSONRPCRequest):
        return None, _invalid_request_response()

    dumped = envelope.root.model_dump(by_alias=True, mode="json", exclude_none=True)
    try:
        ClientRequest.model_validate(dumped)
    except ValidationError:
        return None, _request_validation_error(payload, request_id)
    return envelope, None


def _request_validation_error(
    payload: dict[str, Any], request_id: str | int
) -> JSONResponse:
    method = payload.get("method")
    if isinstance(method, str) and method in _CLIENT_REQUEST_METHODS:
        return _jsonrpc_error_response(
            status_code=200,
            code=INVALID_PARAMS,
            message="Invalid params",
            request_id=request_id,
        )
    if isinstance(method, str):
        return _jsonrpc_error_response(
            status_code=200,
            code=METHOD_NOT_FOUND,
            message="Method not found",
            request_id=request_id,
        )
    return _invalid_request_response()


def _jsonrpc_error_response(
    *,
    status_code: int,
    code: int,
    message: str,
    request_id: str | int | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
                "data": "",
            },
        },
    )


def _invalid_request_response() -> JSONResponse:
    return _jsonrpc_error_response(
        status_code=400,
        code=INVALID_REQUEST,
        message="Invalid Request",
    )


def _notification_accepted_response() -> Response:
    return Response(status_code=202, media_type="application/json")


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
