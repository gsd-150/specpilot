"""Bounded, resumable SSE framing for owner-scoped durable run events."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from uuid import UUID

from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from starlette.types import Send

from specpilot.api.dependencies import ApiRunStore
from specpilot.runs.contracts import RunEvent

_CANONICAL_CURSOR = re.compile(r"(?:0|[1-9][0-9]{0,4})\Z")
_MAX_SEQUENCE = 10_000
_HEARTBEAT = b": keep-alive\n\n"
_FINALIZE_MAX_SECONDS = 0.25
_monotonic = time.monotonic
_sleep = asyncio.sleep


@dataclass(frozen=True, slots=True)
class RunEventStreamConfig:
    """Finite polling and buffering limits for one SSE connection."""

    heartbeat_seconds: float = 10
    poll_seconds: float = 0.25
    max_connection_seconds: float = 60
    page_size: int = 256

    def __post_init__(self) -> None:
        intervals = (
            self.heartbeat_seconds,
            self.poll_seconds,
            self.max_connection_seconds,
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                for value in intervals
            )
            or isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 256
        ):
            raise ValueError("invalid_stream_config")

    @property
    def finalize_seconds(self) -> float:
        return min(self.max_connection_seconds / 2, _FINALIZE_MAX_SECONDS)


class BoundedStreamingResponse(StreamingResponse):
    """Streaming response whose deadline includes iterator and ASGI send waits."""

    def __init__(
        self,
        content: AsyncIterable[bytes],
        *,
        deadline: float,
        finalize_seconds: float,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
            or deadline <= _monotonic()
            or isinstance(finalize_seconds, bool)
            or not isinstance(finalize_seconds, (int, float))
            or not math.isfinite(finalize_seconds)
            or finalize_seconds <= 0
            or finalize_seconds >= deadline - _monotonic()
        ):
            raise ValueError("invalid_stream_config")
        super().__init__(
            content,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )
        self._deadline = deadline
        self._finalize_seconds = finalize_seconds

    async def stream_response(self, send: Send) -> None:
        """Close normally on expiry, including when client send backpressure stalls."""
        deadline = self._deadline
        stream_deadline = deadline - self._finalize_seconds
        iterator = self.body_iterator.__aiter__()
        started = False
        completed = False
        expired = False
        try:
            await _await_before(
                stream_deadline,
                lambda: send(
                    {
                        "type": "http.response.start",
                        "status": self.status_code,
                        "headers": self.raw_headers,
                    }
                ),
            )
            started = True
            while True:
                try:
                    chunk = await _await_before(
                        stream_deadline, lambda: anext(iterator)
                    )
                except StopAsyncIteration:
                    break
                if not isinstance(chunk, bytes | memoryview):
                    chunk = chunk.encode(self.charset)
                await _await_before(
                    stream_deadline,
                    partial(_send_body, send, chunk),
                )
            await _await_before(
                stream_deadline,
                partial(_send_final_body, send),
            )
            completed = True
        except TimeoutError:
            # The response has already started. Expiry is a sanitized body close,
            # including when the client cannot accept the current frame.
            expired = True
        finally:
            await _close_iterator_before(iterator, deadline)
        if expired and started and not completed:
            with suppress(TimeoutError, OSError):
                await _await_before(
                    deadline, partial(_send_final_body, send)
                )


async def _await_before[T](
    deadline: float, operation: Callable[[], Awaitable[T]]
) -> T:
    remaining = deadline - _monotonic()
    if remaining <= 0:
        raise TimeoutError
    async with asyncio.timeout(remaining):
        return await operation()


async def _send_body(send: Send, chunk: bytes | memoryview) -> None:
    await send(
        {"type": "http.response.body", "body": chunk, "more_body": True}
    )


async def _send_final_body(send: Send) -> None:
    await send(
        {"type": "http.response.body", "body": b"", "more_body": False}
    )


async def _close_iterator_before(iterator: object, deadline: float) -> None:
    close = getattr(iterator, "aclose", None)
    if close is not None:
        with suppress(Exception):
            await _await_before(deadline, close)


def encode_event(event: RunEvent) -> bytes:
    """Encode one validated durable event as a compact SSE frame."""
    return (
        f"id: {event.sequence}\nevent: {event.kind.value}\ndata: "
        f"{event.model_dump_json()}\n\n"
    ).encode()


def parse_last_event_id(value: str | None) -> int:
    """Parse a canonical durable sequence cursor, defaulting a new stream to zero."""
    if value is None:
        return 0
    if _CANONICAL_CURSOR.fullmatch(value) is None:
        raise ValueError("invalid_last_event_id")
    cursor = int(value)
    if cursor > _MAX_SEQUENCE:
        raise ValueError("invalid_last_event_id")
    return cursor


async def stream_owned_events(
    store: ApiRunStore,
    run_id: UUID,
    session_id: str,
    *,
    after_sequence: int,
    config: RunEventStreamConfig,
    deadline: float,
) -> AsyncIterator[bytes]:
    """Poll one owner-bound page at a time until terminal, failure, or deadline."""
    started_at = _monotonic()
    heartbeat_at = started_at + config.heartbeat_seconds
    cursor = after_sequence

    while _monotonic() < deadline:
        try:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                return
            async with asyncio.timeout(remaining):
                page = await store.read_events_owned(
                    run_id,
                    session_id,
                    after_sequence=cursor,
                    limit=config.page_size,
                )
            if page is None:
                return
            for event in page.events:
                if _monotonic() >= deadline:
                    return
                yield encode_event(event)
                heartbeat_at = _monotonic() + config.heartbeat_seconds
            cursor = page.last_sequence
        except Exception:
            # Response headers may already be sent. Close without serializing
            # dependency or validation details into the public event stream.
            return

        if page.terminal:
            return
        if page.events:
            continue

        now = _monotonic()
        if now >= deadline:
            return
        if now >= heartbeat_at:
            yield _HEARTBEAT
            heartbeat_at = _monotonic() + config.heartbeat_seconds
            now = _monotonic()

        remaining = deadline - now
        if remaining <= 0:
            return
        await _sleep(
            min(config.poll_seconds, heartbeat_at - now, remaining)
        )


__all__ = [
    "BoundedStreamingResponse",
    "RunEventStreamConfig",
    "connection_deadline",
    "encode_event",
    "parse_last_event_id",
    "remaining_connection_seconds",
    "stream_owned_events",
]


def connection_deadline(config: RunEventStreamConfig) -> float:
    """Establish the one absolute lifetime shared by preflight and body work."""
    return _monotonic() + config.max_connection_seconds


def remaining_connection_seconds(deadline: float) -> float:
    """Return the time still available on an established connection lifetime."""
    return deadline - _monotonic()
