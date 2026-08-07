from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from specpilot.contracts.manifests import (
    AuthorizationConclusion,
    HttpsUrl,
    Identifier,
    OutboundLimitAssessment,
    ProviderPolicyAssessment,
    ProviderRouteBinding,
    Sha256,
    SourceTermsAssessment,
    Summary,
    _validated_timestamp_input,
)


class FrozenComplianceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_https(value: AnyUrl, field_name: str) -> AnyUrl:
    if value.scheme != "https":
        raise ValueError(f"{field_name} must use HTTPS")
    return value


class ComplianceAssessmentDraft(FrozenComplianceModel):
    source_terms: SourceTermsAssessment
    provider_policy: ProviderPolicyAssessment
    outbound_limit: OutboundLimitAssessment
    author_conclusion: AuthorizationConclusion | None = None


class EvidenceIndexEntry(FrozenComplianceModel):
    kind: Identifier
    url: HttpsUrl
    captured_at: datetime
    sha256: Sha256
    summary: Summary
    scope: Summary

    @field_validator("url")
    @classmethod
    def _require_https_url(cls, value: AnyUrl) -> AnyUrl:
        return _require_https(value, "evidence URL")

    @field_validator("captured_at", mode="before")
    @classmethod
    def _normalize_timestamp(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> datetime:
        return _validated_timestamp_input(value, info.field_name)


class ComplianceEvidenceIndex(FrozenComplianceModel):
    schema_version: Literal["compliance-evidence-index/v1"]
    route: ProviderRouteBinding
    model_slug: Identifier
    entries: Annotated[tuple[EvidenceIndexEntry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _require_unique_sorted_kinds(self) -> Self:
        kinds = tuple(entry.kind for entry in self.entries)
        if len(kinds) != len(set(kinds)) or kinds != tuple(sorted(kinds)):
            raise ValueError(
                "evidence kinds must be unique and lexicographically sorted"
            )
        return self


class DeepSeekAccountObservation(FrozenComplianceModel):
    status: Literal["observed"]
    setting_state: Literal["enabled", "disabled"]
    captured_at: datetime
    url: HttpsUrl
    screenshot_sha256: Sha256

    @field_validator("url")
    @classmethod
    def _require_https_url(cls, value: AnyUrl) -> AnyUrl:
        return _require_https(value, "account evidence URL")

    @field_validator("captured_at", mode="before")
    @classmethod
    def _normalize_timestamp(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> datetime:
        return _validated_timestamp_input(value, info.field_name)


class DeepSeekAccountNotCaptured(FrozenComplianceModel):
    status: Literal["not_captured"]
    reason: Identifier
    captured_at: datetime
    url: HttpsUrl

    @field_validator("url")
    @classmethod
    def _require_https_url(cls, value: AnyUrl) -> AnyUrl:
        return _require_https(value, "account evidence URL")

    @field_validator("captured_at", mode="before")
    @classmethod
    def _normalize_timestamp(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> datetime:
        return _validated_timestamp_input(value, info.field_name)


class SourceBoundAssessment(FrozenComplianceModel):
    schema_version: Literal["source-bound-assessment/v1"]
    source_manifest_id: Sha256
    route_binding: ProviderRouteBinding
    model_slug: Identifier
    evidence_index_id: Sha256
    assessment: ComplianceAssessmentDraft
