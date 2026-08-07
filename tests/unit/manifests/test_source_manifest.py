from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.contracts.manifests import (
    AuthorizationConclusion,
    ComplianceAssessment,
    EvidenceSnapshot,
    OutboundLimitAssessment,
    ProviderPolicyAssessment,
    ProviderRouteBinding,
    ProviderUse,
    SourceManifest,
    SourceManifestDraft,
    SourceTermsAssessment,
)
from specpilot.manifests.canonical import canonical_json, canonical_sha256
from specpilot.manifests.store import ManifestStore

_ARCHIVE_SHA256 = "a" * 64
_DOCX_SHA256 = "b" * 64


def initial_fields() -> dict[str, object]:
    return {
        "schema_version": "source-manifest/v1",
        "document_id": "iso-9001",
        "document_version": "2026-edition",
        "download_url": "https://EXAMPLE.com:443/standards/iso-9001.zip",
        "archive_sha256": _ARCHIVE_SHA256,
        "docx_sha256": _DOCX_SHA256,
        "downloaded_at": "2026-08-06T09:30:00+08:00",
        "created_at": "2026-08-06T01:31:00Z",
    }


def build_initial_source_manifest() -> SourceManifest:
    return SourceManifest.from_draft(SourceManifestDraft(**initial_fields()))


def test_initial_source_manifest_is_default_deny() -> None:
    manifest = build_initial_source_manifest()

    assert manifest.schema_version == "source-manifest/v1"
    assert manifest.cloud_egress_authorized is False
    assert manifest.predecessor_manifest_id is None
    assert manifest.compliance_assessment is None
    assert manifest.provider_route_binding is None


def test_manifest_id_is_content_addressed_and_input_order_independent() -> None:
    fields_in_order_a = initial_fields()
    fields_in_order_b = dict(reversed(tuple(fields_in_order_a.items())))

    first = canonical_sha256(SourceManifestDraft(**fields_in_order_a))
    second = canonical_sha256(SourceManifestDraft(**fields_in_order_b))

    assert first == second
    assert first == build_initial_source_manifest().manifest_id
    assert len(first) == 64


def test_source_manifest_v1_canonical_bytes_and_id_remain_unchanged() -> None:
    manifest = build_initial_source_manifest()

    assert manifest.manifest_id == (
        "df5c1ba5c1f6c90555adb9b190443553b6c877740d94168367fdfc100561fd37"
    )
    assert canonical_json(manifest, include_manifest_id=True) == (
        b'{"archive_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"cloud_egress_authorized":false,"compliance_assessment":null,'
        b'"created_at":"2026-08-06T01:31:00Z","document_id":"iso-9001",'
        b'"document_version":"2026-edition",'
        b'"docx_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        b'"download_url":"https://example.com/standards/iso-9001.zip",'
        b'"downloaded_at":"2026-08-06T01:30:00Z",'
        b'"manifest_id":"df5c1ba5c1f6c90555adb9b190443553b6c877740d94168367fdfc100561fd37",'
        b'"predecessor_manifest_id":null,"provider_route_binding":null,'
        b'"schema_version":"source-manifest/v1"}'
    )


def test_canonicalization_normalizes_urls_and_timestamps() -> None:
    first = SourceManifestDraft(**initial_fields())
    alternative_fields = initial_fields()
    alternative_fields["download_url"] = (
        "https://example.com/standards/iso-9001.zip"
    )
    alternative_fields["downloaded_at"] = datetime(
        2026,
        8,
        6,
        1,
        30,
        tzinfo=UTC,
    )
    second = SourceManifestDraft(**alternative_fields)

    assert first.downloaded_at.tzinfo is UTC
    assert canonical_sha256(first) == canonical_sha256(second)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("document_id", ""),
        ("document_version", "   "),
        ("download_url", "http://example.com/source.zip"),
        ("archive_sha256", "A" * 64),
        ("docx_sha256", "b" * 63),
        ("downloaded_at", "2026-08-06T01:30:00"),
        ("created_at", "2026-08-06T01:31:00"),
    ],
)
def test_source_identity_rejects_noncanonical_or_unsafe_values(
    field: str,
    invalid_value: object,
) -> None:
    fields = initial_fields()
    fields[field] = invalid_value

    with pytest.raises(ValidationError):
        SourceManifestDraft(**fields)


def test_initial_manifest_forbids_successor_fields_and_extra_fields() -> None:
    fields = initial_fields()
    fields["predecessor_manifest_id"] = "c" * 64

    with pytest.raises(ValidationError):
        SourceManifestDraft(**fields)

    with pytest.raises(ValidationError):
        SourceManifestDraft(**initial_fields(), unexpected="unsafe")


def test_manifest_models_are_frozen() -> None:
    manifest = build_initial_source_manifest()

    with pytest.raises(ValidationError):
        manifest.document_id = "changed"  # type: ignore[misc]


def test_manifest_rejects_an_id_not_derived_from_its_content() -> None:
    draft = SourceManifestDraft(**initial_fields())

    with pytest.raises(ValidationError):
        SourceManifest(manifest_id="f" * 64, **draft.model_dump())


def snapshot(name: str) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_url=f"https://evidence.example/{name}.html",
        snapshot_sha256="d" * 64,
        captured_at="2026-08-05T12:00:00Z",
    )


def assessment(
    *,
    authorized: bool = True,
    provider_id: str = "provider-a",
    endpoint_purpose: str = "evidence-review",
    expires_at: datetime | None = None,
) -> ComplianceAssessment:
    premise = "Only bounded evidence excerpts may leave the local trust boundary."
    return ComplianceAssessment(
        source_terms=SourceTermsAssessment(
            terms_snapshot=snapshot("source-terms"),
            summary="The source terms were reviewed for the stated use.",
            uncertainty=("The licensor may revise these terms.",),
        ),
        provider_policy=ProviderPolicyAssessment(
            policy_snapshot=snapshot("provider-policy"),
            retention_summary="Retention is limited according to the snapshot.",
            training_summary="Training treatment is recorded in the snapshot.",
            region_summary="Processing region remains provider-dependent.",
            subprocessor_summary="Subprocessors are listed in the snapshot.",
            uncertainty=("Provider policy may change after capture.",),
        ),
        outbound_limit=OutboundLimitAssessment(
            premise=premise,
            premise_sha256=hashlib.sha256(premise.encode("utf-8")).hexdigest(),
        ),
        author_conclusion=AuthorizationConclusion(
            authorized=authorized,
            authorization_statement=(
                "I authorize this exact provider endpoint-purpose route."
            ),
            author_id="compliance-author-1",
            provider_id=provider_id,
            endpoint_purpose=endpoint_purpose,
            authored_at="2026-08-06T02:00:00Z",
            expires_at=expires_at or datetime(2026, 8, 8, tzinfo=UTC),
        ),
    )


def route(
    *,
    provider_id: str = "provider-a",
    endpoint_purpose: str = "evidence-review",
    use: ProviderUse = ProviderUse.ONLINE_MAIN,
) -> ProviderRouteBinding:
    return ProviderRouteBinding(
        provider_id=provider_id,
        endpoint_purpose=endpoint_purpose,
        use=use,
    )


def test_authorized_successor_preserves_identity_and_default_deny_predecessor(
    tmp_path: Path,
) -> None:
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    created_at = datetime(2026, 8, 6, 3, tzinfo=UTC)

    successor = store.create_successor(
        initial,
        assessment=assessment(),
        route_binding=route(),
        created_at=created_at,
    )

    assert successor.manifest_id != initial.manifest_id
    assert successor.predecessor_manifest_id == initial.manifest_id
    assert successor.cloud_egress_authorized is True
    assert successor.document_id == initial.document_id
    assert successor.document_version == initial.document_version
    assert successor.download_url == initial.download_url
    assert successor.archive_sha256 == initial.archive_sha256
    assert successor.docx_sha256 == initial.docx_sha256
    assert successor.downloaded_at == initial.downloaded_at
    assert initial.cloud_egress_authorized is False
    assert initial.predecessor_manifest_id is None


def test_default_deny_expired_and_mismatched_manifests_do_not_authorize_routes(
    tmp_path: Path,
) -> None:
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    created_at = datetime(2026, 8, 6, 3, tzinfo=UTC)
    successor = store.create_successor(
        initial,
        assessment=assessment(),
        route_binding=route(),
        created_at=created_at,
    )

    assert not initial.authorizes(route(), at=created_at)
    assert successor.authorizes(route(), at=created_at)
    assert not successor.authorizes(
        route(provider_id="provider-b"),
        at=created_at,
    )
    assert not successor.authorizes(
        route(endpoint_purpose="other-purpose"),
        at=created_at,
    )
    assert not successor.authorizes(
        route(use=ProviderUse.OFFLINE_JUDGE),
        at=created_at,
    )
    assert not successor.authorizes(
        route(),
        at=datetime(2026, 8, 8, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "bad_assessment",
    [
        assessment(authorized=False),
        assessment(provider_id="provider-b"),
        assessment(endpoint_purpose="other-purpose"),
        assessment(
            expires_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
        ),
    ],
)
def test_successor_rejects_non_authorizing_expired_or_mismatched_conclusion(
    tmp_path: Path,
    bad_assessment: ComplianceAssessment,
) -> None:
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))

    with pytest.raises(ValidationError):
        store.create_successor(
            initial,
            assessment=bad_assessment,
            route_binding=route(),
            created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
        )


def test_policy_readings_require_explicit_uncertainty() -> None:
    with pytest.raises(ValidationError):
        SourceTermsAssessment(
            terms_snapshot=snapshot("source-terms"),
            summary="Reviewed.",
            uncertainty=(),
        )

    with pytest.raises(ValidationError):
        ProviderPolicyAssessment(
            policy_snapshot=snapshot("provider-policy"),
            retention_summary="Reviewed.",
            training_summary="Reviewed.",
            region_summary="Reviewed.",
            subprocessor_summary="Reviewed.",
            uncertainty=(),
        )


def test_outbound_limit_hash_must_cover_the_exact_premise() -> None:
    with pytest.raises(ValidationError):
        OutboundLimitAssessment(
            premise="A precise outbound limit.",
            premise_sha256="e" * 64,
        )


def test_assessment_cannot_claim_external_approval() -> None:
    fields = assessment().author_conclusion.model_dump()
    fields["external_approval"] = True

    with pytest.raises(ValidationError):
        AuthorizationConclusion(**fields)


def test_authorization_check_requires_an_aware_timestamp(
    tmp_path: Path,
) -> None:
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    successor = store.create_successor(
        initial,
        assessment=assessment(),
        route_binding=route(),
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        successor.authorizes(route(), at=datetime(2026, 8, 6, 3))


def test_successor_rejects_a_conclusion_authored_after_creation(
    tmp_path: Path,
) -> None:
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    fields = assessment().author_conclusion.model_dump()
    fields["authored_at"] = datetime(2026, 8, 6, 3, 0, 1, tzinfo=UTC)
    bad_assessment = assessment().model_copy(
        update={"author_conclusion": AuthorizationConclusion(**fields)}
    )

    with pytest.raises(ValidationError):
        store.create_successor(
            initial,
            assessment=bad_assessment,
            route_binding=route(),
            created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
        )


def test_successor_assessment_is_deeply_immutable() -> None:
    review = assessment()

    with pytest.raises(ValidationError):
        review.source_terms.summary = "changed"  # type: ignore[misc]

    assert isinstance(review.source_terms.uncertainty, tuple)


@pytest.mark.parametrize("coerced_value", ["yes", 1, 0])
def test_authorization_conclusion_rejects_coerced_booleans(
    coerced_value: object,
) -> None:
    fields = assessment().author_conclusion.model_dump()
    fields["authorized"] = coerced_value

    with pytest.raises(ValidationError) as raised:
        AuthorizationConclusion(**fields)

    assert {error["type"] for error in raised.value.errors()} == {"bool_type"}


@pytest.mark.parametrize("coerced_value", ["yes", 1, 0])
def test_manifest_cloud_authorization_rejects_coerced_booleans(
    coerced_value: object,
) -> None:
    fields = initial_fields()
    fields["cloud_egress_authorized"] = coerced_value

    with pytest.raises(ValidationError) as raised:
        SourceManifestDraft(**fields)

    assert "bool_type" in {error["type"] for error in raised.value.errors()}


@pytest.mark.parametrize(
    ("field", "unintended_input"),
    [
        ("downloaded_at", 1_723_000_000),
        ("created_at", 1_723_000_000.0),
        ("downloaded_at", True),
        ("created_at", object()),
        ("downloaded_at", "2026-08-06T01:30:00"),
    ],
)
def test_manifest_timestamps_reject_pre_coercion_inputs(
    field: str,
    unintended_input: object,
) -> None:
    fields = initial_fields()
    fields[field] = unintended_input

    with pytest.raises(ValidationError):
        SourceManifestDraft(**fields)


@pytest.mark.parametrize("field", ["authored_at", "expires_at"])
def test_authorization_timestamps_reject_numeric_epochs(field: str) -> None:
    fields = assessment().author_conclusion.model_dump()
    fields[field] = 1_723_000_000

    with pytest.raises(ValidationError):
        AuthorizationConclusion(**fields)


def test_snapshot_timestamp_rejects_numeric_epoch() -> None:
    with pytest.raises(ValidationError):
        EvidenceSnapshot(
            snapshot_url="https://evidence.example/snapshot.html",
            snapshot_sha256="d" * 64,
            captured_at=1_723_000_000,
        )


def test_canonical_authorized_manifest_json_round_trip(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifests")
    initial = store.create_source(SourceManifestDraft(**initial_fields()))
    successor = store.create_successor(
        initial,
        assessment=assessment(),
        route_binding=route(),
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )
    canonical = canonical_json(successor, include_manifest_id=True)

    assert SourceManifest.model_validate_json(canonical) == successor
