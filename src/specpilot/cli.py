from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from specpilot.contracts.archive import ArchivePolicy, UnsafeArchiveError
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    EvidenceExcerpt,
    JudgePayload,
    L1OnlinePayload,
    L2AtomicClaimPayload,
    NormalizedExcerptSpan,
    ReservationOutcome,
    ScoringPoint,
    TaskLevel,
    TocNode,
    VersionMetadata,
)
from specpilot.contracts.manifests import (
    ComplianceAssessment,
    ProviderRouteBinding,
    ProviderUse,
    SourceManifestDraft,
)
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.policy import EgressPolicy
from specpilot.ingestion.archive import extract_expected_docx
from specpilot.ingestion.ooxml import OoxmlLimits, UnsafeOoxmlError, inspect_docx
from specpilot.manifests.store import ManifestStore

# Exit codes, matching the ingestion worker: 2 is a refused input or policy
# violation, 3 is an I/O fault, 4 is bad usage. Every non-zero exit prints one
# stable code to stderr and nothing else.
EXIT_REFUSED = 2
EXIT_IO = 3
EXIT_USAGE = 4

_FIXTURE_CORPUS_ID = "c" * 64


def _emit(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


def _refuse(code: str, exit_code: int = EXIT_REFUSED) -> int:
    """Print one machine-readable code. Never a path, message, or payload."""
    print(code, file=sys.stderr)
    return exit_code


def _archive_inspect(arguments: argparse.Namespace) -> int:
    policy = ArchivePolicy(
        expected_docx_name=arguments.expect_docx,
        max_members=arguments.max_members,
        max_member_bytes=arguments.max_member_bytes,
        max_total_bytes=arguments.max_total_bytes,
    )
    try:
        extraction = extract_expected_docx(
            arguments.archive,
            arguments.destination,
            arguments.quarantine,
            policy,
        )
    except UnsafeArchiveError as error:
        return _refuse(error.code.value)
    except OSError:
        return _refuse("io_error", EXIT_IO)

    try:
        inspection = inspect_docx(
            arguments.destination / arguments.expect_docx,
            OoxmlLimits(),
        )
    except UnsafeOoxmlError as error:
        return _refuse(error.code.value)
    except OSError:
        return _refuse("io_error", EXIT_IO)

    return _emit(
        {
            "status": "accepted",
            "archive_sha256": extraction.archive_sha256,
            "docx_sha256": extraction.docx_sha256,
            "byte_count": extraction.byte_count,
            "member_count": inspection.member_count,
            "relationship_count": inspection.relationship_count,
        }
    )


def _manifest_create(arguments: argparse.Namespace) -> int:
    try:
        draft = SourceManifestDraft(
            document_id=arguments.document_id,
            document_version=arguments.document_version,
            download_url=arguments.download_url,
            archive_sha256=arguments.archive_sha256,
            docx_sha256=arguments.docx_sha256,
            downloaded_at=arguments.downloaded_at,
            created_at=arguments.created_at,
        )
    except ValidationError:
        return _refuse("invalid_manifest_fields", EXIT_USAGE)
    try:
        manifest = ManifestStore(arguments.manifest_dir).create_source(draft)
    except (OSError, ValueError, RuntimeError):
        return _refuse("manifest_not_written", EXIT_IO)
    return _emit(
        {
            "status": "created",
            "manifest_id": manifest.manifest_id,
            "cloud_egress_authorized": manifest.cloud_egress_authorized,
            "predecessor_manifest_id": manifest.predecessor_manifest_id,
        }
    )


def _manifest_authorize(arguments: argparse.Namespace) -> int:
    """Turn a completed self-assessment into a route-bound successor manifest.

    Every judgement here is the author's, recorded in the assessment file. This
    command only checks that the evidence is complete and internally consistent
    and that its conclusion matches the route being bound; it never infers,
    supplies, or upgrades a conclusion.
    """
    store = ManifestStore(arguments.manifest_dir)
    try:
        predecessor = store.read_source(arguments.predecessor)
    except (OSError, ValueError, RuntimeError):
        return _refuse("manifest_not_found")

    try:
        assessment = ComplianceAssessment.model_validate_json(
            arguments.assessment.read_text(encoding="utf-8")
        )
    except (ValidationError, OSError, ValueError):
        return _refuse("invalid_authorization_evidence")

    binding = ProviderRouteBinding(
        provider_id=arguments.provider_id,
        endpoint_purpose=arguments.endpoint_purpose,
        use=ProviderUse(arguments.use),
    )
    try:
        successor = store.create_successor(
            predecessor,
            assessment=assessment,
            route_binding=binding,
            created_at=arguments.created_at,
        )
    except (ValidationError, ValueError):
        return _refuse("invalid_authorization_evidence")
    except (OSError, RuntimeError):
        return _refuse("manifest_not_written", EXIT_IO)

    return _emit(
        {
            "status": "authorized",
            "manifest_id": successor.manifest_id,
            "predecessor_manifest_id": predecessor.manifest_id,
            "cloud_egress_authorized": successor.cloud_egress_authorized,
            "provider_id": binding.provider_id,
            "endpoint_purpose": binding.endpoint_purpose,
            "use": binding.use.value,
        }
    )


class _FixtureCounter:
    provider_id = "fixture-provider"
    model_id = "fixture-model-v1"

    def count_tokens(self, text: str) -> int:
        return max(len(text.split()), 1)


def _fixture_excerpt(index: int, tokens: int, byte_count: int) -> EvidenceExcerpt:
    import hashlib

    filler = byte_count - (tokens - 1)
    quote = " ".join(["x" * (filler - (tokens - 1)), *("x" for _ in range(tokens - 1))])
    return EvidenceExcerpt(
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        content_hash=f"{index % 16:x}" * 64,
        quote=quote,
        quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        span=NormalizedExcerptSpan(
            paragraph_start=index,
            paragraph_end=index,
            token_start=index * 600,
            token_end=index * 600 + tokens,
        ),
    )


def _envelope_smoke(arguments: argparse.Namespace) -> int:
    """Exercise the maximum legal L2 envelope, then one step past each boundary.

    Synthetic text only. This proves the gate arithmetic, and deliberately
    reports no quality figure of any kind.
    """
    del arguments
    from specpilot.egress.policy import EgressPolicy as _Policy

    policy = _Policy.load()
    store, manifest = _fixture_manifest()
    enforcer = EgressPolicyEnforcer(
        policy,
        manifests=store,
        clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
    )
    version = VersionMetadata(
        source_manifest_id=manifest.manifest_id,
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        document_id=manifest.document_id,
        document_version=manifest.document_version,
    )

    def reserve(payload: Any, stage: EgressStage) -> Any:
        return enforcer.prepare(
            EgressRequest(
                evaluation_root_id="envelope-smoke",
                run_id="run-1",
                task_level=TaskLevel.L2,
                version=version,
                stage=stage,
                route=manifest.provider_route_binding,
                model_id="fixture-model-v1",
                source_manifest=manifest,
                payload=payload,
            ),
            _FixtureCounter(),
        )

    def claim(index: int, excerpts: tuple[EvidenceExcerpt, ...], toc: int) -> Any:
        return L2AtomicClaimPayload(
            atomic_claim_id=f"claim-{index}",
            atomic_claim=f"Atomic claim {index}",
            version=version,
            toc_nodes=tuple(
                TocNode(node_id=f"claim-{index}-toc-{n}", title=f"Section {n}")
                for n in range(toc)
            ),
            evidence_excerpts=excerpts,
        )

    stages = (
        EgressStage.EVIDENCE,
        EgressStage.COMPLIANCE,
        EgressStage.VERIFIER,
        EgressStage.VERIFIER,
    )
    outcome: ReservationOutcome | None = None
    try:
        for claim_index in range(3):
            excerpts = tuple(
                _fixture_excerpt(claim_index * 4 + n + 1, 512, 8192) for n in range(4)
            )
            payload = claim(claim_index, excerpts, toc=2)
            for stage in stages:
                outcome = enforcer.apply_reservation(
                    outcome.usage if outcome else None,
                    outcome.corpus_usage if outcome else None,
                    reserve(payload, stage),
                    _FixtureCounter(),
                )
    except EgressPolicyViolation as error:
        return _refuse(f"envelope_rejected:{error.code}")

    judge_store, judge_manifest = _fixture_manifest(
        use=ProviderUse.OFFLINE_JUDGE,
        endpoint_purpose="fixture-judge",
    )
    judge_enforcer = EgressPolicyEnforcer(
        policy,
        manifests=judge_store,
        clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
    )
    judge_version = VersionMetadata(
        source_manifest_id=judge_manifest.manifest_id,
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        document_id=judge_manifest.document_id,
        document_version=judge_manifest.document_version,
    )
    judge_request = EgressRequest(
        evaluation_root_id="envelope-smoke",
        run_id="judge-run",
        task_level=TaskLevel.L2,
        version=judge_version,
        stage=EgressStage.JUDGE,
        route=judge_manifest.provider_route_binding,
        model_id="fixture-model-v1",
        source_manifest=judge_manifest,
        payload=JudgePayload(
            final_answer="Synthetic fixture answer.",
            scoring_points=(ScoringPoint(point_id="p1", text="Fixture point"),),
            gold_excerpts=tuple(_fixture_excerpt(100 + n, 512, 8192) for n in range(5)),
        ),
    )
    try:
        judge_reservation = judge_enforcer.prepare(judge_request, _FixtureCounter())
        for _ in range(2):
            outcome = judge_enforcer.apply_reservation(
                outcome.usage if outcome else None,
                outcome.corpus_usage if outcome else None,
                judge_reservation,
                _FixtureCounter(),
            )
    except EgressPolicyViolation as error:
        return _refuse(f"envelope_rejected:{error.code}")

    assert outcome is not None
    refusals: dict[str, str] = {}
    probes = (
        ("one_more_excerpt", claim(0, (_fixture_excerpt(90, 512, 8192),), 0)),
        ("one_more_toc_node", claim(0, (), 1)),
        (
            "one_more_token_in_an_excerpt",
            claim(0, (_fixture_excerpt(91, 513, 8194),), 0),
        ),
        (
            "one_more_byte_in_an_excerpt",
            claim(0, (_fixture_excerpt(92, 512, 8193),), 0),
        ),
    )
    for name, probe in probes:
        try:
            enforcer.apply_reservation(
                outcome.usage,
                outcome.corpus_usage,
                reserve(probe, EgressStage.EVIDENCE),
                _FixtureCounter(),
            )
        except EgressPolicyViolation as error:
            refusals[name] = error.code
        else:
            return _refuse(f"boundary_not_enforced:{name}")

    return _emit(
        {
            "status": "passed",
            "unique_disclosures": len(outcome.usage.disclosures),
            "root_transmitted_tokens": outcome.usage.root_transmitted_tokens,
            "root_transmitted_bytes": outcome.usage.root_transmitted_bytes,
            "corpus_unique_disclosures": len(outcome.corpus_usage.disclosure_ids),
            "refusals": refusals,
        }
    )


def _fixture_manifest(
    *,
    use: ProviderUse = ProviderUse.ONLINE_MAIN,
    endpoint_purpose: str = "fixture-smoke",
) -> tuple[ManifestStore, Any]:
    """An authorized manifest bound to one synthetic route, for smoke runs."""
    import hashlib
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="specpilot-smoke-manifests-")).resolve()
    store = ManifestStore(directory)
    premise = "Only bounded evidence excerpts may leave the local trust boundary."
    initial = store.create_source(
        SourceManifestDraft.model_validate(
            {
                "document_id": "synthetic-fixture-spec",
                "document_version": "fixture-edition",
                "download_url": "https://fixtures.invalid/synthetic.zip",
                "archive_sha256": "1" * 64,
                "docx_sha256": "2" * 64,
                "downloaded_at": "2026-08-06T01:00:00Z",
                "created_at": "2026-08-06T01:31:00Z",
            }
        )
    )
    binding = ProviderRouteBinding(
        provider_id="fixture-provider",
        endpoint_purpose=endpoint_purpose,
        use=use,
    )
    assessment = ComplianceAssessment.model_validate(
        {
            "source_terms": {
                "terms_snapshot": {
                    "snapshot_url": "https://fixtures.invalid/source-terms",
                    "snapshot_sha256": "3" * 64,
                    "captured_at": "2026-08-06T01:00:00Z",
                },
                "summary": "Synthetic fixture terms for smoke runs only.",
                "uncertainty": ("This corpus is generated, not licensed material.",),
            },
            "provider_policy": {
                "policy_snapshot": {
                    "snapshot_url": "https://fixtures.invalid/provider-policy",
                    "snapshot_sha256": "4" * 64,
                    "captured_at": "2026-08-06T01:00:00Z",
                },
                "retention_summary": "Fixture route retains nothing.",
                "training_summary": "Fixture route trains on nothing.",
                "region_summary": "Fixture route is local.",
                "subprocessor_summary": "Fixture route has no subprocessor.",
                "uncertainty": ("Applies to the fixture route only.",),
            },
            "outbound_limit": {
                "premise": premise,
                "premise_sha256": hashlib.sha256(premise.encode("utf-8")).hexdigest(),
            },
            "author_conclusion": {
                "authorized": True,
                "authorization_statement": (
                    "Synthetic fixture route: no real source text can reach it."
                ),
                "author_id": "fixture-smoke",
                "provider_id": binding.provider_id,
                "endpoint_purpose": binding.endpoint_purpose,
                "authored_at": "2026-08-06T02:00:00Z",
                "expires_at": "2036-08-06T02:00:00Z",
            },
        }
    )
    manifest = store.create_successor(
        initial,
        assessment=assessment,
        route_binding=binding,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )
    return store, manifest


def _route_smoke(arguments: argparse.Namespace) -> int:
    """Send synthetic fixture text through the real transport and ledger.

    This proves the send path is wired and policy-bound. It does not prove any
    real provider works: that needs credentials and a route, and their absence
    is reported as blocked rather than passed.
    """
    if arguments.ledger_dsn is None:
        return _refuse("ledger_not_configured")
    return asyncio.run(_route_smoke_async(arguments))


async def _route_smoke_async(arguments: argparse.Namespace) -> int:
    from specpilot.egress.ledger import LedgerError
    from specpilot.egress.postgres import PostgresEgressLedger
    from specpilot.providers.base import ProviderError
    from specpilot.providers.fake import FakeProvider
    from specpilot.providers.transport import PolicyBoundTransport

    # --route must actually change the route. A judge smoke that quietly
    # exercises the online chain is false evidence for the go/no-go checklist.
    judging = arguments.route == "judge"
    store, manifest = _fixture_manifest(
        use=ProviderUse.OFFLINE_JUDGE if judging else ProviderUse.ONLINE_MAIN,
        endpoint_purpose="fixture-judge" if judging else "fixture-smoke",
    )
    provider = FakeProvider(
        provider_id="fixture-provider",
        model_id="fixture-model-v1",
    )
    transport = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            EgressPolicy.load(),
            manifests=store,
            clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
        ),
        ledger=PostgresEgressLedger(
            arguments.ledger_dsn,
            policy=EgressPolicy.load(),
            manifests=store,
            clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
        ),
        adapters=(provider,),
    )
    version = VersionMetadata(
        source_manifest_id=manifest.manifest_id,
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        document_id=manifest.document_id,
        document_version=manifest.document_version,
    )
    payload: Any
    if judging:
        payload = JudgePayload(
            final_answer="Synthetic fixture answer.",
            scoring_points=(ScoringPoint(point_id="p1", text="Fixture point"),),
            gold_excerpts=(_fixture_excerpt(1, 4, 16),),
        )
    else:
        payload = L1OnlinePayload(
            query="Synthetic fixture probe.",
            version=version,
            evidence_excerpts=(_fixture_excerpt(1, 4, 16),),
        )
    request = EgressRequest(
        evaluation_root_id=f"route-smoke-{arguments.route}",
        run_id="run-1",
        task_level=TaskLevel.L1,
        version=version,
        stage=EgressStage.JUDGE if judging else EgressStage.EVIDENCE,
        route=manifest.provider_route_binding,
        model_id="fixture-model-v1",
        source_manifest=manifest,
        payload=payload,
    )
    try:
        response = await transport.send(request, idempotency_key="route-smoke-1")
    except EgressPolicyViolation as error:
        return _refuse(f"refused:{error.code}")
    except LedgerError as error:
        return _refuse(f"blocked:{error.code}")
    except ProviderError as error:
        return _refuse(f"blocked:{error.public_error_code}")

    return _emit(
        {
            "status": "passed",
            "route": arguments.route,
            "provider_use": manifest.provider_route_binding.use.value,
            "adapter": "fixture",
            "provider_id": response.provider_id,
            "model_id": response.model_id,
            "finish_reason": response.metadata.finish_reason,
            "proves": "transport, enforcer and ledger are wired and policy-bound",
            "does_not_prove": "any real provider route, credential, or model",
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="specpilot")
    commands = parser.add_subparsers(dest="group", required=True)

    archive = commands.add_parser("archive").add_subparsers(
        dest="command", required=True
    )
    inspect = archive.add_parser("inspect")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--destination", type=Path, required=True)
    inspect.add_argument("--quarantine", type=Path, required=True)
    inspect.add_argument("--expect-docx", required=True)
    inspect.add_argument("--max-members", type=int, default=8)
    inspect.add_argument("--max-member-bytes", type=int, default=256 * 1024 * 1024)
    inspect.add_argument("--max-total-bytes", type=int, default=512 * 1024 * 1024)
    inspect.set_defaults(handler=_archive_inspect)

    manifest = commands.add_parser("source-manifest").add_subparsers(
        dest="command", required=True
    )
    create = manifest.add_parser("create")
    create.add_argument("--manifest-dir", type=Path, required=True)
    for field in (
        "document-id",
        "document-version",
        "download-url",
        "archive-sha256",
        "docx-sha256",
        "downloaded-at",
        "created-at",
    ):
        create.add_argument(f"--{field}", required=True)
    create.set_defaults(handler=_manifest_create)

    authorize = manifest.add_parser("authorize-successor")
    authorize.add_argument("--manifest-dir", type=Path, required=True)
    authorize.add_argument("--predecessor", required=True)
    authorize.add_argument("--assessment", type=Path, required=True)
    authorize.add_argument("--provider-id", required=True)
    authorize.add_argument("--endpoint-purpose", required=True)
    authorize.add_argument(
        "--use",
        required=True,
        choices=[item.value for item in ProviderUse],
    )
    authorize.add_argument("--created-at", type=_aware_timestamp, required=True)
    authorize.set_defaults(handler=_manifest_authorize)

    egress = commands.add_parser("egress").add_subparsers(
        dest="command", required=True
    )
    smoke = egress.add_parser("envelope-smoke")
    smoke.set_defaults(handler=_envelope_smoke)

    provider = commands.add_parser("provider").add_subparsers(
        dest="command", required=True
    )
    route = provider.add_parser("route-smoke")
    route.add_argument("--fixture-only", action="store_true", required=True)
    route.add_argument("--route", choices=["main", "judge"], required=True)
    route.add_argument("--ledger-dsn", default=None)
    route.set_defaults(handler=_route_smoke)

    return parser


def _aware_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must carry an offset")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    handler: Any = arguments.handler
    result = handler(arguments)
    assert isinstance(result, int)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
