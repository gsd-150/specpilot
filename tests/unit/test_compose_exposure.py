from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
DEMO_OVERRIDE = ROOT / "compose.demo.yaml"
REAL_OVERRIDE = ROOT / "compose.real.yaml"

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
        'SPECPILOT_MCP_ALLOWED_HOSTS_JSON: \'["127.0.0.1:8080","mcp:8080"]\''
    ) in block
    assert (
        "SPECPILOT_MCP_ALLOWED_ORIGINS_JSON: "
        '\'["http://127.0.0.1:8080","http://mcp:8080"]\''
    ) in block
    assert ":*" not in block


def test_api_service_passes_only_approved_environment_names_without_defaults() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    block = text[text.index("  api:") : text.index("\n\n  fixture-init:")]
    expected = {
        "SPECPILOT_API_PROFILE",
        "SPECPILOT_API_DSN",
        "SPECPILOT_API_MCP_URL",
        "SPECPILOT_API_SESSION_SECRET",
        "SPECPILOT_API_SESSION_AUDIENCE",
        "SPECPILOT_API_BIND_HOST",
        "SPECPILOT_API_CONFIGURATION_HASH",
        "SPECPILOT_API_PROMPT_ID",
        "SPECPILOT_API_PROMPT_HASH",
        "SPECPILOT_MCP_CORPUS_MANIFEST_ID",
        "SPECPILOT_MCP_SOURCES_JSON",
        "SPECPILOT_MCP_READY_ID",
        "SPECPILOT_MAIN_API_KEY",
    }
    declarations = dict(
        re.findall(r"^\s{6}(SPECPILOT_[A-Z0-9_]+):\s+\$\{([^}]+)\}$", block, re.M)
    )

    assert set(declarations) == expected
    assert declarations == {name: name for name in expected}
    assert ":-" not in block


def test_api_and_mcp_share_three_explicit_read_only_artifact_mounts() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    mcp = text[text.index("  mcp:") : text.index("\n\n  api:")]
    api = text[text.index("  api:") : text.index("\n\n  fixture-init:")]
    expected = {
        "${SPECPILOT_MCP_CORPUS_MANIFEST_DIR_HOST}:/run/specpilot/corpus:ro",
        "${SPECPILOT_MCP_SOURCE_MANIFEST_DIR_HOST}:/run/specpilot/manifests:ro",
        "${SPECPILOT_MCP_SOURCE_DATA_DIR_HOST}:/run/specpilot/sources:ro",
        "${SPECPILOT_READY_DIR_HOST}:/run/specpilot/ready:ro",
    }
    for mount in expected:
        assert mount in mcp
        assert mount in api
    for block in (mcp, api):
        assert "SPECPILOT_MCP_CORPUS_MANIFEST_DIR: /run/specpilot/corpus" in block
        assert "SPECPILOT_MCP_SOURCE_MANIFEST_DIR: /run/specpilot/manifests" in block
        assert "SPECPILOT_MCP_CORPUS_MANIFEST_DIR_HOST:" not in block
        assert "SPECPILOT_MCP_SOURCE_MANIFEST_DIR_HOST:" not in block
        assert "SPECPILOT_MCP_SOURCE_DATA_DIR_HOST:" not in block


def test_api_and_mcp_require_the_same_read_only_ready_identity() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    mcp = text[text.index("  mcp:") : text.index("\n\n  api:")]
    api = text[text.index("  api:") : text.index("\n\n  fixture-init:")]
    mount = "${SPECPILOT_READY_DIR_HOST}:/run/specpilot/ready:ro"

    for block in (mcp, api):
        assert mount in block
        assert "SPECPILOT_MCP_READY_DIR: /run/specpilot/ready" in block
        assert "SPECPILOT_MCP_READY_ID: ${SPECPILOT_MCP_READY_ID}" in block


def test_fixture_initializer_runs_the_manifest_scoped_command() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    block = text[text.index("  fixture-init:") : text.index("\n\n  # The only")]

    assert 'profiles: ["demo"]' in block
    assert "condition: service_healthy" in block
    assert '"init-fixture"' in block
    assert '"envelope-smoke"' not in block
    assert "./fixtures/demo:/run/specpilot/fixture:ro" in block
    mcp = text[text.index("  mcp:") : text.index("\n\n  api:")]
    assert "fixture-init:" in mcp
    assert "condition: service_completed_successfully" in mcp


def test_real_override_has_no_ports_and_never_invokes_fixture_initialization() -> None:
    text = REAL_OVERRIDE.read_text(encoding="utf-8")

    assert not _PORTS_KEY.search(text)
    assert "init-real" in text
    assert "init-fixture" not in text
    assert "fixtures/demo" not in text
    assert "real-init:" in text
    assert "condition: service_completed_successfully" in text


def _compose_config(*files: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "SPECPILOT_MCP_CORPUS_MANIFEST_DIR_HOST": "/tmp/corpus",
            "SPECPILOT_MCP_SOURCE_MANIFEST_DIR_HOST": "/tmp/manifests",
            "SPECPILOT_MCP_SOURCE_DATA_DIR_HOST": "/tmp/sources",
            "SPECPILOT_READY_DIR_HOST": "/tmp/ready",
            "SPECPILOT_MCP_READY_ID": "a" * 64,
            "CORPUS_DIR": "/tmp/real-corpus",
        }
    )
    command = ["docker", "compose"]
    for file in files:
        command.extend(("-f", str(file)))
    command.extend(("--profile", "demo", "config", "--format", "json"))
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, env=environment
    )
    return json.loads(completed.stdout)


def test_base_and_real_api_have_egress_but_demo_override_removes_it() -> None:
    base = _compose_config(COMPOSE)
    demo = _compose_config(COMPOSE, DEMO_OVERRIDE)
    base_api = base["services"]["api"]  # type: ignore[index]
    demo_api = demo["services"]["api"]  # type: ignore[index]

    assert set(base_api["networks"]) == {"internal", "egress"}
    assert set(demo_api["networks"]) == {"internal", "demo"}
