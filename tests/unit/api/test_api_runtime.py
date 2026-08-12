from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx
import pytest
from pydantic import ValidationError

from specpilot.api.runtime import (
    ApiRuntimeConfig,
    _McpClientHook,
    create_runtime_app,
    load_runtime_config,
)

API_ENV = {
    "SPECPILOT_API_PROFILE": "fixture",
    "SPECPILOT_API_DSN": "postgresql://db/specpilot",
    "SPECPILOT_API_MCP_URL": "http://mcp:8080/mcp",
    "SPECPILOT_API_SESSION_SECRET": "s" * 32,
    "SPECPILOT_API_SESSION_AUDIENCE": "specpilot-api",
    "SPECPILOT_API_BIND_HOST": "127.0.0.1",
    "SPECPILOT_API_CONFIGURATION_HASH": "a" * 64,
    "SPECPILOT_API_PROMPT_ID": "l1-answer-v1",
    "SPECPILOT_API_PROMPT_HASH": "b" * 64,
}


@pytest.fixture(autouse=True)
def _clear_api_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def _set_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in API_ENV.items():
        monkeypatch.setenv(name, value)


def test_runtime_config_requires_every_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid(monkeypatch)
    config = load_runtime_config()

    assert config == ApiRuntimeConfig(
        profile="fixture",
        dsn=API_ENV["SPECPILOT_API_DSN"],
        mcp_url=API_ENV["SPECPILOT_API_MCP_URL"],
        session_secret=API_ENV["SPECPILOT_API_SESSION_SECRET"],
        session_audience=API_ENV["SPECPILOT_API_SESSION_AUDIENCE"],
        bind_host=API_ENV["SPECPILOT_API_BIND_HOST"],
        configuration_hash=API_ENV["SPECPILOT_API_CONFIGURATION_HASH"],
        prompt_id=API_ENV["SPECPILOT_API_PROMPT_ID"],
        prompt_hash=API_ENV["SPECPILOT_API_PROMPT_HASH"],
    )

    for missing in API_ENV:
        monkeypatch.delenv(missing)
        with pytest.raises(ValidationError):
            load_runtime_config()
        monkeypatch.setenv(missing, API_ENV[missing])


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SPECPILOT_API_PROFILE", "demo"),
        ("SPECPILOT_API_PROFILE", " fixture"),
        ("SPECPILOT_API_DSN", " postgresql://db/specpilot"),
        ("SPECPILOT_API_MCP_URL", "http://user:secret@mcp:8080/mcp"),
        ("SPECPILOT_API_MCP_URL", "http://mcp:8080/mcp?token=secret"),
        ("SPECPILOT_API_MCP_URL", "file:///tmp/mcp"),
        ("SPECPILOT_API_SESSION_SECRET", "short"),
        ("SPECPILOT_API_SESSION_SECRET", " " + "s" * 32),
        ("SPECPILOT_API_SESSION_AUDIENCE", "specpilot-api "),
        ("SPECPILOT_API_BIND_HOST", "0.0.0.0"),
        ("SPECPILOT_API_CONFIGURATION_HASH", "A" * 64),
        ("SPECPILOT_API_PROMPT_ID", " l1-answer-v1"),
        ("SPECPILOT_API_PROMPT_HASH", "b" * 63),
    ],
)
def test_fixture_runtime_rejects_unsafe_or_normalized_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _set_valid(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_runtime_config()


def test_real_runtime_forbids_demo_host_constraint_only_for_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid(monkeypatch)
    monkeypatch.setenv("SPECPILOT_API_PROFILE", "real")
    monkeypatch.setenv("SPECPILOT_API_BIND_HOST", "0.0.0.0")

    assert load_runtime_config().profile == "real"


@pytest.mark.anyio
async def test_runtime_factory_missing_or_invalid_config_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_runtime_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        missing = await client.get("/health")

    assert missing.status_code == 503
    assert missing.json() == {
        "status": "unavailable",
        "postgres": "down",
        "mcp": "down",
    }
    assert "SPECPILOT" not in missing.text

    _set_valid(monkeypatch)
    monkeypatch.setenv("SPECPILOT_API_MCP_URL", "http://mcp/mcp?secret=value")
    invalid = create_runtime_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=invalid), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == missing.json()
    assert "secret" not in response.text


@dataclass
class _TaskBoundContext:
    entered_task: asyncio.Task[object] | None = None
    exited_task: asyncio.Task[object] | None = None

    async def __aenter__(self) -> Self:
        self.entered_task = asyncio.current_task()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.exited_task = asyncio.current_task()
        if self.exited_task is not self.entered_task:
            raise RuntimeError("context exited from a different task")


@dataclass
class _ControlledContext(_TaskBoundContext):
    enter_started: asyncio.Event | None = None
    enter_release: asyncio.Event | None = None
    exit_started: asyncio.Event | None = None
    exit_release: asyncio.Event | None = None
    enter_error: BaseException | None = None
    exits: int = 0

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        if self.enter_started is not None:
            self.enter_started.set()
        if self.enter_release is not None:
            await self.enter_release.wait()
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exits += 1
        if self.exit_started is not None:
            self.exit_started.set()
        if self.exit_release is not None:
            await self.exit_release.wait()
        await super().__aexit__(exc_type, exc_value, traceback)


@pytest.mark.anyio
async def test_mcp_hook_enters_and_exits_task_bound_contexts_in_one_task() -> None:
    http = _TaskBoundContext()
    client = _TaskBoundContext()
    hook = _McpClientHook(http, client)  # type: ignore[arg-type]

    await hook.start()
    await asyncio.create_task(hook.aclose())

    assert http.entered_task is http.exited_task
    assert client.entered_task is client.exited_task


@pytest.mark.anyio
async def test_mcp_hook_start_cancellation_cleans_partial_entry() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    http = _ControlledContext()
    client = _ControlledContext(enter_started=entered, enter_release=release)
    hook = _McpClientHook(http, client)  # type: ignore[arg-type]

    start = asyncio.create_task(hook.start())
    await entered.wait()
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert http.exits == 1
    assert http.entered_task is http.exited_task
    assert hook._owner is None


@pytest.mark.anyio
async def test_mcp_hook_partial_enter_base_exception_closes_prior_context() -> None:
    marker = KeyboardInterrupt("marker")
    http = _ControlledContext()
    client = _ControlledContext(enter_error=marker)
    hook = _McpClientHook(http, client)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt) as raised:
        await hook.start()

    assert raised.value is marker
    assert http.exits == 1
    assert http.entered_task is http.exited_task
    assert hook._owner is None


@pytest.mark.anyio
async def test_mcp_hook_close_cancellation_finishes_owned_cleanup() -> None:
    exiting = asyncio.Event()
    release = asyncio.Event()
    http = _ControlledContext(exit_started=exiting, exit_release=release)
    client = _ControlledContext()
    hook = _McpClientHook(http, client)  # type: ignore[arg-type]
    await hook.start()

    close = asyncio.create_task(hook.aclose())
    await exiting.wait()
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close

    assert http.exits == 1
    assert client.exits == 1
    assert hook._owner is None


@pytest.mark.anyio
async def test_mcp_hook_close_is_idempotent_after_cleanup() -> None:
    http = _ControlledContext()
    client = _ControlledContext()
    hook = _McpClientHook(http, client)  # type: ignore[arg-type]

    await hook.start()
    await hook.aclose()
    await hook.aclose()

    assert http.exits == 1
    assert client.exits == 1
