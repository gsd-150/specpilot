from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest

from specpilot.contracts.archive import ArchivePolicy, UnsafeArchiveError
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    L1OnlinePayload,
    TaskLevel,
    VersionMetadata,
)
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from specpilot.ingestion.archive import extract_expected_docx
from specpilot.ingestion.ooxml import OoxmlLimits, inspect_docx
from specpilot.providers.fake import FakeProvider
from specpilot.providers.transport import PolicyBoundTransport
from tests.cli.conftest import FIXTURE_CONTENT_MARKERS, build_submission

pytestmark = [pytest.mark.fixture_smoke, pytest.mark.anyio]

# The whole point of the demo path is that it proves wiring, not quality. If any
# of these ever appear in smoke output, the demo has started making claims it
# cannot support.
QUALITY_WORDS = re.compile(
    r"\b(recall|precision|accuracy|f1|macro|kappa|score|correct|better|worse)\b",
    re.IGNORECASE,
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


def archive_policy() -> ArchivePolicy:
    return ArchivePolicy(
        expected_docx_name="expected.docx",
        max_members=8,
        max_member_bytes=8 * 1024 * 1024,
        max_total_bytes=16 * 1024 * 1024,
    )


def fixture_pipeline_manifest():
    """Reuse the CLI's synthetic authorized manifest for the demo route."""
    from specpilot.cli import _fixture_manifest

    return _fixture_manifest()


def l1_request(manifest, excerpt, *, root: str, stage=EgressStage.EVIDENCE):
    version = VersionMetadata(
        source_manifest_id=manifest.manifest_id,
        corpus_manifest_id="c" * 64,
        document_id=manifest.document_id,
        document_version=manifest.document_version,
    )
    return EgressRequest(
        evaluation_root_id=root,
        run_id="run-1",
        task_level=TaskLevel.L1,
        version=version,
        stage=stage,
        route=manifest.provider_route_binding,
        model_id="fixture-model-v1",
        source_manifest=manifest,
        payload=L1OnlinePayload(
            query="Synthetic fixture probe.",
            version=version,
            evidence_excerpts=(excerpt,),
        ),
    )


def demo_transport(dsn: str, store, provider: FakeProvider) -> PolicyBoundTransport:
    from datetime import UTC, datetime

    clock = datetime(2026, 8, 6, 4, tzinfo=UTC)
    return PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            EgressPolicy.load(), manifests=store, clock=lambda: clock
        ),
        ledger=PostgresEgressLedger(
            dsn,
            policy=EgressPolicy.load(),
            manifests=store,
            clock=lambda: clock,
        ),
        adapters=(provider,),
    )


def test_a_safe_fixture_passes_ingestion_and_inspection(tmp_path: Path) -> None:
    archive = build_submission(tmp_path)
    destination = tmp_path / "corpus" / "fixture"

    extraction = extract_expected_docx(
        archive, destination, tmp_path / "quarantine", archive_policy()
    )
    inspection = inspect_docx(destination / "expected.docx", OoxmlLimits())

    assert len(extraction.docx_sha256) == 64
    assert inspection.member_count >= 2
    assert inspection.external_relationships == ()


def test_a_malicious_fixture_is_quarantined_and_never_extracted(
    tmp_path: Path,
) -> None:
    archive = build_submission(tmp_path, member_name="payload.exe")
    destination = tmp_path / "corpus" / "fixture"

    with pytest.raises(UnsafeArchiveError):
        extract_expected_docx(
            archive, destination, tmp_path / "quarantine", archive_policy()
        )

    assert not destination.exists(), "a rejected archive left an extracted artefact"
    records = list((tmp_path / "quarantine").glob("*/record.json"))
    assert len(records) == 1
    for marker in FIXTURE_CONTENT_MARKERS:
        assert marker not in records[0].read_text(encoding="utf-8")


async def test_the_demo_route_reserves_sends_and_records_without_payload_text(
    clean_ledger: str,
) -> None:
    from specpilot.cli import _fixture_excerpt

    store, manifest = fixture_pipeline_manifest()
    provider = FakeProvider(
        provider_id="fixture-provider", model_id="fixture-model-v1"
    )
    transport = demo_transport(clean_ledger, store, provider)

    response = await transport.send(
        l1_request(manifest, _fixture_excerpt(1, 4, 16), root="smoke-case-1"),
        idempotency_key="smoke-1",
    )

    assert provider.call_count == 1
    assert response.content.startswith("fixture answer")

    with psycopg.connect(clean_ledger) as connection:
        reservations = connection.execute(
            "SELECT count(*) FROM egress_reservation"
        ).fetchone()
        attempts = connection.execute("SELECT count(*) FROM egress_attempt").fetchone()
        trace = connection.execute(
            "SELECT usage_snapshot::text FROM egress_evaluation_root"
        ).fetchone()
    assert reservations is not None and reservations[0] == 1
    assert attempts is not None and attempts[0] == 1
    assert trace is not None
    assert "Synthetic fixture probe" not in trace[0], (
        "the sanitized trace must carry hashes and counts, never payload text"
    )


async def test_a_policy_violation_produces_a_no_send_event(
    clean_ledger: str,
) -> None:
    """The Verifier-style refusal path: over the cap, nothing leaves."""
    from specpilot.cli import _fixture_excerpt

    store, manifest = fixture_pipeline_manifest()
    provider = FakeProvider(
        provider_id="fixture-provider", model_id="fixture-model-v1"
    )
    transport = demo_transport(clean_ledger, store, provider)

    with pytest.raises(EgressPolicyViolation) as caught:
        await transport.send(
            l1_request(
                manifest, _fixture_excerpt(2, 513, 8194), root="smoke-case-2"
            ),
            idempotency_key="smoke-2",
        )

    # Bytes, not tokens: every cap vector's `tokens` now equals its `bytes`, so
    # the byte check is what can fire first. This assertion still named the old
    # code two commits after that rename, because the test needs a DSN and was
    # skipped in every run that reported green.
    assert caught.value.code == "excerpt_bytes_exceeded"
    assert provider.call_count == 0, "a refusal that still called the provider"
    with psycopg.connect(clean_ledger) as connection:
        attempts = connection.execute("SELECT count(*) FROM egress_attempt").fetchone()
    assert attempts is not None and attempts[0] == 0


def test_the_smoke_path_reports_no_quality_metric(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from specpilot.cli import main

    code = main(["egress", "envelope-smoke"])
    captured = capsys.readouterr()

    assert code == 0
    assert not QUALITY_WORDS.search(captured.out)
    assert not QUALITY_WORDS.search(captured.err)
