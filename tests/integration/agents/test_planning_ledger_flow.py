from __future__ import annotations

import psycopg
import pytest

from specpilot.agents.planner import Planner, PlannerContext
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import PolicyBoundTransport
from tests.unit.egress.test_planning_projection import planning_request
from tests.unit.egress.test_policy_projection import (
    NOW,
    fixture_policy,
    fixture_store,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def scalar(dsn: str, query: str, params: tuple[object, ...] = ()) -> object:
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        row = await (await connection.execute(query, params)).fetchone()
    return None if row is None else row[0]


async def test_planning_records_one_attempt_and_no_source_disclosures(
    clean_ledger: str,
) -> None:
    template = planning_request(query="When may a sender retry?")
    provider = FakeProvider()
    transport = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
        ),
        ledger=PostgresEgressLedger(
            clean_ledger,
            policy=fixture_policy(),
            manifests=fixture_store(),
            clock=lambda: NOW,
        ),
        adapters=(provider,),
    )
    planner = Planner(transport)
    context = PlannerContext(
        source_manifest=template.source_manifest,
        corpus_manifest_id=template.version.corpus_manifest_id,
        evaluation_root_id=template.evaluation_root_id,
        run_id=template.run_id,
        model_id=template.model_id,
        idempotency_key="planning-1",
    )

    result = await planner.plan("When may a sender retry?", context)

    reservation_id = await scalar(
        clean_ledger, "SELECT reservation_id FROM egress_reservation"
    )
    assert result.plan.plan_id == "fixture-plan"
    assert result.reservation_id == str(reservation_id)
    assert provider.call_count == 1
    assert reservation_id is not None
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_reservation") == 1
    assert await scalar(clean_ledger, "SELECT count(*) FROM egress_attempt") == 1
    assert (
        await scalar(
            clean_ledger,
            "SELECT count(*) FROM egress_reservation_disclosure "
            "WHERE reservation_id = %s",
            (reservation_id,),
        )
        == 0
    )
