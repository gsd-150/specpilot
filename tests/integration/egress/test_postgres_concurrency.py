from __future__ import annotations

import asyncio

import pytest

from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import Reservation
from tests.integration.egress.test_postgres_reservation import ledger, reservation_for
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_policy_projection import FixtureTokenCounter

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

RACERS = 20


async def fill_to_one_slot_remaining(dsn: str, quote: str) -> None:
    """Consume four of the five L1 unique excerpt slots."""
    book = ledger(dsn)
    for index in range(4):
        await book.check_and_reserve(
            reservation_for(distinct_excerpt(index + 1, quote)),
            FixtureTokenCounter(),
            idempotency_key=f"seed-{index}",
        )


async def test_exactly_one_of_twenty_racers_takes_the_last_excerpt_slot(
    clean_ledger: str,
) -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    await fill_to_one_slot_remaining(clean_ledger, quote)

    async def racer(index: int) -> Reservation:
        # Each racer offers a different excerpt, so they compete for the slot
        # rather than deduplicating onto one another's disclosure.
        return await ledger(clean_ledger).check_and_reserve(
            reservation_for(distinct_excerpt(100 + index, quote)),
            FixtureTokenCounter(),
            idempotency_key=f"racer-{index}",
        )

    results = await asyncio.gather(
        *(racer(index) for index in range(RACERS)),
        return_exceptions=True,
    )

    winners = [item for item in results if isinstance(item, Reservation)]
    refusals = [item for item in results if isinstance(item, EgressPolicyViolation)]
    unexpected = [
        item
        for item in results
        if not isinstance(item, Reservation | EgressPolicyViolation)
    ]

    assert unexpected == [], f"racers failed for the wrong reason: {unexpected}"
    assert len(winners) == 1, f"{len(winners)} racers were granted the last slot"
    assert len(refusals) == RACERS - 1
    assert all(item.code.endswith("_excerpts_exceeded") for item in refusals)
    assert winners[0].usage.root_unique_tokens == 5 * 512


async def test_concurrent_replays_of_one_key_yield_one_reservation(
    clean_ledger: str,
) -> None:
    request = reservation_for(distinct_excerpt(1))

    async def replay() -> Reservation:
        return await ledger(clean_ledger).check_and_reserve(
            request, FixtureTokenCounter(), idempotency_key="shared-key"
        )

    results = await asyncio.gather(
        *(replay() for _ in range(RACERS)),
        return_exceptions=True,
    )

    granted = [item for item in results if isinstance(item, Reservation)]
    assert len(granted) == RACERS, [
        item for item in results if not isinstance(item, Reservation)
    ]
    assert len({item.reservation_id for item in granted}) == 1
    assert sum(1 for item in granted if not item.replayed) == 1, (
        "exactly one racer created the reservation; the rest must be replays"
    )

    final = max(item.usage.root_transmitted_tokens for item in granted)
    assert final == 2, "concurrent replays must charge transmitted usage once"


async def test_the_corpus_scope_is_serialized_across_separate_cases(
    clean_ledger: str,
) -> None:
    """Distinct cases still share one corpus ledger, so the race must be safe."""

    async def racer(index: int) -> Reservation:
        request = reservation_for(distinct_excerpt(200 + index))
        return await ledger(clean_ledger).check_and_reserve(
            request.model_copy(update={"evaluation_root_id": f"case-{index}"}),
            FixtureTokenCounter(),
            idempotency_key=f"case-{index}-evidence",
        )

    results = await asyncio.gather(
        *(racer(index) for index in range(RACERS)),
        return_exceptions=True,
    )

    granted = [item for item in results if isinstance(item, Reservation)]
    assert len(granted) == RACERS, [
        item for item in results if not isinstance(item, Reservation)
    ]
    final = max(len(item.corpus_usage.disclosure_ids) for item in granted)
    assert final == RACERS, (
        "every case disclosed a different excerpt, so the corpus ledger must "
        "have counted all of them without losing an update"
    )
