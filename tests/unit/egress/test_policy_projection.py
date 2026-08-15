from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    EvidenceExcerpt,
    JudgePayload,
    L1OnlinePayload,
    NormalizedExcerptSpan,
    ScoringPoint,
    TaskLevel,
    TocNode,
    VersionMetadata,
)
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    ProviderUse,
    SourceManifest,
    SourceManifestDraft,
)
from specpilot.egress.enforcer import (
    EgressPolicyEnforcer,
    EgressPolicyViolation,
    TokenAccountingUnavailable,
)
from specpilot.egress.policy import EgressPolicy
from specpilot.manifests.store import ManifestStore
from tests.unit.manifests.test_source_manifest import assessment, initial_fields

NOW = datetime(2026, 8, 6, 4, tzinfo=UTC)
CORPUS_MANIFEST_ID = "c" * 64
# Resolved because ManifestStore walks path components with O_NOFOLLOW and macOS
# hands out /var/folders/..., where /var is a symlink to /private/var.
_FIXTURE_MANIFEST_DIR = Path(
    tempfile.mkdtemp(prefix="specpilot-fixture-manifests-")
).resolve()


class FixtureTokenCounter:
    """Deterministic whitespace counter for policy fixtures only."""

    provider_id = "provider-a"
    model_id = "fixture-model-v1"

    def count_tokens(self, text: str) -> int:
        return len(text.split())


def online_route() -> ProviderRouteBinding:
    return ProviderRouteBinding(
        provider_id="provider-a",
        endpoint_purpose="evidence-review",
        use=ProviderUse.ONLINE_MAIN,
    )


def fixture_store() -> ManifestStore:
    """One create-only store shared by these fixtures.

    Manifests are content-addressed and the store has a byte-identical replay
    path, so a shared directory is safe and lets ``fixture_enforcer`` resolve
    every manifest the fixtures legitimately created.
    """
    return ManifestStore(_FIXTURE_MANIFEST_DIR)


FIXTURE_DOCUMENT = "iso-9001"
OTHER_FIXTURE_DOCUMENT = "iso-14001"


def fixture_policy(**document_caps: dict[str, int]) -> EgressPolicy:
    """The shipped policy, extended to price this suite's invented documents.

    ``corpus_document_unique`` fails closed on a document it does not price, so
    a fixture standard has to be priced somewhere. It is priced here rather than
    in ``default-v1.json`` on purpose: the shipped file lists the two frozen
    RFCs and the synthetic demo corpus, all of which really ship, and adding
    ``iso-9001`` to it would put test data in the artifact the container loads.
    """
    generous = {"excerpts": 1024, "tokens": 524288, "bytes": 8388608}
    caps: dict[str, object] = {
        FIXTURE_DOCUMENT: generous,
        OTHER_FIXTURE_DOCUMENT: generous,
    }
    fields = EgressPolicy.load().model_dump(mode="json")
    caps.update(fields["corpus_document_unique"])
    caps.update(document_caps)
    fields["corpus_document_unique"] = caps
    return EgressPolicy.model_validate(fields)


def test_fixture_policy_prices_only_the_committed_demo_document() -> None:
    default = EgressPolicy.load()
    fixture = EgressPolicy.load_fixture()

    assert "ietf-rfc-9999" not in default.corpus_document_unique
    assert set(fixture.corpus_document_unique) == {
        *default.corpus_document_unique,
        "ietf-rfc-9999",
    }
    cap = fixture.corpus_document_unique["ietf-rfc-9999"]
    assert (cap.excerpts, cap.tokens, cap.bytes) == (64, 524_288, 524_288)


def fixture_enforcer() -> EgressPolicyEnforcer:
    return EgressPolicyEnforcer(
        fixture_policy(), manifests=fixture_store(), clock=lambda: NOW
    )


def authorized_manifest(
    *,
    route: ProviderRouteBinding | None = None,
) -> SourceManifest:
    store = fixture_store()
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    binding = route or online_route()
    return store.create_successor(
        initial,
        assessment=assessment(
            provider_id=binding.provider_id,
            endpoint_purpose=binding.endpoint_purpose,
        ),
        route_binding=binding,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )


def version_metadata(
    *,
    source_manifest_id: str = "f" * 64,
    corpus_manifest_id: str = CORPUS_MANIFEST_ID,
) -> VersionMetadata:
    return VersionMetadata(
        source_manifest_id=source_manifest_id,
        corpus_manifest_id=corpus_manifest_id,
        document_id="iso-9001",
        document_version="2026-edition",
    )


def test_version_metadata_carries_source_and_corpus_identity() -> None:
    version = VersionMetadata(
        source_manifest_id="a" * 64,
        corpus_manifest_id="b" * 64,
        document_id="iso-9001",
        document_version="2026-edition",
    )

    assert version.source_manifest_id == "a" * 64
    assert version.corpus_manifest_id == "b" * 64


def test_planning_policy_allows_only_l1_plan_and_prices_its_query() -> None:
    policy = fixture_policy()

    snapshot = policy.snapshot(
        task_level=TaskLevel.L1.value,
        payload_kind="l1_plan",
        document_id=FIXTURE_DOCUMENT,
    )

    assert policy.stage_payload_allowlist[EgressStage.PLANNING.value] == ("l1_plan",)
    assert snapshot.projected_text_tokens == 1024


def excerpt(
    text: str = "bounded evidence",
    *,
    corpus_manifest_id: str = CORPUS_MANIFEST_ID,
) -> EvidenceExcerpt:
    quote_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return EvidenceExcerpt(
        corpus_manifest_id=corpus_manifest_id,
        content_hash="2" * 64,
        quote=text,
        quote_hash=quote_hash,
        span=NormalizedExcerptSpan(
            paragraph_start=4,
            paragraph_end=4,
            token_start=10,
            token_end=12,
        ),
    )


def l1_payload(**overrides: object) -> L1OnlinePayload:
    fields: dict[str, object] = {
        "kind": "l1_query",
        "query": "What controls are required?",
        "version": version_metadata(),
        "toc_nodes": (),
        "evidence_excerpts": (excerpt(),),
    }
    fields.update(overrides)
    return L1OnlinePayload(**fields)


def egress_request(
    *,
    payload: L1OnlinePayload | None = None,
    manifest: SourceManifest | None = None,
    route: ProviderRouteBinding | None = None,
) -> EgressRequest:
    binding = route or online_route()
    source = manifest or authorized_manifest(route=binding)
    request_version = version_metadata(source_manifest_id=source.manifest_id)
    selected_payload = payload or l1_payload()
    selected_payload = selected_payload.model_copy(update={"version": request_version})
    return EgressRequest(
        evaluation_root_id="case-1",
        run_id="run-1",
        task_level=TaskLevel.L1,
        version=request_version,
        stage="evidence",
        route=binding,
        model_id="fixture-model-v1",
        source_manifest=source,
        payload=selected_payload,
    )


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "full_clause",
        "retrieval_candidates",
        "full_toc",
        "stack_trace",
        "source_path",
        "secret",
        "arbitrary_extra",
    ],
)
def test_online_payload_cannot_encode_local_or_unlisted_fields(
    forbidden_field: str,
) -> None:
    fields = l1_payload().model_dump()
    fields[forbidden_field] = "must stay local"

    with pytest.raises(ValidationError) as caught:
        L1OnlinePayload.model_validate(fields)

    assert caught.value.errors()[0]["type"] == "extra_forbidden"


def test_online_payload_accepts_only_bounded_projection_fields() -> None:
    payload = l1_payload(
        toc_nodes=tuple(
            TocNode(node_id=f"n-{index}", title=f"Section {index}")
            for index in range(12)
        )
    )

    assert set(payload.model_dump()) == {
        "kind",
        "query",
        "version",
        "toc_nodes",
        "evidence_excerpts",
    }

    with pytest.raises(ValidationError):
        l1_payload(
            toc_nodes=tuple(
                TocNode(node_id=f"n-{index}", title=f"Section {index}")
                for index in range(13)
            )
        )


def test_evidence_excerpt_verifies_exact_quote_hash() -> None:
    with pytest.raises(ValidationError):
        EvidenceExcerpt(
            corpus_manifest_id="1" * 64,
            content_hash="2" * 64,
            quote="exact quote",
            quote_hash="3" * 64,
            span=NormalizedExcerptSpan(
                paragraph_start=0,
                paragraph_end=0,
                token_start=0,
                token_end=2,
            ),
        )


def test_judge_payload_exposes_only_answer_scoring_and_gold_excerpts() -> None:
    payload = JudgePayload(
        query="The question being scored.",
        final_answer="Final answer",
        scoring_points=(ScoringPoint(point_id="p1", text="Correctness"),),
        gold_excerpts=(excerpt(),),
    )

    assert set(payload.model_dump()) == {
        "kind",
        "query",
        "final_answer",
        "scoring_points",
        "gold_excerpts",
    }

    with pytest.raises(ValidationError) as caught:
        JudgePayload.model_validate(
            {
                **payload.model_dump(),
                "version": version_metadata().model_dump(),
            }
        )
    assert caught.value.errors()[0]["type"] == "extra_forbidden"


def test_prepare_requires_authorized_exact_route_and_counter() -> None:
    enforcer = fixture_enforcer()
    request = egress_request()

    reservation = enforcer.prepare(request, FixtureTokenCounter())

    assert reservation.projected_payload == request.payload
    assert len(reservation.disclosures) == 1
    assert reservation.disclosures[0].token_count == 2
    assert reservation.disclosures[0].byte_count == len(b"bounded evidence")

    with pytest.raises(TokenAccountingUnavailable) as missing:
        enforcer.prepare(request, None)  # type: ignore[arg-type]
    assert missing.value.code == "token_accounting_unavailable"

    wrong_route = ProviderRouteBinding(
        provider_id="provider-b",
        endpoint_purpose="evidence-review",
        use=ProviderUse.ONLINE_MAIN,
    )
    with pytest.raises(EgressPolicyViolation) as unauthorized:
        enforcer.prepare(
            request.model_copy(update={"route": wrong_route}),
            FixtureTokenCounter(),
        )
    assert unauthorized.value.code == "route_unauthorized"


@pytest.mark.parametrize("result", [0, -1, RuntimeError("counter failed")])
def test_prepare_fails_closed_when_counting_is_not_positive(
    result: int | RuntimeError,
) -> None:
    class BrokenCounter(FixtureTokenCounter):
        def count_tokens(self, text: str) -> int:
            if isinstance(result, RuntimeError):
                raise result
            return result

    with pytest.raises(TokenAccountingUnavailable) as caught:
        fixture_enforcer().prepare(egress_request(), BrokenCounter())

    assert caught.value.code == "token_accounting_unavailable"


def test_enforcer_uses_trusted_aware_clock_for_manifest_authorization() -> None:
    request = egress_request()
    expired = EgressPolicyEnforcer(
        fixture_policy(),
        manifests=fixture_store(),
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )

    with pytest.raises(EgressPolicyViolation) as denied:
        expired.prepare(request, FixtureTokenCounter())
    assert denied.value.code == "route_unauthorized"

    naive = EgressPolicyEnforcer(
        fixture_policy(),
        manifests=fixture_store(),
        clock=lambda: datetime(2026, 8, 6, 4),
    )
    with pytest.raises(EgressPolicyViolation) as invalid_clock:
        naive.prepare(request, FixtureTokenCounter())
    assert invalid_clock.value.code == "authorization_clock_invalid"

    fields = request.model_dump()
    fields["requested_at"] = NOW
    with pytest.raises(ValidationError) as untrusted_time:
        EgressRequest.model_validate(fields)
    assert untrusted_time.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("field", "invalid_value", "code"),
    [
        ("source_manifest_id", "0" * 64, "source_manifest_mismatch"),
        ("corpus_manifest_id", "d" * 64, "corpus_manifest_mismatch"),
        ("document_id", "other-document", "document_id_mismatch"),
        ("document_version", "other-version", "document_version_mismatch"),
    ],
)
def test_prepare_binds_all_version_and_corpus_facts_to_authorized_source(
    field: str,
    invalid_value: str,
    code: str,
) -> None:
    request = egress_request()
    changed_version = request.version.model_copy(update={field: invalid_value})
    changed_payload = request.payload.model_copy(update={"version": changed_version})
    changed = request.model_copy(
        update={"version": changed_version, "payload": changed_payload}
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        fixture_enforcer().prepare(changed, FixtureTokenCounter())

    assert caught.value.code == code


def test_prepare_rejects_payload_version_or_excerpt_corpus_disagreement() -> None:
    request = egress_request()
    payload_version = request.version.model_copy(
        update={"corpus_manifest_id": "d" * 64}
    )
    mismatched_payload = request.payload.model_copy(update={"version": payload_version})

    with pytest.raises(EgressPolicyViolation) as payload_error:
        fixture_enforcer().prepare(
            request.model_copy(update={"payload": mismatched_payload}),
            FixtureTokenCounter(),
        )
    assert payload_error.value.code == "payload_version_mismatch"

    mismatched_excerpt = excerpt(corpus_manifest_id="d" * 64)
    with pytest.raises(EgressPolicyViolation) as excerpt_error:
        fixture_enforcer().prepare(
            request.model_copy(
                update={
                    "payload": request.payload.model_copy(
                        update={"evidence_excerpts": (mismatched_excerpt,)}
                    )
                }
            ),
            FixtureTokenCounter(),
        )
    assert excerpt_error.value.code == "corpus_manifest_mismatch"
