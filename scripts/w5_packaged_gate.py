#!/usr/bin/env python3
"""Bounded, destructive-only-within-one-project W5 packaged demo gate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PREFIX = "specpilot-w5-task9-packaged-"
RUN_TOKEN = f"{os.getpid()}-{secrets.token_hex(4)}"
PROJECT_NAME = f"{PROJECT_PREFIX}{RUN_TOKEN}"
COMPOSE_FILES = ("compose.yaml", "compose.demo.yaml", "compose.w5-gate.yaml")
ENV_FILE = ROOT / "fixtures" / "demo" / "w5-gate.env"


API_PORT = 49152 + int(RUN_TOKEN.rsplit("-", 1)[1], 16) % 16384
os.environ["SPECPILOT_W5_API_PORT"] = str(API_PORT)
API_BASE_URL = f"http://127.0.0.1:{API_PORT}"
EXPECTED_MIGRATIONS = tuple(f"{number:03d}" for number in range(1, 17))
COMMAND_TIMEOUT_SECONDS = 180
GATE_TIMEOUT_SECONDS = 480
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = re.compile(r"^specpilot-w5-task9-packaged-[0-9]+-[0-9a-f]{8}$")
GATE_LABEL = "io.specpilot.w5.gate"
VOLUME_NAMES = {
    name: f"{PROJECT_NAME}_{name}"
    for name in ("w5-corpus", "w5-manifests", "w5-ready", "w5-sources")
}
INTERNAL_NETWORK = f"{PROJECT_NAME}_internal"
API_IMAGE = f"{PROJECT_NAME}-api"
MCP_IMAGE = f"{PROJECT_NAME}-mcp"
FIXTURE_INIT_IMAGE = f"{PROJECT_NAME}-fixture-init"
MIGRATION_IMAGE = f"{PROJECT_NAME}-migrations"
AUDIT_PROXY_NAME = f"{PROJECT_NAME}-qdrant-read-audit"
IMAGE_TAGS = (API_IMAGE, MCP_IMAGE, FIXTURE_INIT_IMAGE, MIGRATION_IMAGE)
_READ_POST_PATH = re.compile(
    r"^/collections/[^/?]+/points/(?:count|scroll)(?:\?.*)?$"
)

if not _PROJECT.fullmatch(PROJECT_NAME):
    raise RuntimeError("internal packaged gate project name is invalid")


@dataclass(frozen=True, slots=True)
class ResourceIds:
    containers: tuple[str, ...]
    volumes: tuple[str, ...]
    networks: tuple[str, ...]
    images: tuple[str, ...]

_SCENARIOS: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    (
        "l1_answered",
        "L1",
        "answered",
        frozenset(
            {
                "tool_finished",
                "egress_summary",
                "verifier_summary",
                "answer_outcome",
                "terminal",
            }
        ),
    ),
    (
        "l2_answered",
        "L2",
        "answered",
        frozenset(
            {
                "tool_finished",
                "egress_summary",
                "compliance_summary",
                "verifier_summary",
                "semantic_summary",
                "terminal",
            }
        ),
    ),
    (
        "evidence_refused",
        "L1",
        "refused",
        frozenset(
            {
                "tool_finished",
                "egress_summary",
                "verifier_summary",
                "answer_outcome",
                "terminal",
            }
        ),
    ),
    (
        "verifier_recovered",
        "L2",
        "answered",
        frozenset(
            {
                "compliance_summary",
                "verifier_summary",
                "semantic_summary",
                "recovery_summary",
                "terminal",
            }
        ),
    ),
)


def compose_command(*arguments: str) -> list[str]:
    command = [
        "docker",
        "compose",
        "-p",
        PROJECT_NAME,
        "--env-file",
        str(ENV_FILE),
    ]
    for filename in COMPOSE_FILES:
        command.extend(("-f", filename))
    command.extend(("--profile", "demo", *arguments))
    return command


def run_command(
    command: list[str],
    *,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one transcripted command without a shell and with a hard bound."""
    print(f"$ {shlex.join(command)}", flush=True)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired as error:
        elapsed = time.monotonic() - started
        print(f"command_timeout_seconds={elapsed:.2f}", file=sys.stderr, flush=True)
        raise RuntimeError(f"command exceeded {timeout}s: {command[0]}") from error
    elapsed = time.monotonic() - started
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )
    print(
        f"command_exit={result.returncode} elapsed_seconds={elapsed:.2f}",
        flush=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {result.returncode}: {shlex.join(command)}"
        )
    return result


def _docker_run_command(
    *,
    name: str,
    image: str,
    arguments: Sequence[str],
    network: str | None = None,
    mounts: Sequence[str] = (),
    environment: Sequence[str] = (),
    user: str | None = None,
    entrypoint: str | None = None,
) -> list[str]:
    command = [
        "docker", "run",
        "--rm",
        "--name", name,
        "--label", f"{GATE_LABEL}={RUN_TOKEN}",
    ]
    if network is not None:
        command.extend(("--network", network))
    if user is not None:
        command.extend(("--user", user))
    for mount in mounts:
        command.extend(("--volume", mount))
    for value in environment:
        command.extend(("--env", value))
    if entrypoint is not None:
        command.extend(("--entrypoint", entrypoint))
    command.append(image)
    command.extend(arguments)
    return command


def _artifact_mounts(*, read_only: bool) -> tuple[str, ...]:
    suffix = ":ro" if read_only else ""
    return tuple(
        f"{VOLUME_NAMES[name]}:/run/specpilot/{destination}{suffix}"
        for name, destination in (
            ("w5-corpus", "corpus"),
            ("w5-manifests", "manifests"),
            ("w5-ready", "ready"),
            ("w5-sources", "sources"),
        )
    )


def _create_artifact_volumes() -> None:
    for name in VOLUME_NAMES.values():
        result = run_command(
            [
                "docker",
                "volume",
                "create",
                "--label",
                f"{GATE_LABEL}={RUN_TOKEN}",
                name,
            ]
        )
        if result.stdout.strip() != name:
            raise AssertionError("Docker created an unexpected artifact volume")


def _initialize_artifact_volumes() -> None:
    script = (
        "mkdir -p /run/specpilot/corpus /run/specpilot/manifests "
        "/run/specpilot/ready /run/specpilot/sources; "
        "cp /run/specpilot/fixture/source.xml "
        "/run/specpilot/sources/source.xml; "
        "chown -R 10001:10001 /run/specpilot/corpus "
        "/run/specpilot/manifests /run/specpilot/ready /run/specpilot/sources; "
        "chmod 0700 /run/specpilot/corpus /run/specpilot/manifests "
        "/run/specpilot/ready /run/specpilot/sources; "
        "chmod 0400 /run/specpilot/sources/source.xml"
    )
    run_command(
        _docker_run_command(
            name=f"{PROJECT_NAME}-volume-init",
            image=FIXTURE_INIT_IMAGE,
            arguments=("-ec", script),
            mounts=_artifact_mounts(read_only=False),
            user="0:0",
            entrypoint="sh",
        )
    )


def _apply_migrations() -> None:
    script = (
        "set -- /opt/specpilot/migrations/[0-9][0-9][0-9]_*.sql; "
        'test "$#" -eq 16; expected=1; '
        "for migration do "
        'prefix="$(basename "$migration" | cut -c1-3)"; '
        'test "$prefix" = "$(printf \'%03d\' "$expected")"; '
        "psql --host=postgres --username=specpilot --dbname=specpilot "
        '--set=ON_ERROR_STOP=1 --file="$migration"; '
        'echo "migration=$(basename "$migration")"; '
        "expected=$((expected + 1)); done; "
        'test "$expected" -eq 17; echo "applied=16"'
    )
    result = run_command(
        _docker_run_command(
            name=f"{PROJECT_NAME}-migrate",
            image=MIGRATION_IMAGE,
            arguments=("sh", "-ec", script),
            network=INTERNAL_NETWORK,
            environment=("PGPASSWORD=specpilot-local-only",),
        )
    )
    if "applied=16" not in result.stdout:
        raise AssertionError("packaged migration run did not apply 001..016")


def _fixture_init_command(
    *, network: str, qdrant_url: str = "http://qdrant:6333"
) -> list[str]:
    return _docker_run_command(
        name=f"{PROJECT_NAME}-fixture-init-{network.replace('/', '-')}",
        image=FIXTURE_INIT_IMAGE,
        arguments=(
            "-m",
            "specpilot.cli",
            "corpus",
            "init-fixture",
            "--fixture-dir",
            "/run/specpilot/fixture",
            "--source-manifest-dir",
            "/run/specpilot/manifests",
            "--corpus-manifest-dir",
            "/run/specpilot/corpus",
            "--ready-dir",
            "/run/specpilot/ready",
            "--qdrant-url",
            qdrant_url,
        ),
        network=network,
        mounts=_artifact_mounts(read_only=False),
        entrypoint="python",
    )


def is_proxy_request_allowed(method: str, path: str) -> bool:
    """Allow Qdrant reads, including its POST-based scroll operation."""
    normalized = method.upper()
    return normalized in {"GET", "HEAD"} or (
        normalized == "POST" and _READ_POST_PATH.fullmatch(path) is not None
    )


_AUDIT_PROXY_PROGRAM = r'''
import http.server
import json
import re
import urllib.error
import urllib.request

BACKEND = "http://qdrant:6333"
READ_POST = re.compile(r"^/collections/[^/?]+/points/(?:count|scroll)(?:\?.*)?$")

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _handle(self):
        method = self.command.upper()
        allowed = method in {"GET", "HEAD"} or (
            method == "POST" and READ_POST.fullmatch(self.path) is not None
        )
        audit = "read" if allowed else "mutation_rejected"
        record = {"audit": audit, "method": method, "path": self.path}
        print(json.dumps(record, sort_keys=True), flush=True)
        if not allowed:
            body = b'{"status":"mutation rejected by W5 replay audit"}'
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else None
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        request = urllib.request.Request(
            BACKEND + self.path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        body = b"" if method == "HEAD" else response.read()
        self.send_response(response.status)
        content_type = response.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

http.server.ThreadingHTTPServer(("0.0.0.0", 6334), Handler).serve_forever()
'''.strip()


def _start_read_only_qdrant_proxy() -> None:
    run_command(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            AUDIT_PROXY_NAME,
            "--label",
            f"{GATE_LABEL}={RUN_TOKEN}",
            "--network",
            INTERNAL_NETWORK,
            "--entrypoint",
            "python",
            API_IMAGE,
            "-c",
            _AUDIT_PROXY_PROGRAM,
        ]
    )
    run_command(
        [
            "docker",
            "exec",
            AUDIT_PROXY_NAME,
            "python",
            "-c",
            (
                "import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:6334/',timeout=10).read()"
            ),
        ],
        timeout=20,
    )


def assert_read_only_proxy_audit(logs: str) -> int:
    records = [
        json.loads(line)
        for line in logs.splitlines()
        if line.strip().startswith("{")
    ]
    if any(record.get("audit") == "mutation_rejected" for record in records):
        raise AssertionError("fixture replay attempted a mutating Qdrant request")
    reads = sum(1 for record in records if record.get("audit") == "read")
    if not reads:
        raise AssertionError("fixture replay made no Qdrant reads through audit proxy")
    return reads


def _audit_proxy_logs() -> str:
    result = run_command(["docker", "logs", AUDIT_PROXY_NAME])
    reads = assert_read_only_proxy_audit(result.stdout)
    print(f"fixture_repeat_qdrant_reads={reads} mutating_requests=0", flush=True)
    return result.stdout


_LEDGER_BOOTSTRAP_PROGRAM = r'''
import asyncio
import json
import os
import sys

from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.manifests.store import ManifestStore

async def main():
    corpus_manifest_id = sys.argv[1]
    policy = EgressPolicy.load_fixture()
    ledger = PostgresEgressLedger(
        os.environ["LEDGER_DSN"],
        policy=policy,
        manifests=ManifestStore("/run/specpilot/manifests"),
    )
    epoch_id = await ledger.initialize_corpus(corpus_manifest_id)
    print(json.dumps({
        "corpus_manifest_id": corpus_manifest_id,
        "corpus_ledger_id": epoch_id,
        "policy_hash": policy.policy_hash,
    }, sort_keys=True))

asyncio.run(main())
'''.strip()


def _ledger_bootstrap_command(corpus_manifest_id: str) -> list[str]:
    return _docker_run_command(
        name=f"{PROJECT_NAME}-ledger-bootstrap",
        image=FIXTURE_INIT_IMAGE,
        network=INTERNAL_NETWORK,
        mounts=_artifact_mounts(read_only=True),
        environment=(
            "LEDGER_DSN=postgresql://specpilot:specpilot-local-only@"
            "postgres:5432/specpilot",
        ),
        entrypoint="python",
        arguments=("-c", _LEDGER_BOOTSTRAP_PROGRAM, corpus_manifest_id),
    )


def _initialize_fixture_ledger(corpus_manifest_id: str) -> None:
    first_run = run_command(_ledger_bootstrap_command(corpus_manifest_id))
    first = _last_json(first_run.stdout)
    second = _last_json(
        run_command(_ledger_bootstrap_command(corpus_manifest_id)).stdout
    )
    if first != second or first.get("corpus_manifest_id") != corpus_manifest_id:
        raise AssertionError("fixture corpus ledger bootstrap was not idempotent")
    if not _SHA256.fullmatch(str(first.get("policy_hash", ""))):
        raise AssertionError("fixture corpus ledger policy identity is invalid")
    print("fixture_corpus_ledger_bootstrap=replayed", flush=True)


def _last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        start = line.find("{")
        if start < 0:
            continue
        try:
            payload = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AssertionError("command produced no JSON object")


def parse_ready_payload(text: str) -> dict[str, Any]:
    payload = _last_json(text)
    required = {
        "status",
        "ready_id",
        "mode",
        "source_manifest_ids",
        "corpus_manifest_id",
        "collection",
        "point_count",
        "inventory_root_sha256",
    }
    if set(payload) != required:
        raise AssertionError("fixture ready payload fields changed")
    if payload["status"] != "ready" or payload["mode"] != "fixture":
        raise AssertionError("fixture initialization did not reach ready")
    hashes = (
        payload["ready_id"],
        payload["corpus_manifest_id"],
        payload["inventory_root_sha256"],
        *payload["source_manifest_ids"],
    )
    if not hashes or any(
        not isinstance(item, str) or not _SHA256.fullmatch(item) for item in hashes
    ):
        raise AssertionError("fixture ready payload contains an invalid identity")
    if payload["point_count"] != 6:
        raise AssertionError("fixture ready payload has an unexpected point count")
    return payload


def assert_identical_replay(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> None:
    if dict(first) != dict(second):
        raise AssertionError("fixture repeat changed ready identity")


def assert_scenario_events(
    *,
    scenario_id: str,
    expected_terminal: str,
    required_kinds: Set[str],
    private_marker: str,
    raw_sse: str,
) -> list[dict[str, Any]]:
    if private_marker in raw_sse:
        raise AssertionError(f"{scenario_id}: private marker leaked into SSE")
    events: list[dict[str, Any]] = []
    for line in raw_sse.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if not isinstance(payload, dict):
            raise AssertionError(f"{scenario_id}: SSE event is not an object")
        events.append(payload)
    if not events or events[-1].get("kind") != "terminal":
        raise AssertionError(f"{scenario_id}: SSE has no terminal event")
    if events[-1].get("status") != expected_terminal:
        raise AssertionError(f"{scenario_id}: terminal status changed")
    if not required_kinds <= {str(event.get("kind")) for event in events}:
        raise AssertionError(f"{scenario_id}: required SSE kinds are missing")
    if [event.get("sequence") for event in events] != list(range(1, len(events) + 1)):
        raise AssertionError(f"{scenario_id}: SSE sequences are not contiguous")
    return events


def _validate_migrations() -> None:
    files = sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    prefixes = tuple(path.name[:3] for path in files)
    if prefixes != EXPECTED_MIGRATIONS:
        raise AssertionError(
            f"packaged gate requires migrations 001..016 exactly, got {prefixes}"
        )


def _qdrant_snapshot(collection: str) -> dict[str, Any]:
    program = """
import hashlib
import json
import urllib.request

collection = __import__('sys').argv[1]
base = 'http://qdrant:6333/collections/' + collection
info = json.load(urllib.request.urlopen(base, timeout=10))['result']
request = urllib.request.Request(
    base + '/points/scroll',
    data=json.dumps({'limit': 100, 'with_payload': True, 'with_vector': True}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST',
)
points = json.load(urllib.request.urlopen(request, timeout=10))['result']['points']
encoded = json.dumps(
    points, allow_nan=False, separators=(',', ':'), sort_keys=True
).encode()
print(json.dumps({
    'point_count': info['points_count'],
    'points_sha256': hashlib.sha256(encoded).hexdigest(),
}, sort_keys=True))
""".strip()
    result = run_command(
        _docker_run_command(
            name=f"{PROJECT_NAME}-qdrant-snapshot",
            image=API_IMAGE,
            network=INTERNAL_NETWORK,
            arguments=(
            "python",
            "-c",
            program,
            collection,
            ),
        )
    )
    snapshot = _last_json(result.stdout)
    if snapshot.get("point_count") != 6 or not _SHA256.fullmatch(
        str(snapshot.get("points_sha256", ""))
    ):
        raise AssertionError("Qdrant fixture snapshot is incomplete")
    return snapshot


def _assert_health() -> None:
    mcp = run_command(
        compose_command(
            "exec",
            "-T",
            "mcp",
            "python",
            "-c",
            (
                "import json,urllib.request; "
                "print(json.dumps(json.load(urllib.request.urlopen("
                "'http://127.0.0.1:8080/health',timeout=10)),sort_keys=True))"
            ),
        )
    )
    if _last_json(mcp.stdout) != {"status": "ok"}:
        raise AssertionError("packaged MCP health is not ok")
    with httpx.Client(base_url=API_BASE_URL, timeout=15, trust_env=False) as client:
        response = client.get("/health")
        response.raise_for_status()
        if response.json() != {"status": "ok", "postgres": "ok", "mcp": "ok"}:
            raise AssertionError("packaged API health is not fully ok")


def _run_http_scenarios(ready: Mapping[str, Any]) -> None:
    source_id = ready["source_manifest_ids"][0]
    corpus_id = ready["corpus_manifest_id"]
    with httpx.Client(base_url=API_BASE_URL, timeout=20, trust_env=False) as client:
        session = client.post("/sessions/demo")
        session.raise_for_status()
        token = session.cookies.get("specpilot_session")
        if not token:
            raise AssertionError("packaged demo session cookie is missing")
        headers = {"Authorization": f"Bearer {token}"}
        for scenario_id, task_level, terminal, required in _SCENARIOS:
            private_marker = f"packaged-private-{scenario_id}-{uuid4()}"
            started = time.monotonic()
            accepted = client.post(
                "/chat",
                headers=headers,
                json={
                    "question": private_marker,
                    "request_id": str(uuid4()),
                    "evaluation_root_id": f"w5-packaged-{scenario_id}",
                    "task_level": task_level,
                    "scenario_id": scenario_id,
                    "source_manifest_id": source_id,
                    "corpus_manifest_id": corpus_id,
                },
            )
            if accepted.status_code != 202:
                print(
                    f"packaged_chat_status={accepted.status_code} "
                    f"body={accepted.text}",
                    file=sys.stderr,
                    flush=True,
                )
                run_command(
                    compose_command(
                        "exec",
                        "-T",
                        "postgres",
                        "psql",
                        "--username=specpilot",
                        "--dbname=specpilot",
                        "--tuples-only",
                        "--command",
                        (
                            "SELECT status, terminal_reason, count(*) "
                            "FROM specpilot_run GROUP BY status, terminal_reason"
                        ),
                    ),
                    check=False,
                )
            accepted.raise_for_status()
            run_id = accepted.json()["run_id"]
            streamed = client.get(f"/runs/{run_id}/events", headers=headers)
            streamed.raise_for_status()
            if not streamed.headers.get("content-type", "").startswith(
                "text/event-stream"
            ):
                raise AssertionError(f"{scenario_id}: response is not SSE")
            events = assert_scenario_events(
                scenario_id=scenario_id,
                expected_terminal=terminal,
                required_kinds=required,
                private_marker=private_marker,
                raw_sse=streamed.text,
            )
            print(
                json.dumps(
                    {
                        "events": len(events),
                        "http_sse_elapsed_seconds": round(
                            time.monotonic() - started, 3
                        ),
                        "run_id": run_id,
                        "scenario": scenario_id,
                        "terminal": terminal,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )


def _bind_ready_environment(ready: Mapping[str, Any]) -> None:
    source_ids = ready["source_manifest_ids"]
    if not isinstance(source_ids, list) or len(source_ids) != 1:
        raise AssertionError("fixture ready payload must contain one source")
    os.environ["SPECPILOT_MCP_CORPUS_MANIFEST_ID"] = str(
        ready["corpus_manifest_id"]
    )
    os.environ["SPECPILOT_MCP_READY_ID"] = str(ready["ready_id"])
    os.environ["SPECPILOT_MCP_SOURCES_JSON"] = json.dumps(
        [
            {
                "manifest_id": source_ids[0],
                "xml_path": "/run/specpilot/sources/source.xml",
            }
        ],
        separators=(",", ":"),
    )


def exercise_packaged_demo() -> None:
    _validate_migrations()
    run_command(compose_command("config", "--quiet"))
    run_command(compose_command("build", "api", "mcp", "fixture-init"))
    run_command(
        [
            "docker",
            "build",
            "--file",
            "docker/migrations.Dockerfile",
            "--tag",
            MIGRATION_IMAGE,
            ".",
        ]
    )
    _create_artifact_volumes()
    run_command(
        compose_command(
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "120",
            "postgres",
            "qdrant",
        )
    )
    _initialize_artifact_volumes()
    _apply_migrations()
    first_run = run_command(_fixture_init_command(network=INTERNAL_NETWORK))
    first = parse_ready_payload(first_run.stdout)
    before = _qdrant_snapshot(str(first["collection"]))

    _start_read_only_qdrant_proxy()
    repeat_started = time.monotonic()
    repeated = run_command(
        _fixture_init_command(
            network=INTERNAL_NETWORK,
            qdrant_url=f"http://{AUDIT_PROXY_NAME}:6334",
        ),
        check=False,
    )
    repeat_elapsed = time.monotonic() - repeat_started
    _audit_proxy_logs()
    if repeated.returncode != 0:
        raise RuntimeError(
            f"audited fixture repeat failed with exit {repeated.returncode}"
        )
    second = parse_ready_payload(repeated.stdout)
    assert_identical_replay(first, second)
    print(
        f"fixture_repeat_read_only_qdrant_seconds={repeat_elapsed:.3f}", flush=True
    )
    after = _qdrant_snapshot(str(first["collection"]))
    if after != before:
        raise AssertionError("fixture repeat changed Qdrant points")
    print(
        "qdrant_replay_unchanged="
        + hashlib.sha256(
            json.dumps(after, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        flush=True,
    )

    _initialize_fixture_ledger(str(first["corpus_manifest_id"]))
    _bind_ready_environment(first)
    run_command(
        compose_command(
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "120",
            "mcp",
            "api",
        )
    )
    _assert_health()
    _run_http_scenarios(first)


def _listed(command: list[str]) -> tuple[str, ...]:
    result = run_command(command, timeout=20)
    return tuple(sorted({line for line in result.stdout.splitlines() if line}))


def discover_resource_ids() -> ResourceIds:
    containers = set(
        _listed(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label={GATE_LABEL}={RUN_TOKEN}",
            ]
        )
    )
    containers.update(
        _listed(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={PROJECT_NAME}",
            ]
        )
    )
    volumes = set(
        _listed(
            [
                "docker",
                "volume",
                "ls",
                "--quiet",
                "--filter",
                f"label={GATE_LABEL}={RUN_TOKEN}",
            ]
        )
    )
    volumes.update(
        _listed(
            [
                "docker",
                "volume",
                "ls",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={PROJECT_NAME}",
            ]
        )
    )
    networks = _listed(
        [
            "docker",
            "network",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={PROJECT_NAME}",
        ]
    )
    images: list[str] = []
    for tag in IMAGE_TAGS:
        inspected = run_command(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            timeout=20,
            check=False,
        )
        if inspected.returncode == 0:
            image_id = inspected.stdout.strip()
            print(
                json.dumps(
                    {"image_id": image_id, "image_tag": tag}, sort_keys=True
                ),
                flush=True,
            )
            images.append(tag)
    return ResourceIds(
        containers=tuple(sorted(containers)),
        volumes=tuple(sorted(volumes)),
        networks=networks,
        images=tuple(images),
    )


def cleanup_resource_ids(resources: ResourceIds) -> None:
    failures: list[str] = []
    for kind, identifiers in (
        ("container", resources.containers),
        ("volume", resources.volumes),
        ("network", resources.networks),
        ("image", resources.images),
    ):
        for identifier in identifiers:
            if kind == "container":
                command = ["docker", "rm", "--force", identifier]
            elif kind == "volume":
                command = ["docker", "volume", "rm", identifier]
            elif kind == "network":
                command = ["docker", "network", "rm", identifier]
            else:
                command = [
                    "docker",
                    "image",
                    "rm",
                    "--force",
                    "--no-prune",
                    identifier,
                ]
            try:
                result = run_command(command, timeout=20, check=False)
            except RuntimeError:
                failures.append(f"{kind}:{identifier}:timeout")
                continue
            if result.returncode != 0:
                failures.append(f"{kind}:{identifier}:exit-{result.returncode}")
    if failures:
        raise RuntimeError("packaged cleanup failed: " + ", ".join(failures))


def cleanup_resources() -> None:
    cleanup_resource_ids(discover_resource_ids())


def _deadline(_signum: int, _frame: object) -> None:
    raise TimeoutError(f"packaged gate exceeded {GATE_TIMEOUT_SECONDS}s")


def run_gate() -> None:
    previous = signal.signal(signal.SIGALRM, _deadline)
    signal.alarm(GATE_TIMEOUT_SECONDS)
    try:
        exercise_packaged_demo()
    except BaseException:
        run_command(
            compose_command(
                "logs",
                "--no-color",
                "--tail",
                "120",
                "api",
                "mcp",
                "postgres",
            ),
            timeout=20,
            check=False,
        )
        raise
    finally:
        try:
            cleanup_resources()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)


def main() -> int:
    try:
        run_gate()
    except (
        AssertionError,
        OSError,
        RuntimeError,
        TimeoutError,
        httpx.HTTPError,
    ) as error:
        print(f"packaged_demo_gate_failed: {error}", file=sys.stderr)
        return 1
    print("packaged_demo_gate=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
