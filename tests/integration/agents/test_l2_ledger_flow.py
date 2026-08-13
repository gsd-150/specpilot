from __future__ import annotations

import psycopg
import pytest

from specpilot.agents.compliance import ComplianceAgent, ComplianceContext
from specpilot.answer.evidence import build_evidence_from_unit
from specpilot.contracts.answer import Citation
from specpilot.contracts.verdict import IdentifiedCandidate
from specpilot.corpus.indexable import IndexUnit
from specpilot.egress.enforcer import EgressPolicyEnforcer
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import PolicyBoundTransport
from specpilot.verifier.deterministic import DeterministicCheck, DeterministicResult
from specpilot.verifier.semantic import SemanticContext, SemanticVerifier
from tests.unit.egress.test_policy_projection import (
    NOW,
    egress_request,
    fixture_policy,
    fixture_store,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def evidence():
    text = "A sender MUST perform the stated check."
    return build_evidence_from_unit(
        IndexUnit(
            unit_id="a" * 64,
            kind="clause",
            document_id="iso-9001",
            document_version="2026-edition",
            section_number="7.1",
            section_path="Requirements > 7.1",
            ordinal=1,
            text=text,
            indexed=text,
        ),
        corpus_manifest_id="c" * 64,
    )


def transport(dsn: str, provider: FakeProvider) -> PolicyBoundTransport:
    return PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
        ),
        ledger=PostgresEgressLedger(
            dsn, policy=fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
        ),
        adapters=(provider,),
    )


async def test_l2_compliance_and_verifier_share_one_root_with_separate_stages(
    clean_ledger: str,
) -> None:
    request = egress_request()
    disclosed = evidence()
    provider = FakeProvider()
    line = transport(clean_ledger, provider)
    compliance_context = ComplianceContext(
        source_manifest=request.source_manifest,
        corpus_manifest_id=request.version.corpus_manifest_id,
        evaluation_root_id="l2-case",
        run_id="l2-run",
        model_id=request.model_id,
        idempotency_key="initial",
        reconstruction_generation=0,
    )

    compliance = await ComplianceAgent(line).evaluate(
        "The design must follow the requirement.", (disclosed,), compliance_context
    )
    candidate: IdentifiedCandidate = compliance.candidates[0]
    deterministic = DeterministicResult(
        checks=(
            DeterministicCheck(
                evidence_id=disclosed.excerpt.content_hash,
                fault=None,
            ),
        ),
        citations=(
            Citation(
                clause_id=disclosed.disclosed.clause_id,
                corpus_manifest_id=disclosed.disclosed.corpus_manifest_id,
                document_id=disclosed.disclosed.document_id,
                document_version=disclosed.disclosed.document_version,
                section_number=disclosed.disclosed.section_number,
                content_hash=disclosed.excerpt.content_hash,
            ),
        ),
    )
    await SemanticVerifier(line).verify(
        candidate,
        (disclosed,),
        deterministic,
        SemanticContext(
            source_manifest=request.source_manifest,
            corpus_manifest_id=request.version.corpus_manifest_id,
            evaluation_root_id="l2-case",
            run_id="l2-run",
            model_id=request.model_id,
            idempotency_key="initial",
            reconstruction_generation=0,
        ),
    )

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        rows = await (
            await connection.execute(
                "SELECT stage, evaluation_root_id, run_id FROM egress_reservation "
                "ORDER BY created_at"
            )
        ).fetchall()
    compliance_reservation, verifier_reservation = rows
    assert compliance_reservation[0] == "compliance"
    assert verifier_reservation[0] == "verifier"
    assert compliance_reservation[1] == verifier_reservation[1] == "l2-case"
    assert compliance_reservation[2] == verifier_reservation[2] == "l2-run"
    assert len(provider.calls) == 2
