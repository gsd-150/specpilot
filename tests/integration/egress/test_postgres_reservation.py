from __future__ import annotations

import pytest

from specpilot.contracts.egress import EgressStage
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.ledger import AttemptOutcome, ReservationState, TransmittedUsage
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_policy_projection import (
    NOW,
    FixtureTokenCounter,
    egress_request,
    fixture_enforcer,
    fixture_store,
    l1_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# Text that exists in the fixtures and must never reach a ledger column.
PLAINTEXT_MARKERS = ("bounded evidence", "What controls are required?", "xxx")


def ledger(dsn: str) -> PostgresEgressLedger:
    return PostgresEgressLedger(
        dsn,
        policy=EgressPolicy.load(),
        manifests=fixture_store(),
        clock=lambda: NOW,
    )


def reservation_for(*excerpts, stage: EgressStage = EgressStage.EVIDENCE):
    request = egress_request(payload=l1_payload(evidence_excerpts=excerpts))
    return fixture_enforcer().prepare(
        request.model_copy(update={"stage": stage}),
        FixtureTokenCounter(),
    )


async def dump_all_text(dsn: str) -> str:
    """Concatenate every text and jsonb value the ledger holds."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        rows = await (
            await connection.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name LIKE 'egress%'
                  AND data_type IN ('text', 'jsonb', 'character varying')
                """
            )
        ).fetchall()
        chunks: list[str] = []
        for table_name, column_name in rows:
            values = await (
                await connection.execute(
                    f'SELECT {column_name}::text FROM {table_name}'  # noqa: S608
                )
            ).fetchall()
            chunks.extend(str(value[0]) for value in values if value[0] is not None)
    return "\n".join(chunks)


async def test_first_reservation_persists_both_scopes(clean_ledger: str) -> None:
    reservation = await ledger(clean_ledger).check_and_reserve(
        reservation_for(distinct_excerpt(1)),
        FixtureTokenCounter(),
        idempotency_key="evidence-1",
    )

    assert reservation.state is ReservationState.RESERVED
    assert reservation.replayed is False
    assert reservation.usage.root_unique_tokens == 2
    assert reservation.usage.root_transmitted_tokens == 2
    assert len(reservation.corpus_usage.disclosure_ids) == 1
    assert reservation.corpus_usage.unique_tokens == 2


async def test_ledger_stores_no_query_claim_or_excerpt_text(
    clean_ledger: str,
) -> None:
    await ledger(clean_ledger).check_and_reserve(
        reservation_for(distinct_excerpt(1)),
        FixtureTokenCounter(),
        idempotency_key="evidence-1",
    )

    stored = await dump_all_text(clean_ledger)

    assert stored, "nothing was written, so this assertion would pass vacuously"
    for marker in PLAINTEXT_MARKERS:
        assert marker not in stored, f"ledger leaked payload text: {marker!r}"


async def test_replayed_key_returns_the_same_reservation_and_charges_once(
    clean_ledger: str,
) -> None:
    book = ledger(clean_ledger)
    request = reservation_for(distinct_excerpt(1))

    first = await book.check_and_reserve(
        request, FixtureTokenCounter(), idempotency_key="evidence-1"
    )
    replay = await book.check_and_reserve(
        request, FixtureTokenCounter(), idempotency_key="evidence-1"
    )

    assert replay.reservation_id == first.reservation_id
    assert replay.replayed is True
    assert replay.usage.root_transmitted_tokens == first.usage.root_transmitted_tokens
    assert replay.usage.root_transmitted_tokens == 2, (
        "a replayed reservation never reached a provider, so it must not be "
        "charged transmitted usage a second time"
    )


async def test_a_different_key_is_a_genuine_resend_and_is_charged_again(
    clean_ledger: str,
) -> None:
    book = ledger(clean_ledger)
    request = reservation_for(distinct_excerpt(1))

    await book.check_and_reserve(
        request, FixtureTokenCounter(), idempotency_key="evidence-1"
    )
    retry = await book.check_and_reserve(
        request, FixtureTokenCounter(), idempotency_key="evidence-1-retry"
    )

    assert retry.replayed is False
    assert retry.usage.root_transmitted_tokens == 4
    assert len(retry.usage.disclosures) == 1, "one excerpt is still one disclosure"
    assert len(retry.corpus_usage.disclosure_ids) == 1


async def test_a_cap_violation_reserves_nothing_and_leaves_no_row(
    clean_ledger: str,
) -> None:
    book = ledger(clean_ledger)
    quote = sized_quote(tokens=512, byte_count=8192)
    for index in range(5):
        await book.check_and_reserve(
            reservation_for(distinct_excerpt(index + 1, quote)),
            FixtureTokenCounter(),
            idempotency_key=f"evidence-{index}",
        )

    with pytest.raises(EgressPolicyViolation) as caught:
        await book.check_and_reserve(
            reservation_for(distinct_excerpt(99, quote)),
            FixtureTokenCounter(),
            idempotency_key="evidence-over",
        )

    assert caught.value.code == "online_unique_excerpts_exceeded"
    import psycopg

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        count = await (
            await connection.execute(
                "SELECT count(*) FROM egress_reservation WHERE idempotency_key = %s",
                ("evidence-over",),
            )
        ).fetchone()
    assert count is not None and count[0] == 0


async def test_attempts_are_recorded_against_a_reservation(
    clean_ledger: str,
) -> None:
    book = ledger(clean_ledger)
    reservation = await book.check_and_reserve(
        reservation_for(distinct_excerpt(1)),
        FixtureTokenCounter(),
        idempotency_key="evidence-1",
    )

    attempt = await book.record_attempt(
        reservation.reservation_id,
        reservation.route,
        TransmittedUsage(transmitted_tokens=2, transmitted_bytes=16),
        AttemptOutcome.SUCCEEDED,
        duration_ms=12,
    )

    assert attempt.reservation_id == reservation.reservation_id
    assert attempt.outcome is AttemptOutcome.SUCCEEDED
    assert attempt.duration_ms == 12
