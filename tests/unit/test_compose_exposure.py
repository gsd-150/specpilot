from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
DEMO_OVERRIDE = ROOT / "compose.demo.yaml"

_PORTS_KEY = re.compile(r"^\s*ports:\s*$", re.M)


def test_the_base_compose_file_publishes_nothing() -> None:
    """No host exposure may live in the file both profiles load.

    An earlier version put the demo port on a second service using `extends`.
    That service inherited the base service's `profiles` list, so the real
    profile published port 8000 as well. The fix was to move publishing into an
    explicitly-passed override; this test is what keeps it there.
    """
    assert not _PORTS_KEY.search(COMPOSE.read_text(encoding="utf-8")), (
        "compose.yaml declares a ports: block; host publishing belongs only in "
        "compose.demo.yaml, which has to be passed with an explicit -f"
    )


def test_the_demo_override_publishes_only_to_loopback() -> None:
    text = DEMO_OVERRIDE.read_text(encoding="utf-8")

    published = re.findall(r'^\s*-\s*"([^"]+)"\s*$', text, re.M)

    assert published, "the demo override exists to publish something"
    for mapping in published:
        assert mapping.startswith("127.0.0.1:"), (
            f"{mapping!r} is published beyond loopback"
        )


def test_the_untrusted_ingestion_service_keeps_every_restriction() -> None:
    """The one container that handles untrusted documents stays locked down."""
    text = COMPOSE.read_text(encoding="utf-8")
    ingestion = text[text.index("  ingestion:") :]

    for restriction in (
        "network_mode: none",
        "read_only: true",
        'cap_drop: ["ALL"]',
        "no-new-privileges:true",
        "mem_limit:",
        "pids_limit:",
        ":/input:ro",
    ):
        assert restriction in ingestion, f"ingestion lost {restriction!r}"


def test_internal_services_are_on_an_internal_network() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "internal: true" in text, "the internal network must be marked internal"
    for service in ("postgres:", "qdrant:", "mcp:"):
        block = text[text.index(f"  {service}") :]
        block = block[: block.index("\n\n")]
        assert "networks: [internal]" in block, f"{service} left the internal network"


def test_mcp_service_declares_only_exact_internal_transport_identities() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    block = text[text.index("  mcp:") : text.index("\n\n  api:")]

    assert (
        "SPECPILOT_MCP_ALLOWED_HOSTS_JSON: "
        "'[\"127.0.0.1:8080\",\"mcp:8080\"]'"
    ) in block
    assert (
        "SPECPILOT_MCP_ALLOWED_ORIGINS_JSON: "
        "'[\"http://127.0.0.1:8080\",\"http://mcp:8080\"]'"
    ) in block
    assert ":*" not in block
