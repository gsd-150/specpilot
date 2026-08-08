from __future__ import annotations

import pytest

from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import LedgerUnavailable
from specpilot.egress.postgres import PostgresEgressLedger
from tests.integration.egress.test_postgres_reservation import ledger, reservation_for
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_policy_projection import (
    NOW,
    FixtureTokenCounter,
    fixture_policy,
    fixture_store,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_a_new_ledger_instance_restores_every_total(clean_ledger: str) -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    first = ledger(clean_ledger)
    for index in range(3):
        await first.check_and_reserve(
            reservation_for(distinct_excerpt(index + 1, quote)),
            FixtureTokenCounter(),
            idempotency_key=f"evidence-{index}",
        )

    restarted = ledger(clean_ledger)
    resumed = await restarted.check_and_reserve(
        reservation_for(distinct_excerpt(4, quote)),
        FixtureTokenCounter(),
        idempotency_key="evidence-3",
    )

    assert resumed.usage.root_unique_tokens == 4 * 512
    assert resumed.usage.root_transmitted_tokens == 4 * 512
    assert len(resumed.corpus_usage.disclosure_ids) == 4


async def test_a_restarted_ledger_rejects_over_budget_continuation(
    clean_ledger: str,
) -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    first = ledger(clean_ledger)
    for index in range(5):
        await first.check_and_reserve(
            reservation_for(distinct_excerpt(index + 1, quote)),
            FixtureTokenCounter(),
            idempotency_key=f"evidence-{index}",
        )

    restarted = ledger(clean_ledger)
    with pytest.raises(EgressPolicyViolation) as caught:
        await restarted.check_and_reserve(
            reservation_for(distinct_excerpt(6, quote)),
            FixtureTokenCounter(),
            idempotency_key="evidence-after-restart",
        )

    assert caught.value.code == "online_unique_excerpts_exceeded"


async def test_an_unreachable_ledger_fails_closed_rather_than_allowing_a_send(
    clean_ledger: str,
) -> None:
    unreachable = PostgresEgressLedger(
        "postgresql://127.0.0.1:1/specpilot_does_not_exist?connect_timeout=1",
        policy=fixture_policy(),
        manifests=fixture_store(),
        clock=lambda: NOW,
    )

    with pytest.raises(LedgerUnavailable) as caught:
        await unreachable.check_and_reserve(
            reservation_for(distinct_excerpt(1)),
            FixtureTokenCounter(),
            idempotency_key="evidence-1",
        )

    assert caught.value.code == "ledger_unavailable"


async def test_a_policy_change_stops_an_evaluation_root_mid_flight(
    clean_ledger: str,
) -> None:
    """A different policy hash must not silently continue an existing budget."""
    await ledger(clean_ledger).check_and_reserve(
        reservation_for(distinct_excerpt(1)),
        FixtureTokenCounter(),
        idempotency_key="evidence-0",
    )

    changed = fixture_policy().model_copy(update={"toc_per_run": 25})
    with_other_policy = PostgresEgressLedger(
        clean_ledger,
        policy=changed,
        manifests=fixture_store(),
        clock=lambda: NOW,
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        await with_other_policy.check_and_reserve(
            reservation_for(distinct_excerpt(2)),
            FixtureTokenCounter(),
            idempotency_key="evidence-1",
        )

    assert caught.value.code == "policy_snapshot_mismatch"
