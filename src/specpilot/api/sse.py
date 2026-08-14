"""Bounded, resumable SSE framing for owner-scoped durable run events."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from specpilot.api.dependencies import ApiRunStore
from specpilot.runs.contracts import RunEvent

_CANONICAL_CURSOR = re.compile(r"(?:0|[1-9][0-9]{0,4})\Z")
_MAX_SEQUENCE = 10_000
_HEARTBEAT = b": keep-alive\n\n"
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
) -> AsyncIterator[bytes]:
    """Poll one owner-bound page at a time until terminal, failure, or deadline."""
    started_at = _monotonic()
    deadline = started_at + config.max_connection_seconds
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
    "RunEventStreamConfig",
    "encode_event",
    "parse_last_event_id",
    "stream_owned_events",
]
