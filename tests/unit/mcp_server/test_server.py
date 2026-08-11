from __future__ import annotations

from typing import cast
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from specpilot.mcp_server.app import create_app
from specpilot.mcp_server.server import create_mcp_server
from specpilot.mcp_server.services import McpToolServices


@pytest.mark.anyio
async def test_server_registers_the_five_typed_read_only_tools() -> None:
    services = cast(McpToolServices, Mock(spec=McpToolServices))

    server = create_mcp_server(services)
    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {
        "search_clauses",
        "get_clause",
        "get_toc",
        "expand_references",
        "lookup_term",
    }
    schemas = {tool.name: tool.inputSchema for tool in tools}
    assert schemas["search_clauses"]["required"] == [
        "query",
        "corpus_manifest_id",
        "document_ids",
        "normative_levels",
        "limit",
    ]
    assert schemas["search_clauses"]["properties"]["limit"]["type"] == "integer"
    assert schemas["expand_references"]["properties"]["clause_ids"]["type"] == (
        "array"
    )
    assert server.settings.stateless_http is True
    assert server.settings.json_response is True
    assert server.settings.transport_security is not None
    assert server.settings.transport_security.enable_dns_rebinding_protection is True
    assert server.settings.transport_security.allowed_hosts == [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    ]
    assert server.settings.transport_security.allowed_origins == [
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://localhost",
        "http://localhost:*",
        "http://[::1]",
        "http://[::1]:*",
    ]


def test_host_app_serves_health_and_rejects_non_loopback_mcp_hosts() -> None:
    services = cast(McpToolServices, Mock(spec=McpToolServices))

    with TestClient(create_app(services), base_url="http://127.0.0.1") as client:
        health = client.get("/health")
        rejected = client.post(
            "/mcp",
            headers={"host": "attacker.example", "content-type": "application/json"},
            json={},
        )
        rejected_origin = client.post(
            "/mcp",
            headers={
                "host": "127.0.0.1",
                "origin": "https://attacker.example",
                "content-type": "application/json",
            },
            json={},
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert rejected.status_code == 421
    assert rejected_origin.status_code == 403
