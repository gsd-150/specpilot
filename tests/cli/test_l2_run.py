"""CLI coverage for the author-run single-case L2 execution command.

Every test drives the full L2 chain against the fixture corpus and the
``FakeProvider``; no test sends a live provider call or touches a real ledger.
The provider, policy, and ledger are injected by monkeypatch so the handler's
own construction — the enforcer and the ``PolicyBoundTransport`` — stays the
production one under test.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import specpilot.cli as cli
from specpilot.contracts.corpus_manifest import Bm25Binding, ParseQaEvidence
from specpilot.contracts.egress import CorpusUsage, UsageSnapshot
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    ProviderUse,
    RfcSourceManifestDraft,
)
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.dense_inventory import derived_corpus_sha256
from specpilot.egress.ledger import (
    Attempt,
    AttemptOutcome,
    RequestSize,
    Reservation,
    ReservationState,
)
from specpilot.egress.policy import EgressPolicy
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore
from specpilot.providers.fake import FakeProvider
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.local import LocalCorpus
from tests.helpers import rfc_factory
from tests.helpers.corpus_manifest_factory import corpus_draft
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML
from tests.unit.manifests.test_source_manifest import assessment

DOCUMENT_ID = "ietf-rfc-9999"
DOCUMENT_VERSION = "2026-08"
PROVIDER_ID = "deepseek"
MODEL_ID = "deepseek-v4-flash"
QUESTION = "A sender MUST record a retry attempt."
# Clause prose present in the fixture corpus; it must never appear in an artifact.
CLAUSE_MARKER = "First definition."


class StubLedger:
    """An in-memory egress ledger that grants every reservation it is asked for."""

    def __init__(
        self,
        conninfo: str = "",
        *,
        policy: Any = None,
        manifests: Any = None,
        clock: Any = None,
    ) -> None:
        del conninfo, policy, manifests, clock
        self.reserved: list[object] = []
        self.attempts: list[Attempt] = []
        self.sealed: list[tuple[str, str, str]] = []

    async def check_and_reserve(
        self, request: Any, counter: Any, *, idempotency_key: str
    ) -> Reservation:
        del counter
        self.reserved.append(request)
        return Reservation(
            reservation_id=str(uuid4()),
            idempotency_key=idempotency_key,
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            policy_hash="a" * 64,
            corpus_manifest_id=request.version.corpus_manifest_id,
            route=request.route,
            state=ReservationState.RESERVED,
            usage=UsageSnapshot(
                evaluation_root_id=request.evaluation_root_id,
                task_level=request.task_level,
                policy_hash="a" * 64,
            ),
            corpus_usage=CorpusUsage(
                corpus_manifest_id=request.version.corpus_manifest_id,
                policy_hash="a" * 64,
            ),
        )

    async def record_attempt(
        self,
        reservation_id: str,
        route: Any,
        request_size: RequestSize,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> Attempt:
        attempt = Attempt(
            attempt_id=f"att-{len(self.attempts) + 1}",
            reservation_id=reservation_id,
            route=route,
            outcome=outcome,
            request_size=request_size,
            duration_ms=duration_ms,
            public_error_code=public_error_code,
        )
        self.attempts.append(attempt)
        return attempt

    async def seal_run(
        self, evaluation_root_id: str, run_id: str, reason: str
    ) -> None:
        self.sealed.append((evaluation_root_id, run_id, reason))


def _build_workspace(tmp_path: Path) -> dict[str, str]:
    xml_path = rfc_factory.write(tmp_path, "l2.xml", TOOL_RFC_XML)
    verified = load_verified_rfc(xml_path, RfcLimits())
    corpus = LocalCorpus.load(((verified, ClauseLimits()),), RfcLimits())

    source_dir = tmp_path / "source-manifests"
    source_store = ManifestStore(source_dir)
    initial = source_store.create_source_v2(
        RfcSourceManifestDraft(
            document_id=DOCUMENT_ID,
            document_version=DOCUMENT_VERSION,
            text_url="https://example.test/rfc9999.txt",
            xml_url="https://example.test/rfc9999.xml",
            text_sha256="f" * 64,
            xml_sha256=hashlib.sha256(xml_path.read_bytes()).hexdigest(),
            downloaded_at=datetime(2026, 8, 6, tzinfo=UTC),
            created_at=datetime(2026, 8, 6, 1, tzinfo=UTC),
        )
    )
    route = ProviderRouteBinding(
        provider_id=PROVIDER_ID,
        endpoint_purpose="l2-author-fixture",
        use=ProviderUse.ONLINE_MAIN,
    )
    source = source_store.create_successor_v2(
        initial,
        assessment=assessment(
            provider_id=route.provider_id,
            endpoint_purpose=route.endpoint_purpose,
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        ),
        route_binding=route,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )

    bm25 = Bm25Index.build(corpus.indexable())
    corpus_dir = tmp_path / "corpus-manifests"
    corpus_store = CorpusManifestStore(corpus_dir)
    draft = corpus_draft(
        source_manifest_ids=(source.manifest_id,),
        bm25=Bm25Binding(
            tokenizer_version=bm25.tokenizer_version,
            k1=bm25.parameters.k1,
            b=bm25.parameters.b,
            index_fingerprint=bm25.fingerprint,
        ),
        point_count=corpus.unit_count(),
        derived_corpus_sha256=derived_corpus_sha256(corpus.units()),
        parse_qa=(
            ParseQaEvidence(
                source_manifest_id=source.manifest_id,
                evidence_sha256="1" * 64,
            ),
        ),
    )
    with corpus_store.acquire_freeze_lease(draft.collection_name) as lease:
        corpus_manifest = corpus_store.create(draft, lease=lease)
    return {
        "xml": str(xml_path),
        "source_manifest_id": source.manifest_id,
        "corpus_manifest_id": corpus_manifest.manifest_id,
        "source_dir": str(source_dir),
        "corpus_dir": str(corpus_dir),
    }


def _patch_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    # Computed once before the monkeypatch so `load_fixture` uses the real load.
    fixture = EgressPolicy.load_fixture()
    monkeypatch.setattr(EgressPolicy, "load", staticmethod(lambda: fixture))


def _patch_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specpilot.egress.postgres.PostgresEgressLedger",
        lambda *args, **kwargs: StubLedger(),
    )


def _patch_provider(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_with: str | None = None,
    reply: str | None = None,
) -> FakeProvider:
    provider = FakeProvider(
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        fail_with=fail_with,
        reply=reply,
    )
    monkeypatch.setattr(
        "specpilot.providers.http.resolve_credential",
        lambda endpoint: "fixture-key",
    )
    monkeypatch.setattr(
        "specpilot.providers.http.HttpChatAdapter",
        lambda endpoint, *, api_key: provider,
    )
    return provider


def _argv(workspace: dict[str, str], tmp_path: Path, **overrides: str) -> list[str]:
    values: dict[str, str] = {
        "question": QUESTION,
        "case_id": "l2-dev-001",
        "corpus_manifest": workspace["corpus_manifest_id"],
        "corpus_manifest_dir": workspace["corpus_dir"],
        "manifest_dir": workspace["source_dir"],
        "manifest": workspace["source_manifest_id"],
        "xml": workspace["xml"],
        "source_manifest": workspace["source_manifest_id"],
        "model_dir": str(tmp_path / "models"),
        "device": "cpu",
        "qdrant_url": "http://127.0.0.1:6333",
        "ledger_dsn": "postgresql://unused",
        "route": "main",
        "evaluation_root_id": "l2-author-root",
        "run_id": str(uuid4()),
        "out_dir": str(tmp_path / "artifacts" / "restricted" / "l2-dev" / "outcomes"),
    }
    values.update(overrides)
    return [
        "l2",
        "run",
        "--question", values["question"],
        "--case-id", values["case_id"],
        "--corpus-manifest", values["corpus_manifest"],
        "--corpus-manifest-dir", values["corpus_manifest_dir"],
        "--manifest-dir", values["manifest_dir"],
        "--manifest", values["manifest"],
        "--xml", values["xml"],
        "--source-manifest", values["source_manifest"],
        "--model-dir", values["model_dir"],
        "--device", values["device"],
        "--qdrant-url", values["qdrant_url"],
        "--ledger-dsn", values["ledger_dsn"],
        "--route", values["route"],
        "--evaluation-root-id", values["evaluation_root_id"],
        "--run-id", values["run_id"],
        "--out-dir", values["out_dir"],
    ]


def test_l2_run_happy_path_writes_a_valid_outcome_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _build_workspace(tmp_path)
    _patch_policy(monkeypatch)
    _patch_ledger(monkeypatch)
    _patch_provider(monkeypatch)
    argv = _argv(workspace, tmp_path)
    out_dir = Path(argv[-1])

    code = cli.main(argv)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    emitted = json.loads(captured.out)
    assert emitted["status"] == "completed"
    assert emitted["case_id"] == "l2-dev-001"
    assert emitted["verdicts"] == ["compliant"]

    outcome_path = Path(emitted["outcome_path"])
    assert outcome_path == out_dir / "l2-dev-001.json"
    assert stat.S_IMODE(os.stat(out_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(outcome_path).st_mode) == 0o600
    artifact = json.loads(outcome_path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "l2-outcome/v1"
    assert artifact["case_id"] == "l2-dev-001"
    assert artifact["design_description"] == QUESTION
    assert len(artifact["candidates"]) == 1
    assert artifact["candidates"][0]["claim"] == (
        "The design satisfies the cited requirement."
    )
    assert artifact["candidates"][0]["proposed_verdict"] == "compliant"
    assert artifact["results"][0]["verdict"] == "compliant"
    assert artifact["results"][0]["citation_count"] == 1
    assert artifact["provider_error"] is None
    assert artifact["parse_fault"] is None
    assert CLAUSE_MARKER not in outcome_path.read_text(encoding="utf-8")

    pre_verifier_path = out_dir / "l2-dev-001.pre-verifier.json"
    assert pre_verifier_path.exists()
    assert stat.S_IMODE(os.stat(pre_verifier_path).st_mode) == 0o600
    pre_verifier = json.loads(pre_verifier_path.read_text(encoding="utf-8"))
    assert pre_verifier["schema_version"] == "l2-pre-verifier/v1"
    assert pre_verifier["case_id"] == "l2-dev-001"
    assert len(pre_verifier["candidates"]) == 1
    assert pre_verifier["candidates"][0]["deterministic_passed"] is True
    assert pre_verifier["candidates"][0]["proposed_verdict"] == "compliant"
    # The hash covers the proposal: rereading the file and rehashing the
    # candidate block must reproduce the recorded identity.
    from specpilot.verifier.gate_only import (
        PreVerifierCandidate,
        pre_verifier_artifact_hash,
    )

    candidate = pre_verifier["candidates"][0]
    assert pre_verifier_artifact_hash(
        [
            PreVerifierCandidate(
                claim_id=candidate["claim_id"],
                claim=candidate["claim"],
                proposed_verdict=candidate["proposed_verdict"],
                evidence_ids=tuple(candidate["evidence_ids"]),
                rationale=candidate["rationale"],
            )
        ]
    ) == pre_verifier["artifact_hash"]
    assert CLAUSE_MARKER not in pre_verifier_path.read_text(encoding="utf-8")


def test_l2_run_missing_credential_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _build_workspace(tmp_path)
    _patch_policy(monkeypatch)
    _patch_ledger(monkeypatch)
    monkeypatch.delenv("SPECPILOT_MAIN_API_KEY", raising=False)

    code = cli.main(_argv(workspace, tmp_path))

    captured = capsys.readouterr()
    assert code == cli.EXIT_USAGE
    assert captured.out == ""
    assert captured.err.strip() == "provider_credential_missing"


def test_l2_run_missing_source_manifest_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _build_workspace(tmp_path)
    _patch_policy(monkeypatch)
    _patch_ledger(monkeypatch)
    _patch_provider(monkeypatch)

    code = cli.main(_argv(workspace, tmp_path, source_manifest="f" * 64))

    captured = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert captured.out == ""
    assert captured.err.strip() == "manifest_not_found"


def test_l2_run_missing_ledger_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _build_workspace(tmp_path)
    _patch_policy(monkeypatch)
    _patch_provider(monkeypatch)
    # No ledger monkeypatch: the real ledger tries the unreachable DSN and
    # refuses before any send, which is exactly the fail-closed path.
    argv = _argv(workspace, tmp_path, ledger_dsn="postgresql://127.0.0.1:9/db")

    code = cli.main(argv)

    captured = capsys.readouterr()
    assert code == cli.EXIT_IO
    assert captured.out == ""
    assert captured.err.strip() == "blocked:ledger_unavailable"


def test_l2_run_provider_error_still_writes_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _build_workspace(tmp_path)
    _patch_policy(monkeypatch)
    _patch_ledger(monkeypatch)
    _patch_provider(monkeypatch, fail_with="provider_timeout")
    argv = _argv(workspace, tmp_path)
    out_dir = Path(argv[-1])

    code = cli.main(argv)

    captured = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert captured.err.strip() == "failed:provider_timeout"
    emitted = json.loads(captured.out)
    assert emitted["status"] == "refused"
    assert emitted["verdicts"] == []

    artifact = json.loads(
        Path(emitted["outcome_path"]).read_text(encoding="utf-8")
    )
    assert artifact["schema_version"] == "l2-outcome/v1"
    assert artifact["candidates"] == []
    assert artifact["results"] == []
    assert artifact["provider_error"] == "provider_timeout"
    assert artifact["parse_fault"] is None
    assert stat.S_IMODE(os.stat(out_dir).st_mode) == 0o700


def test_l2_run_parse_fault_sets_parse_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _build_workspace(tmp_path)
    _patch_policy(monkeypatch)
    _patch_ledger(monkeypatch)
    _patch_provider(monkeypatch, reply="not a plan")

    code = cli.main(_argv(workspace, tmp_path))

    captured = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert captured.err.strip() == "invalid_tool_plan"
    emitted = json.loads(captured.out)
    assert emitted["status"] == "refused"
    artifact = json.loads(
        Path(emitted["outcome_path"]).read_text(encoding="utf-8")
    )
    assert artifact["candidates"] == []
    assert artifact["results"] == []
    assert artifact["provider_error"] is None
    assert artifact["parse_fault"] == "invalid_tool_plan"
