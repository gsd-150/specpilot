from __future__ import annotations

import pytest
from pydantic import ValidationError

from specpilot.runs.contracts import (
    RunEventPage,
    RunStatus,
    StateTransitionEvent,
    TerminalEvent,
)


def _transition(sequence: int) -> StateTransitionEvent:
    return StateTransitionEvent(
        sequence=sequence,
        previous_status=None,
        status=RunStatus.QUEUED,
        reason=None,
    )


def test_run_event_page_accepts_a_closed_sanitized_page() -> None:
    """Catches a page contract that cannot carry the persisted event union."""
    event = _transition(1)

    page = RunEventPage(events=(event,), terminal=False, last_sequence=1)

    assert page.events == (event,)
    assert page.terminal is False
    assert page.last_sequence == 1


@pytest.mark.parametrize("last_sequence", [-1, 10_001])
def test_run_event_page_rejects_out_of_range_last_sequence(
    last_sequence: int,
) -> None:
    """Catches accepting a cursor outside the durable sequence domain."""
    with pytest.raises(ValidationError, match="last_sequence"):
        RunEventPage(events=(), terminal=False, last_sequence=last_sequence)


def test_run_event_page_rejects_more_than_256_events() -> None:
    """Catches an unbounded incremental page reaching the streaming layer."""
    events = tuple(_transition(sequence) for sequence in range(1, 258))

    with pytest.raises(ValidationError, match="events"):
        RunEventPage(events=events, terminal=False, last_sequence=257)


def test_run_event_page_rejects_non_increasing_events() -> None:
    """Catches duplicate or reordered durable sequences inside one page."""
    with pytest.raises(ValidationError, match="strictly increasing"):
        RunEventPage(
            events=(_transition(2), _transition(1)),
            terminal=False,
            last_sequence=1,
        )


def test_run_event_page_rejects_final_event_cursor_mismatch() -> None:
    """Catches a cursor that would skip or replay the page's final event."""
    with pytest.raises(ValidationError, match="last event"):
        RunEventPage(
            events=(_transition(1),),
            terminal=False,
            last_sequence=2,
        )


def test_run_event_page_rejects_terminal_flag_without_terminal_event() -> None:
    """Catches closing a stream before a durable terminal event is delivered."""
    with pytest.raises(ValidationError, match="terminal event"):
        RunEventPage(
            events=(_transition(1),),
            terminal=True,
            last_sequence=1,
        )


def test_run_event_page_accepts_terminal_event_as_final_event() -> None:
    """Catches rejecting a page that closes on its durable terminal event."""
    event = TerminalEvent(
        sequence=3,
        status=RunStatus.FAILED,
        reason="provider_timeout",
    )

    page = RunEventPage(events=(event,), terminal=True, last_sequence=3)

    assert page.events == (event,)


def test_run_event_page_is_frozen_and_extra_forbidding() -> None:
    """Catches mutation or extension of the closed streaming boundary."""
    page = RunEventPage(events=(), terminal=False, last_sequence=0)

    with pytest.raises(ValidationError, match="frozen"):
        page.last_sequence = 1
    with pytest.raises(ValidationError, match="extra"):
        RunEventPage(
            events=(), terminal=False, last_sequence=0, secret="must-not-pass"
        )
