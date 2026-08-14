from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

import specpilot.api.sse as sse
from specpilot.api.sse import (
    BoundedStreamingResponse,
    RunEventStreamConfig,
    encode_event,
    parse_last_event_id,
    stream_owned_events,
)
from specpilot.runs.contracts import (
    RunEventPage,
    RunStatus,
    StateTransitionEvent,
    TerminalEvent,
)


def test_encode_event_uses_one_compact_data_line_and_blank_terminator() -> None:
    """Catches verbose JSON or invalid SSE framing at the HTTP boundary."""
    event = StateTransitionEvent(
        sequence=7,
        previous_status=RunStatus.QUEUED,
        status=RunStatus.RUNNING,
        reason=None,
    )

    assert encode_event(event) == (
        b"id: 7\n"
        b"event: state_transition\n"
        b'data: {"sequence":7,"kind":"state_transition",'
        b'"previous_status":"queued","status":"running","reason":null}\n\n'
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), ("0", 0), ("1", 1), ("10000", 10_000)],
)
def test_parse_last_event_id_accepts_only_canonical_bounded_cursors(
    value: str | None, expected: int
) -> None:
    """Catches valid reconnect cursors being rejected at either boundary."""
    assert parse_last_event_id(value) == expected


@pytest.mark.parametrize(
    "value", ["+1", "01", " 1", "1 ", "-1", "event-1", "10001", ""]
)
def test_parse_last_event_id_rejects_noncanonical_or_unbounded_cursors(
    value: str,
) -> None:
    """Catches ambiguous cursors entering owner authorization or persistence."""
    with pytest.raises(ValueError, match="^invalid_last_event_id$"):
        parse_last_event_id(value)


def _transition(sequence: int) -> StateTransitionEvent:
    return StateTransitionEvent(
        sequence=sequence,
        previous_status=None if sequence == 1 else RunStatus.QUEUED,
        status=RunStatus.QUEUED if sequence == 1 else RunStatus.RUNNING,
        reason=None,
    )


def _terminal(sequence: int) -> TerminalEvent:
    return TerminalEvent(
        sequence=sequence,
        status=RunStatus.FAILED,
        reason="provider_timeout",
    )


@dataclass
class FakeStore:
    pages: list[RunEventPage | None | BaseException]
    calls: list[tuple[UUID, str, int, int]] = field(default_factory=list)

    async def read_events_owned(
        self,
        run_id: UUID,
        session_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> RunEventPage | None:
        self.calls.append((run_id, session_id, after_sequence, limit))
        page = self.pages.pop(0)
        if isinstance(page, BaseException):
            raise page
        return page


@dataclass
class ControlledClock:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds
        await asyncio.sleep(0)


@pytest.mark.anyio
async def test_stream_replays_resumed_pages_without_prefetching_more_than_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches cursor loss, page skipping, or unbounded read-ahead during replay."""
    clock = ControlledClock()
    monkeypatch.setattr(sse, "_monotonic", clock)
    monkeypatch.setattr(sse, "_sleep", clock.sleep)
    run_id = uuid4()
    store = FakeStore(
        [
            RunEventPage(
                events=(_transition(2), _transition(3)),
                terminal=False,
                last_sequence=3,
            ),
            RunEventPage(
                events=(_terminal(4),), terminal=True, last_sequence=4
            ),
        ]
    )
    stream = stream_owned_events(
        store,
        run_id,
        "owner-a",
        after_sequence=1,
        config=RunEventStreamConfig(page_size=2),
    )

    assert await anext(stream) == encode_event(_transition(2))
    assert len(store.calls) == 1
    assert await anext(stream) == encode_event(_transition(3))
    assert len(store.calls) == 1
    assert await anext(stream) == encode_event(_terminal(4))
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert store.calls == [
        (run_id, "owner-a", 1, 2),
        (run_id, "owner-a", 3, 2),
    ]


@pytest.mark.anyio
async def test_stream_stops_draining_a_page_after_slow_consumer_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches later buffered frames escaping after connection expiry."""
    clock = ControlledClock()
    monkeypatch.setattr(sse, "_monotonic", clock)
    monkeypatch.setattr(sse, "_sleep", clock.sleep)
    store = FakeStore(
        [
            RunEventPage(
                events=(_transition(1), _transition(2)),
                terminal=False,
                last_sequence=2,
            )
        ]
    )
    stream = stream_owned_events(
        store,
        uuid4(),
        "owner-a",
        after_sequence=0,
        config=RunEventStreamConfig(max_connection_seconds=60),
    )

    assert await anext(stream) == encode_event(_transition(1))
    clock.value = 61

    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert len(store.calls) == 1


@pytest.mark.anyio
async def test_stream_emits_exactly_one_comment_per_idle_heartbeat_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches heartbeats carrying event data or firing more than once per interval."""
    clock = ControlledClock()
    monkeypatch.setattr(sse, "_monotonic", clock)
    monkeypatch.setattr(sse, "_sleep", clock.sleep)
    empty = RunEventPage(events=(), terminal=False, last_sequence=0)
    store = FakeStore([empty, empty, empty, empty, empty])

    frames = [
        frame
        async for frame in stream_owned_events(
            store,
            uuid4(),
            "owner-a",
            after_sequence=0,
            config=RunEventStreamConfig(
                heartbeat_seconds=2,
                poll_seconds=1,
                max_connection_seconds=5,
                page_size=1,
            ),
        )
    ]

    assert frames == [b": keep-alive\n\n", b": keep-alive\n\n"]
    assert all(b"data:" not in frame for frame in frames)


@pytest.mark.anyio
async def test_stream_terminal_page_closes_without_poll_or_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a completed stream retaining a connection after its terminal event."""
    clock = ControlledClock()
    monkeypatch.setattr(sse, "_monotonic", clock)
    monkeypatch.setattr(sse, "_sleep", clock.sleep)
    store = FakeStore(
        [RunEventPage(events=(_terminal(1),), terminal=True, last_sequence=1)]
    )

    frames = [
        frame
        async for frame in stream_owned_events(
            store,
            uuid4(),
            "owner-a",
            after_sequence=0,
            config=RunEventStreamConfig(),
        )
    ]

    assert frames == [encode_event(_terminal(1))]
    assert len(store.calls) == 1
    assert clock.sleeps == []


@pytest.mark.anyio
async def test_stream_cancellation_releases_pending_read_without_mutating_run() -> None:
    """Catches disconnect cancellation being swallowed or converted into a run write."""

    class BlockingStore:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.reads = 0

        async def read_events_owned(self, *_args: Any, **_kwargs: Any) -> None:
            self.reads += 1
            self.entered.set()
            try:
                await asyncio.Future()
            finally:
                self.cleaned.set()

    store = BlockingStore()
    stream: AsyncIterator[bytes] = stream_owned_events(
        store,
        uuid4(),
        "owner-a",
        after_sequence=0,
        config=RunEventStreamConfig(),
    )
    pending = asyncio.create_task(anext(stream))
    await store.entered.wait()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert store.cleaned.is_set()
    assert store.reads == 1


@pytest.mark.anyio
async def test_stream_deadline_cancels_a_stalled_owner_read() -> None:
    """Catches a blocked dependency bypassing the total connection lifetime."""

    class StalledStore:
        def __init__(self) -> None:
            self.cleaned = asyncio.Event()

        async def read_events_owned(self, *_args: Any, **_kwargs: Any) -> None:
            try:
                await asyncio.Future()
            finally:
                self.cleaned.set()

    store = StalledStore()

    async def collect() -> list[bytes]:
        return [
            frame
            async for frame in stream_owned_events(
                store,
                uuid4(),
                "owner-a",
                after_sequence=0,
                config=RunEventStreamConfig(max_connection_seconds=0.01),
            )
        ]

    frames = await asyncio.wait_for(collect(), timeout=0.1)

    assert frames == []
    assert store.cleaned.is_set()


@pytest.mark.anyio
async def test_stream_closes_silently_when_owner_store_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches dependency details crossing an already-started SSE response."""
    clock = ControlledClock()
    monkeypatch.setattr(sse, "_monotonic", clock)
    monkeypatch.setattr(sse, "_sleep", clock.sleep)
    store = FakeStore([RuntimeError("secret-postgres-host-and-query")])

    frames = [
        frame
        async for frame in stream_owned_events(
            store,
            uuid4(),
            "owner-a",
            after_sequence=0,
            config=RunEventStreamConfig(),
        )
    ]

    assert frames == []


@pytest.mark.parametrize("page_size", [0, 257])
def test_stream_config_rejects_page_sizes_outside_store_cap(page_size: int) -> None:
    """Catches callers bypassing the durable page's 256-event memory bound."""
    with pytest.raises(ValueError, match="^invalid_stream_config$"):
        RunEventStreamConfig(page_size=page_size)


@pytest.mark.anyio
async def test_bounded_response_sends_final_empty_body_after_idle_expiry() -> None:
    """Catches ordinary lifetime expiry leaving an incomplete ASGI response."""

    class IdleIterator:
        def __init__(self) -> None:
            self.first = True
            self.closed = asyncio.Event()

        def __aiter__(self) -> IdleIterator:
            return self

        async def __anext__(self) -> bytes:
            if self.first:
                self.first = False
                return b": keep-alive\n\n"
            await asyncio.Future()

        async def aclose(self) -> None:
            self.closed.set()

    iterator = IdleIterator()
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    response = BoundedStreamingResponse(
        iterator,
        max_connection_seconds=0.01,
        media_type="text/event-stream",
    )

    await asyncio.wait_for(response.stream_response(send), timeout=0.1)

    assert [message["type"] for message in messages] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]
    assert messages[1]["more_body"] is True
    assert messages[2] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }
    assert iterator.closed.is_set()


@pytest.mark.anyio
async def test_bounded_response_cancels_stalled_send_and_closes_iterator() -> None:
    """Catches ASGI backpressure retaining a page beyond connection expiry."""

    class ClosingIterator:
        def __init__(self, source: AsyncIterator[bytes]) -> None:
            self.source = source
            self.closed = asyncio.Event()

        def __aiter__(self) -> ClosingIterator:
            return self

        async def __anext__(self) -> bytes:
            return await anext(self.source)

        async def aclose(self) -> None:
            await self.source.aclose()
            self.closed.set()

    run_id = uuid4()
    store = FakeStore(
        [
            RunEventPage(
                events=(_transition(1), _transition(2)),
                terminal=False,
                last_sequence=2,
            )
        ]
    )
    source = stream_owned_events(
        store,
        run_id,
        "owner-a",
        after_sequence=0,
        config=RunEventStreamConfig(),
    )
    iterator = ClosingIterator(source)
    body_attempts = 0
    body_cancellations = 0

    async def send(message: dict[str, Any]) -> None:
        nonlocal body_attempts, body_cancellations
        if message["type"] != "http.response.body":
            return
        body_attempts += 1
        try:
            await asyncio.Future()
        finally:
            body_cancellations += 1

    response = BoundedStreamingResponse(
        iterator,
        max_connection_seconds=0.01,
        media_type="text/event-stream",
    )

    await asyncio.wait_for(response.stream_response(send), timeout=0.1)

    assert body_attempts == body_cancellations == 2
    assert iterator.closed.is_set()
    assert store.calls == [(run_id, "owner-a", 0, 256)]
