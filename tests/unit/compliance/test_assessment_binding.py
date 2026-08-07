from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.compliance.validation import (
    TASK8_CHATANYWHERE_ROUTE,
    TASK8_DEEPSEEK_CONCLUSION,
    TASK8_DEEPSEEK_ROUTE,
    AssessmentBindingError,
    validate_task8_source_bound_assessment,
)
from specpilot.contracts.compliance import (
    ComplianceAssessmentDraft,
    ComplianceEvidenceIndex,
    DeepSeekAccountNotCaptured,
    DeepSeekAccountObservation,
    EvidenceIndexEntry,
    SourceBoundAssessment,
)
from specpilot.contracts.manifests import (
    AuthorizationConclusion,
    ComplianceAssessment,
    EvidenceSnapshot,
    OutboundLimitAssessment,
    ProviderPolicyAssessment,
    ProviderRouteBinding,
    SourceManifest,
    SourceManifestDraft,
    SourceTermsAssessment,
)
from specpilot.manifests.canonical import canonical_sha256
from specpilot.manifests.store import ManifestStore

_DEEPSEEK_STATEMENT = (
    "基于截至2026-08-07记录的3GPP/ETSI官方条款、适用于本账户的服务商数据政策，"
    "以及default-v1强制出站限制，我授权仅将该冻结源文档的白名单字段和受限片段通过"
    "DeepSeek官方API路由用于SpecPilot在线主链处理。"
)
_DEEPSEEK_CONCLUSION_FIELDS: dict[str, object] = {
    "authorized": True,
    "authorization_statement": _DEEPSEEK_STATEMENT,
    "author_id": "chunxue",
    "provider_id": "deepseek",
    "endpoint_purpose": "online-main-deepseek-v4-flash-api",
    "authored_at": "2026-08-06T20:00:00Z",
    "expires_at": "2026-09-05T20:00:00Z",
}
_DEEPSEEK_ROUTE_FIELDS: dict[str, object] = {
    "provider_id": "deepseek",
    "endpoint_purpose": "online-main-deepseek-v4-flash-api",
    "use": "online_main",
}
_CHATANYWHERE_ROUTE_FIELDS: dict[str, object] = {
    "provider_id": "chatanywhere",
    "endpoint_purpose": "offline-judge-glm-5-2-api",
    "use": "offline_judge",
}


@dataclass(frozen=True)
class StoredSources:
    store: ManifestStore
    directory: Path
    manifests: dict[str, SourceManifest]

    def __getitem__(self, document_id: str) -> SourceManifest:
        return self.manifests[document_id]


def source_fields(document_id: str) -> dict[str, object]:
    return {
        "schema_version": "source-manifest/v1",
        "document_id": document_id,
        "document_version": "Release 19",
        "download_url": f"https://www.3gpp.org/ftp/Specs/{document_id}.zip",
        "archive_sha256": hashlib.sha256(f"{document_id}:zip".encode()).hexdigest(),
        "docx_sha256": hashlib.sha256(f"{document_id}:docx".encode()).hexdigest(),
        "downloaded_at": "2026-08-06T18:00:00Z",
        "created_at": "2026-08-06T18:01:00Z",
    }


@pytest.fixture
def stored_sources(tmp_path: Path) -> StoredSources:
    directory = tmp_path / "manifests"
    store = ManifestStore(directory)
    manifests = {
        document_id: store.create_source(
            SourceManifestDraft(**source_fields(document_id))
        )
        for document_id in ("38.300", "38.401")
    }
    return StoredSources(store=store, directory=directory, manifests=manifests)


def snapshot(name: str) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_url=f"https://evidence.example/{name}.html",
        snapshot_sha256=hashlib.sha256(name.encode()).hexdigest(),
        captured_at="2026-08-06T19:00:00Z",
    )


def build_assessment(
    *,
    author_conclusion: AuthorizationConclusion | None,
) -> ComplianceAssessmentDraft:
    premise = "Only frozen whitelist fields and bounded excerpts may leave locally."
    return ComplianceAssessmentDraft(
        source_terms=SourceTermsAssessment(
            terms_snapshot=snapshot("source-terms"),
            summary="The official source terms were captured and reviewed.",
            uncertainty=("Terms can change after the capture time.",),
        ),
        provider_policy=ProviderPolicyAssessment(
            policy_snapshot=snapshot("provider-policy"),
            retention_summary="Retention was recorded from the provider policy.",
            training_summary="Training use was recorded from the provider policy.",
            region_summary="Processing regions were recorded from the provider policy.",
            subprocessor_summary=(
                "Subprocessors were recorded from the provider policy."
            ),
            uncertainty=("Provider policy can change after capture.",),
        ),
        outbound_limit=OutboundLimitAssessment(
            premise=premise,
            premise_sha256=hashlib.sha256(premise.encode()).hexdigest(),
        ),
        author_conclusion=author_conclusion,
    )


def build_envelope(
    *,
    source_manifest_id: str,
    route_binding: ProviderRouteBinding,
    model_slug: str,
    evidence_index_id: str,
    author_conclusion: AuthorizationConclusion | None,
) -> SourceBoundAssessment:
    return SourceBoundAssessment(
        schema_version="source-bound-assessment/v1",
        source_manifest_id=source_manifest_id,
        route_binding=route_binding,
        model_slug=model_slug,
        evidence_index_id=evidence_index_id,
        assessment=build_assessment(author_conclusion=author_conclusion),
    )


@pytest.fixture
def account_observation() -> DeepSeekAccountObservation:
    return DeepSeekAccountObservation(
        status="observed",
        setting_state="enabled",
        captured_at="2026-08-06T19:30:00Z",
        url="https://platform.deepseek.com/account/data-controls",
        screenshot_sha256="c" * 64,
    )


def index_entry(
    kind: str,
    *,
    url: str = "https://evidence.example/provider-policy.html",
    captured_at: str = "2026-08-06T19:00:00Z",
    sha256: str = "d" * 64,
) -> EvidenceIndexEntry:
    return EvidenceIndexEntry(
        kind=kind,
        url=url,
        captured_at=captured_at,
        sha256=sha256,
        summary=f"Captured evidence for {kind}.",
        scope=f"Scope of {kind} evidence.",
    )


@pytest.fixture
def chatanywhere_index() -> ComplianceEvidenceIndex:
    return ComplianceEvidenceIndex(
        schema_version="compliance-evidence-index/v1",
        route=ProviderRouteBinding(**_CHATANYWHERE_ROUTE_FIELDS),
        model_slug="glm-5.2",
        entries=(index_entry("chatanywhere-policy"),),
    )


@pytest.fixture
def deepseek_index(
    account_observation: DeepSeekAccountObservation,
) -> ComplianceEvidenceIndex:
    return ComplianceEvidenceIndex(
        schema_version="compliance-evidence-index/v1",
        route=ProviderRouteBinding(**_DEEPSEEK_ROUTE_FIELDS),
        model_slug="deepseek-v4-flash",
        entries=(
            index_entry(
                "deepseek-account-setting",
                url=str(account_observation.url),
                captured_at="2026-08-06T19:30:00Z",
                sha256=canonical_sha256(account_observation),
            ),
            index_entry("deepseek-policy"),
        ),
    )


def exact_deepseek_conclusion() -> AuthorizationConclusion:
    return AuthorizationConclusion(**_DEEPSEEK_CONCLUSION_FIELDS)


def test_unsigned_chatanywhere_envelope_is_bound_but_not_complete(
    stored_sources: StoredSources,
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=ProviderRouteBinding(**_CHATANYWHERE_ROUTE_FIELDS),
        model_slug="glm-5.2",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=None,
    )

    resolved = validate_task8_source_bound_assessment(
        envelope,
        manifest_store=stored_sources.store,
        evidence_index=chatanywhere_index,
    )

    assert resolved.manifest_id == envelope.source_manifest_id
    with pytest.raises(ValidationError) as error:
        ComplianceAssessment.model_validate(envelope.assessment.model_dump())
    assert {item["loc"] for item in error.value.errors()} == {
        ("author_conclusion",)
    }


def test_complete_deepseek_envelope_requires_exact_hash_bound_account_evidence(
    stored_sources: StoredSources,
    deepseek_index: ComplianceEvidenceIndex,
    account_observation: DeepSeekAccountObservation,
) -> None:
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=ProviderRouteBinding(**_DEEPSEEK_ROUTE_FIELDS),
        model_slug="deepseek-v4-flash",
        evidence_index_id=canonical_sha256(deepseek_index),
        author_conclusion=exact_deepseek_conclusion(),
    )
    existing_files = tuple(sorted(stored_sources.directory.iterdir()))

    resolved = validate_task8_source_bound_assessment(
        envelope,
        manifest_store=stored_sources.store,
        evidence_index=deepseek_index,
        account_evidence=account_observation,
    )

    assert resolved == stored_sources["38.300"]
    assert tuple(sorted(stored_sources.directory.iterdir())) == existing_files
    assert envelope.route_binding == TASK8_DEEPSEEK_ROUTE
    assert envelope.assessment.author_conclusion == TASK8_DEEPSEEK_CONCLUSION


def test_envelope_refuses_a_model_not_bound_by_its_index(
    stored_sources: StoredSources,
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=ProviderRouteBinding(**_CHATANYWHERE_ROUTE_FIELDS),
        model_slug="different-model",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=None,
    )
    with pytest.raises(AssessmentBindingError, match="model"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=chatanywhere_index,
        )


@pytest.mark.parametrize(
    ("envelope_route", "index_route", "model_slug", "expected_message"),
    [
        (
            _DEEPSEEK_ROUTE_FIELDS,
            _CHATANYWHERE_ROUTE_FIELDS,
            "glm-5.2",
            "route binding is invalid",
        ),
        (
            _DEEPSEEK_ROUTE_FIELDS,
            {
                **_DEEPSEEK_ROUTE_FIELDS,
                "use": "offline_judge",
            },
            "deepseek-v4-flash",
            "route binding is invalid",
        ),
        (
            {
                **_DEEPSEEK_ROUTE_FIELDS,
                "provider_id": "other-provider",
            },
            {
                **_DEEPSEEK_ROUTE_FIELDS,
                "provider_id": "other-provider",
            },
            "deepseek-v4-flash",
            "task8 route is invalid",
        ),
        (
            {
                **_DEEPSEEK_ROUTE_FIELDS,
                "endpoint_purpose": "other-endpoint",
            },
            {
                **_DEEPSEEK_ROUTE_FIELDS,
                "endpoint_purpose": "other-endpoint",
            },
            "deepseek-v4-flash",
            "task8 route is invalid",
        ),
    ],
)
def test_envelope_refuses_route_provider_endpoint_and_use_swaps(
    stored_sources: StoredSources,
    envelope_route: dict[str, object],
    index_route: dict[str, object],
    model_slug: str,
    expected_message: str,
) -> None:
    evidence_index = ComplianceEvidenceIndex(
        schema_version="compliance-evidence-index/v1",
        route=ProviderRouteBinding(**index_route),
        model_slug=model_slug,
        entries=(index_entry("provider-policy"),),
    )
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=ProviderRouteBinding(**envelope_route),
        model_slug=model_slug,
        evidence_index_id=canonical_sha256(evidence_index),
        author_conclusion=None,
    )

    with pytest.raises(AssessmentBindingError, match=expected_message):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=evidence_index,
        )


def test_envelope_refuses_an_evidence_index_id_swap(
    stored_sources: StoredSources,
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    changed_index = chatanywhere_index.model_copy(
        update={"entries": (index_entry("different-policy"),)}
    )
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=TASK8_CHATANYWHERE_ROUTE,
        model_slug="glm-5.2",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=None,
    )

    with pytest.raises(AssessmentBindingError, match="evidence index"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=changed_index,
        )


def test_envelope_refuses_an_unstored_source_swap(
    stored_sources: StoredSources,
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    envelope = build_envelope(
        source_manifest_id="f" * 64,
        route_binding=TASK8_CHATANYWHERE_ROUTE,
        model_slug="glm-5.2",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=None,
    )

    with pytest.raises(AssessmentBindingError, match="source binding is invalid"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=chatanywhere_index,
        )


def test_envelope_refuses_a_stored_successor_source(
    stored_sources: StoredSources,
    deepseek_index: ComplianceEvidenceIndex,
    account_observation: DeepSeekAccountObservation,
) -> None:
    successor = stored_sources.store.create_successor(
        stored_sources["38.300"],
        assessment=ComplianceAssessment.model_validate(
            build_assessment(
                author_conclusion=exact_deepseek_conclusion()
            ).model_dump()
        ),
        route_binding=TASK8_DEEPSEEK_ROUTE,
        created_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    envelope = build_envelope(
        source_manifest_id=successor.manifest_id,
        route_binding=TASK8_DEEPSEEK_ROUTE,
        model_slug="deepseek-v4-flash",
        evidence_index_id=canonical_sha256(deepseek_index),
        author_conclusion=exact_deepseek_conclusion(),
    )

    with pytest.raises(AssessmentBindingError, match="source state is invalid"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=deepseek_index,
            account_evidence=account_observation,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("authorized", False),
        ("authorization_statement", _DEEPSEEK_STATEMENT + "改"),
        ("author_id", "another-author"),
        ("provider_id", "another-provider"),
        ("endpoint_purpose", "another-endpoint"),
        ("authored_at", "2026-08-06T20:00:01Z"),
        ("expires_at", "2026-09-05T20:00:01Z"),
    ],
)
def test_deepseek_conclusion_refuses_every_changed_confirmed_field(
    stored_sources: StoredSources,
    deepseek_index: ComplianceEvidenceIndex,
    account_observation: DeepSeekAccountObservation,
    field: str,
    replacement: object,
) -> None:
    conclusion_fields = {**_DEEPSEEK_CONCLUSION_FIELDS, field: replacement}
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=TASK8_DEEPSEEK_ROUTE,
        model_slug="deepseek-v4-flash",
        evidence_index_id=canonical_sha256(deepseek_index),
        author_conclusion=AuthorizationConclusion(**conclusion_fields),
    )

    with pytest.raises(AssessmentBindingError, match="conclusion content is invalid"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=deepseek_index,
            account_evidence=account_observation,
        )


def test_chatanywhere_refuses_any_present_conclusion(
    stored_sources: StoredSources,
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    fields = {
        **_DEEPSEEK_CONCLUSION_FIELDS,
        "provider_id": "chatanywhere",
        "endpoint_purpose": "offline-judge-glm-5-2-api",
    }
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=TASK8_CHATANYWHERE_ROUTE,
        model_slug="glm-5.2",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=AuthorizationConclusion(**fields),
    )

    with pytest.raises(AssessmentBindingError, match="conclusion route is invalid"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=chatanywhere_index,
        )


@pytest.mark.parametrize(
    "account_evidence_factory",
    [
        lambda observation: None,
        lambda observation: DeepSeekAccountNotCaptured(
            status="not_captured",
            reason="blocked",
            captured_at=observation.captured_at,
            url=str(observation.url),
        ),
        lambda observation: observation.model_copy(
            update={"screenshot_sha256": "e" * 64}
        ),
    ],
)
def test_deepseek_conclusion_refuses_missing_blocked_or_hash_mismatched_account_record(
    stored_sources: StoredSources,
    deepseek_index: ComplianceEvidenceIndex,
    account_observation: DeepSeekAccountObservation,
    account_evidence_factory: Callable[
        [DeepSeekAccountObservation],
        DeepSeekAccountObservation | DeepSeekAccountNotCaptured | None,
    ],
) -> None:
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=TASK8_DEEPSEEK_ROUTE,
        model_slug="deepseek-v4-flash",
        evidence_index_id=canonical_sha256(deepseek_index),
        author_conclusion=exact_deepseek_conclusion(),
    )

    with pytest.raises(AssessmentBindingError, match="account evidence is invalid"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=deepseek_index,
            account_evidence=account_evidence_factory(account_observation),
        )


@pytest.mark.parametrize(
    "entries",
    [
        (index_entry("same-kind"), index_entry("same-kind")),
        (index_entry("z-kind"), index_entry("a-kind")),
    ],
)
def test_evidence_index_requires_unique_lexicographically_sorted_kinds(
    entries: tuple[EvidenceIndexEntry, ...],
) -> None:
    with pytest.raises(ValidationError):
        ComplianceEvidenceIndex(
            schema_version="compliance-evidence-index/v1",
            route=TASK8_CHATANYWHERE_ROUTE,
            model_slug="glm-5.2",
            entries=entries,
        )


def test_evidence_index_id_changes_when_model_slug_changes(
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    changed = chatanywhere_index.model_copy(update={"model_slug": "different-model"})

    assert canonical_sha256(changed) != canonical_sha256(chatanywhere_index)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EvidenceIndexEntry(
            **{
                **index_entry("unsafe").model_dump(),
                "url": "http://evidence.example/unsafe",
            }
        ),
        lambda: DeepSeekAccountObservation(
            status="observed",
            setting_state="enabled",
            captured_at="2026-08-06T19:30:00Z",
            url="http://platform.deepseek.com/settings",
            screenshot_sha256="c" * 64,
        ),
        lambda: DeepSeekAccountNotCaptured(
            status="not_captured",
            reason="blocked",
            captured_at="2026-08-06T19:30:00Z",
            url="http://platform.deepseek.com/settings",
        ),
    ],
)
def test_evidence_contracts_reject_unsafe_urls(factory: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EvidenceIndexEntry(
            **{
                **index_entry("bad-time").model_dump(),
                "captured_at": 1_723_000_000,
            }
        ),
        lambda: DeepSeekAccountObservation(
            status="observed",
            setting_state="enabled",
            captured_at="2026-08-06T19:30:00",
            url="https://platform.deepseek.com/settings",
            screenshot_sha256="c" * 64,
        ),
        lambda: DeepSeekAccountNotCaptured(
            status="not_captured",
            reason="blocked",
            captured_at=True,
            url="https://platform.deepseek.com/settings",
        ),
    ],
)
def test_evidence_contracts_reject_invalid_timestamps(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValidationError):
        factory()


@pytest.mark.parametrize(
    ("model", "extra_fields"),
    [
        (DeepSeekAccountObservation, {"unexpected": "value"}),
        (DeepSeekAccountNotCaptured, {"unexpected": "value"}),
        (EvidenceIndexEntry, {"unexpected": "value"}),
        (ComplianceEvidenceIndex, {"unexpected": "value"}),
        (SourceBoundAssessment, {"unexpected": "value"}),
    ],
)
def test_compliance_contracts_forbid_extra_fields(
    model: type,
    extra_fields: dict[str, object],
    chatanywhere_index: ComplianceEvidenceIndex,
    stored_sources: StoredSources,
) -> None:
    valid_by_model = {
        DeepSeekAccountObservation: {
            "status": "observed",
            "setting_state": "enabled",
            "captured_at": "2026-08-06T19:30:00Z",
            "url": "https://platform.deepseek.com/settings",
            "screenshot_sha256": "c" * 64,
        },
        DeepSeekAccountNotCaptured: {
            "status": "not_captured",
            "reason": "blocked",
            "captured_at": "2026-08-06T19:30:00Z",
            "url": "https://platform.deepseek.com/settings",
        },
        EvidenceIndexEntry: index_entry("entry").model_dump(),
        ComplianceEvidenceIndex: chatanywhere_index.model_dump(),
        SourceBoundAssessment: build_envelope(
            source_manifest_id=stored_sources["38.300"].manifest_id,
            route_binding=TASK8_CHATANYWHERE_ROUTE,
            model_slug="glm-5.2",
            evidence_index_id=canonical_sha256(chatanywhere_index),
            author_conclusion=None,
        ).model_dump(),
    }

    with pytest.raises(ValidationError):
        model(**valid_by_model[model], **extra_fields)


def test_compliance_contracts_are_deeply_immutable(
    chatanywhere_index: ComplianceEvidenceIndex,
) -> None:
    with pytest.raises(ValidationError):
        chatanywhere_index.entries[0].summary = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("authorized", [1, 0, "true", "false"])
def test_authorization_conclusion_rejects_non_strict_booleans(
    authorized: object,
) -> None:
    fields = {**_DEEPSEEK_CONCLUSION_FIELDS, "authorized": authorized}

    with pytest.raises(ValidationError):
        AuthorizationConclusion(**fields)


@pytest.mark.parametrize("affix", [" ", "\n"])
def test_authorization_statement_rejects_boundary_whitespace(affix: str) -> None:
    for statement in (affix + _DEEPSEEK_STATEMENT, _DEEPSEEK_STATEMENT + affix):
        fields = {
            **_DEEPSEEK_CONCLUSION_FIELDS,
            "authorization_statement": statement,
        }
        with pytest.raises(ValidationError):
            AuthorizationConclusion(**fields)
