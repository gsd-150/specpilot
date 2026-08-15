from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("SPECPILOT_W5_PROJECT", "malicious-shared-project")
    monkeypatch.setenv("SPECPILOT_W5_API_PORT", "1")
    path = Path("scripts/w5_packaged_gate.py")
    if not path.is_file():
        pytest.fail("packaged Compose gate script is missing")
    spec = importlib.util.spec_from_file_location("w5_packaged_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_compose_commands_are_scoped_to_one_exact_project(gate: ModuleType) -> None:
    assert re.fullmatch(
        r"specpilot-w5-task9-packaged-[0-9]+-[0-9a-f]{8}", gate.PROJECT_NAME
    )
    assert gate.PROJECT_NAME != "malicious-shared-project"
    assert gate.API_PORT != 1
    assert 49152 <= gate.API_PORT <= 65535
    assert f"http://127.0.0.1:{gate.API_PORT}" == gate.API_BASE_URL
    assert gate.COMPOSE_FILES == (
        "compose.yaml",
        "compose.demo.yaml",
        "compose.w5-gate.yaml",
    )
    assert gate.compose_command("ps", "--all") == [
        "docker",
        "compose",
        "-p",
        gate.PROJECT_NAME,
        "--env-file",
        str(gate.ENV_FILE),
        "-f",
        "compose.yaml",
        "-f",
        "compose.demo.yaml",
        "-f",
        "compose.w5-gate.yaml",
        "--profile",
        "demo",
        "ps",
        "--all",
    ]


def test_packaged_commands_use_a_closed_environment_without_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
    gate: ModuleType,
) -> None:
    monkeypatch.setenv("SPECPILOT_MAIN_API_KEY", "private-main-provider-key")
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-anthropic-key")
    monkeypatch.setenv("UNRELATED_HOST_VALUE", "must-not-cross-the-boundary")

    probe = gate.run_command(
        [
            sys.executable,
            "-c",
            "import json,os; print(json.dumps(dict(os.environ),sort_keys=True))",
        ]
    )
    child = json.loads(probe.stdout)

    assert child["SPECPILOT_W5_API_PORT"] == str(gate.API_PORT)
    assert child["NO_COLOR"] == "1"
    assert "SPECPILOT_MAIN_API_KEY" not in child
    assert "OPENAI_API_KEY" not in child
    assert "ANTHROPIC_API_KEY" not in child
    assert "UNRELATED_HOST_VALUE" not in child
    assert set(child) <= gate.PERMITTED_SUBPROCESS_ENVIRONMENT


def test_failure_path_always_requests_exact_project_volume_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    gate: ModuleType,
) -> None:
    calls: list[str] = []

    def fail() -> None:
        raise RuntimeError("synthetic packaged failure")

    monkeypatch.setattr(gate, "exercise_packaged_demo", fail)
    monkeypatch.setattr(gate, "cleanup_resources", lambda: calls.append("cleanup"))

    with pytest.raises(RuntimeError, match="synthetic packaged failure"):
        gate.run_gate()

    assert calls == ["cleanup"]


def test_cleanup_removes_only_discovered_exact_resource_ids(
    monkeypatch: pytest.MonkeyPatch,
    gate: ModuleType,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        gate,
        "run_command",
        lambda command, **_: (
            calls.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    gate.cleanup_resource_ids(
        gate.ResourceIds(
            containers=("container-id",),
            volumes=("volume-name",),
            networks=("network-id",),
            images=("unique-image:latest",),
        )
    )

    assert calls == [
        ["docker", "rm", "--force", "container-id"],
        ["docker", "volume", "rm", "volume-name"],
        ["docker", "network", "rm", "network-id"],
        [
            "docker",
            "image",
            "rm",
            "--force",
            "--no-prune",
            "unique-image:latest",
        ],
    ]


def test_volume_initializer_overrides_fixture_cli_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    gate: ModuleType,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        gate,
        "run_command",
        lambda command, **_: (
            calls.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    gate._initialize_artifact_volumes()

    command = calls[0]
    entrypoint = command.index("--entrypoint")
    assert command[entrypoint : entrypoint + 2] == [
        "--entrypoint",
        "sh",
    ]
    assert command[-2] == "-ec"


def test_fixture_repeat_requires_identical_complete_ready_identity(
    gate: ModuleType,
) -> None:
    payload = {
        "status": "ready",
        "ready_id": "a" * 64,
        "mode": "fixture",
        "source_manifest_ids": ["b" * 64],
        "corpus_manifest_id": "c" * 64,
        "collection": "specpilot_fixture",
        "point_count": 6,
        "inventory_root_sha256": "d" * 64,
    }
    encoded = "fixture-init-1 | " + json.dumps(payload, sort_keys=True)

    first = gate.parse_ready_payload(encoded)
    second = gate.parse_ready_payload(json.dumps(payload))
    gate.assert_identical_replay(first, second)

    changed = dict(second)
    changed["ready_id"] = "e" * 64
    with pytest.raises(AssertionError, match="fixture repeat changed ready identity"):
        gate.assert_identical_replay(first, changed)


def test_packaged_gate_bootstraps_exact_corpus_through_public_ledger_api(
    gate: ModuleType,
) -> None:
    command = gate._ledger_bootstrap_command("c" * 64)

    assert gate.FIXTURE_INIT_IMAGE in command
    assert gate.INTERNAL_NETWORK in command
    assert "PostgresEgressLedger" in command[-2]
    assert "initialize_corpus" in command[-2]
    assert "EgressPolicy.load_fixture" in command[-2]
    assert command[-1] == "c" * 64
    assert not any(":/run/specpilot/fixture" in item for item in command)


def test_gate_compose_override_uses_named_artifact_volumes_and_migrations(
    gate: ModuleType,
) -> None:
    override = Path("compose.w5-gate.yaml").read_text(encoding="utf-8")

    for volume in (
        "w5-corpus:",
        "w5-manifests:",
        "w5-ready:",
        "w5-sources:",
    ):
        assert volume in override
    assert "127.0.0.1:${SPECPILOT_W5_API_PORT}:8000" in override
    assert "depends_on: !override" in override
    assert "SPECPILOT_MCP_SOURCE_DATA_DIR_HOST" not in override

    script = Path("scripts/w5_packaged_gate.py").read_text(encoding="utf-8")
    assert '"docker", "run"' in script
    assert "applied=16" in script
    audited_repeat = gate._fixture_init_command(
        network=gate.INTERNAL_NETWORK,
        qdrant_url=f"http://{gate.AUDIT_PROXY_NAME}:6334",
    )
    assert audited_repeat[
        audited_repeat.index("--network") : audited_repeat.index("--network") + 2
    ] == ["--network", gate.INTERNAL_NETWORK]
    assert f"http://{gate.AUDIT_PROXY_NAME}:6334" in audited_repeat


def test_gate_runtime_has_no_host_bind_and_real_image_excludes_fixture() -> None:
    script = Path("scripts/w5_packaged_gate.py").read_text(encoding="utf-8")
    assert ":/run/specpilot/fixture:ro" not in script
    assert ":/migrations:ro" not in script

    api_dockerfile = Path("docker/api.Dockerfile").read_text(encoding="utf-8")
    migration_dockerfile = Path("docker/migrations.Dockerfile").read_text(
        encoding="utf-8"
    )
    base = Path("compose.yaml").read_text(encoding="utf-8")
    real = Path("compose.real.yaml").read_text(encoding="utf-8")

    assert "FROM python-runtime AS initializer-runtime" in api_dockerfile
    assert "FROM initializer-runtime AS fixture" in api_dockerfile
    assert api_dockerfile.index("FROM initializer-runtime AS fixture") < (
        api_dockerfile.index("FROM node:22.12-bookworm-slim AS frontend")
    )
    assert "rm -rf src/specpilot/api/static/trace" in api_dockerfile
    assert "COPY --from=frontend /build/src/specpilot/api/static/trace" in (
        api_dockerfile
    )
    assert "COPY --chown=10001:10001 fixtures/demo /run/specpilot/fixture" in (
        api_dockerfile
    )
    assert "COPY migrations /opt/specpilot/migrations" in migration_dockerfile
    assert "target: fixture" in base[base.index("  fixture-init:") :]
    api_start = base.index("  api:")
    api_block = base[api_start : base.index("\n  fixture-init:", api_start)]
    assert "target: runtime" in api_block
    assert "target: initializer-runtime" in real[real.index("  real-init:") :]


def test_packaged_sse_validator_rejects_noncontiguous_or_private_output(
    gate: ModuleType,
) -> None:
    events = [
        {"sequence": 1, "kind": "tool_finished", "status": None},
        {"sequence": 2, "kind": "egress_summary", "status": None},
        {"sequence": 3, "kind": "verifier_summary", "status": None},
        {"sequence": 4, "kind": "answer_outcome", "status": None},
        {"sequence": 5, "kind": "terminal", "status": "answered"},
    ]
    raw = ": keep-alive\n\n" + "".join(
        (
            f"id: {event['sequence']}\n"
            f"event: {event['kind']}\n"
            f"data: {json.dumps(event)}\n\n"
        )
        for event in events
    )
    gate.assert_scenario_events(
        scenario_id="l1_answered",
        expected_terminal="answered",
        required_kinds={
            "tool_finished",
            "egress_summary",
            "verifier_summary",
            "answer_outcome",
            "terminal",
        },
        private_marker="private-marker",
        raw_sse=raw,
    )

    broken = [dict(event) for event in events]
    broken[-1]["sequence"] = 7
    with pytest.raises(AssertionError, match="contiguous"):
        gate.assert_scenario_events(
            scenario_id="l1_answered",
            expected_terminal="answered",
            required_kinds={"terminal"},
            private_marker="private-marker",
            raw_sse="".join(
                f"id: {event['sequence']}\nevent: {event['kind']}\n"
                f"data: {json.dumps(event)}\n\n"
                for event in broken
            ),
        )

    leaked = "private-marker\n" + "\n".join(
        f"data: {json.dumps(event)}" for event in events
    )
    with pytest.raises(AssertionError, match="private marker"):
        gate.assert_scenario_events(
            scenario_id="l1_answered",
            expected_terminal="answered",
            required_kinds={"terminal"},
            private_marker="private-marker",
            raw_sse=leaked,
        )


def test_packaged_sse_validator_binds_frame_fields_and_terminal_cardinality(
    gate: ModuleType,
) -> None:
    events = [
        {"sequence": 1, "kind": "recovery_summary", "status": None},
        {"sequence": 2, "kind": "terminal", "status": "answered"},
    ]

    def framed(
        *,
        id_override: str | None = None,
        event_override: str | None = None,
    ) -> str:
        return "".join(
            f"id: {id_override if index == 0 and id_override else event['sequence']}\n"
            "event: "
            f"{event_override if index == 0 and event_override else event['kind']}\n"
            f"data: {json.dumps(event)}\n\n"
            for index, event in enumerate(events)
        )

    gate.assert_scenario_events(
        scenario_id="verifier_recovered",
        expected_terminal="answered",
        required_kinds={"recovery_summary", "terminal"},
        private_marker="private-marker",
        raw_sse=framed(),
    )
    with pytest.raises(AssertionError, match="id.*sequence"):
        gate.assert_scenario_events(
            scenario_id="verifier_recovered",
            expected_terminal="answered",
            required_kinds={"terminal"},
            private_marker="private-marker",
            raw_sse=framed(id_override="9"),
        )
    with pytest.raises(AssertionError, match="event.*kind"):
        gate.assert_scenario_events(
            scenario_id="verifier_recovered",
            expected_terminal="answered",
            required_kinds={"terminal"},
            private_marker="private-marker",
            raw_sse=framed(event_override="tool_finished"),
        )

    duplicate_terminal = framed() + (
        'id: 3\nevent: terminal\ndata: '
        '{"sequence":3,"kind":"terminal","status":"answered"}\n\n'
    )
    with pytest.raises(AssertionError, match="exactly one terminal"):
        gate.assert_scenario_events(
            scenario_id="verifier_recovered",
            expected_terminal="answered",
            required_kinds={"terminal"},
            private_marker="private-marker",
            raw_sse=duplicate_terminal,
        )

    without_recovery = (
        'id: 1\nevent: terminal\ndata: '
        '{"sequence":1,"kind":"terminal","status":"answered"}\n\n'
    )
    with pytest.raises(AssertionError, match="exactly one recovery"):
        gate.assert_scenario_events(
            scenario_id="verifier_recovered",
            expected_terminal="answered",
            required_kinds={"terminal"},
            private_marker="private-marker",
            raw_sse=without_recovery,
        )


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/", True),
        ("GET", "/collections/specpilot_fixture", True),
        ("GET", "/collections/specpilot_fixture/snapshots", True),
        ("POST", "/collections/specpilot_fixture/points/scroll", True),
        ("POST", "/collections/specpilot_fixture/points/scroll?wait=true", True),
        ("POST", "/collections/specpilot_fixture/points/count", True),
        ("PUT", "/collections/specpilot_fixture/points", False),
        ("POST", "/collections/specpilot_fixture/points", False),
        ("PUT", "/collections/specpilot_fixture", False),
        ("DELETE", "/collections/specpilot_fixture", False),
    ],
)
def test_repeat_audit_proxy_allows_only_qdrant_reads(
    gate: ModuleType, method: str, path: str, allowed: bool
) -> None:
    assert gate.is_proxy_request_allowed(method, path) is allowed


def test_repeat_audit_requires_successful_reads_and_zero_mutations(
    gate: ModuleType,
) -> None:
    logs = "\n".join(
        (
            '{"audit":"read","method":"GET","path":"/collections/x"}',
            '{"audit":"read","method":"POST","path":"/collections/x/points/scroll"}',
        )
    )
    assert gate.assert_read_only_proxy_audit(logs) == 2

    with pytest.raises(AssertionError, match="mutating"):
        mutation = (
            '{"audit":"mutation_rejected","method":"PUT",'
            '"path":"/collections/x/points"}'
        )
        gate.assert_read_only_proxy_audit(
            logs + "\n" + mutation
        )
    with pytest.raises(AssertionError, match="no Qdrant reads"):
        gate.assert_read_only_proxy_audit("")


def test_repeat_audit_requires_collection_count_and_scroll_after_readiness(
    gate: ModuleType,
) -> None:
    readiness = '{"audit":"read","method":"GET","path":"/"}'
    replay = "\n".join(
        (
            '{"audit":"read","method":"GET","path":"/collections/x"}',
            '{"audit":"read","method":"POST",'
            '"path":"/collections/x/points/count"}',
            '{"audit":"read","method":"POST",'
            '"path":"/collections/x/points/scroll"}',
        )
    )

    assert gate.assert_read_only_proxy_audit(
        readiness + "\n" + replay,
        after_records=1,
        collection="x",
    ) == 3
    with pytest.raises(AssertionError, match="count"):
        gate.assert_read_only_proxy_audit(
            readiness + "\n" + replay.replace(
                '{"audit":"read","method":"POST",'
                '"path":"/collections/x/points/count"}\n',
                "",
            ),
            after_records=1,
            collection="x",
        )
    with pytest.raises(AssertionError, match="after readiness"):
        gate.assert_read_only_proxy_audit(
            replay,
            after_records=3,
            collection="x",
        )
