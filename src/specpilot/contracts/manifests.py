from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
Summary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
Statement = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_048),
]
HttpsUrl = Annotated[AnyUrl, Field(max_length=2_048)]
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


def _utc_timestamp(value: datetime, field_name: str | None) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validated_timestamp_input(value: object, field_name: str | None) -> datetime:
    if isinstance(value, datetime):
        return _utc_timestamp(value, field_name)
    if not isinstance(value, str) or _RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an RFC3339 timestamp") from error
    return _utc_timestamp(parsed, field_name)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceSnapshot(_FrozenModel):
    snapshot_url: HttpsUrl
    snapshot_sha256: Sha256
    captured_at: datetime

    @field_validator("snapshot_url")
    @classmethod
    def _require_https(cls, value: AnyUrl) -> AnyUrl:
        if value.scheme != "https":
            raise ValueError("snapshot URL must use HTTPS")
        return value

    @field_validator("captured_at", mode="before")
    @classmethod
    def _normalize_timestamp(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> datetime:
        return _validated_timestamp_input(value, info.field_name)


class SourceTermsAssessment(_FrozenModel):
    terms_snapshot: EvidenceSnapshot
    summary: Summary
    uncertainty: Annotated[tuple[Statement, ...], Field(min_length=1, max_length=16)]


class ProviderPolicyAssessment(_FrozenModel):
    policy_snapshot: EvidenceSnapshot
    retention_summary: Summary
    training_summary: Summary
    region_summary: Summary
    subprocessor_summary: Summary
    uncertainty: Annotated[tuple[Statement, ...], Field(min_length=1, max_length=16)]


class OutboundLimitAssessment(_FrozenModel):
    premise: Summary
    premise_sha256: Sha256

    @model_validator(mode="after")
    def _verify_exact_premise_hash(self) -> Self:
        expected = hashlib.sha256(self.premise.encode("utf-8")).hexdigest()
        if self.premise_sha256 != expected:
            raise ValueError("premise_sha256 does not match the exact premise")
        return self


class AuthorizationConclusion(_FrozenModel):
    authorized: StrictBool
    authorization_statement: Statement
    author_id: Identifier
    provider_id: Identifier
    endpoint_purpose: Identifier
    authored_at: datetime
    expires_at: datetime

    @field_validator("authorization_statement", mode="before")
    @classmethod
    def _reject_boundary_whitespace(cls, value: object) -> str:
        if not isinstance(value, str) or value != value.strip():
            raise ValueError(
                "authorization_statement must be a boundary-whitespace-free string"
            )
        return value

    @field_validator("authored_at", "expires_at", mode="before")
    @classmethod
    def _normalize_timestamp(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> datetime:
        return _validated_timestamp_input(value, info.field_name)

    @model_validator(mode="after")
    def _require_forward_expiry(self) -> Self:
        if self.expires_at <= self.authored_at:
            raise ValueError("authorization expiry must follow authorship")
        return self


class ComplianceAssessment(_FrozenModel):
    source_terms: SourceTermsAssessment
    provider_policy: ProviderPolicyAssessment
    outbound_limit: OutboundLimitAssessment
    author_conclusion: AuthorizationConclusion


class ProviderUse(StrEnum):
    ONLINE_MAIN = "online_main"
    OFFLINE_JUDGE = "offline_judge"


class ProviderRouteBinding(_FrozenModel):
    provider_id: Identifier
    endpoint_purpose: Identifier
    use: ProviderUse


class SourceManifestDraft(_FrozenModel):
    schema_version: Literal["source-manifest/v1"] = "source-manifest/v1"
    document_id: Identifier
    document_version: Identifier
    download_url: HttpsUrl
    archive_sha256: Sha256
    docx_sha256: Sha256
    downloaded_at: datetime
    created_at: datetime
    predecessor_manifest_id: Sha256 | None = None
    cloud_egress_authorized: StrictBool = False
    compliance_assessment: ComplianceAssessment | None = None
    provider_route_binding: ProviderRouteBinding | None = None

    @field_validator("download_url")
    @classmethod
    def _require_https(cls, value: AnyUrl) -> AnyUrl:
        if value.scheme != "https":
            raise ValueError("download URL must use HTTPS")
        return value

    @field_validator("downloaded_at", "created_at", mode="before")
    @classmethod
    def _normalize_timestamp(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> datetime:
        return _validated_timestamp_input(value, info.field_name)

    @model_validator(mode="after")
    def _enforce_manifest_state(self) -> Self:
        if self.predecessor_manifest_id is None:
            if (
                self.cloud_egress_authorized
                or self.compliance_assessment is not None
                or self.provider_route_binding is not None
            ):
                raise ValueError("initial source manifest must be default-deny")
            return self

        if (
            not self.cloud_egress_authorized
            or self.compliance_assessment is None
            or self.provider_route_binding is None
        ):
            raise ValueError("successor must contain explicit authorization evidence")

        conclusion = self.compliance_assessment.author_conclusion
        route = self.provider_route_binding
        if not conclusion.authorized:
            raise ValueError("successor conclusion must explicitly authorize egress")
        if (
            conclusion.provider_id != route.provider_id
            or conclusion.endpoint_purpose != route.endpoint_purpose
        ):
            raise ValueError("successor conclusion must match its provider route")
        if conclusion.authored_at > self.created_at:
            raise ValueError("successor conclusion cannot postdate the successor")
        if conclusion.expires_at <= self.created_at:
            raise ValueError("successor conclusion is expired at creation")
        return self


class SourceManifest(SourceManifestDraft):
    manifest_id: Sha256

    @model_validator(mode="after")
    def _verify_manifest_id(self) -> Self:
        from specpilot.manifests.canonical import canonical_sha256

        if self.manifest_id != canonical_sha256(self):
            raise ValueError("manifest_id does not match canonical content")
        return self

    @classmethod
    def from_draft(cls, draft: SourceManifestDraft) -> SourceManifest:
        from specpilot.manifests.canonical import canonical_sha256

        return cls(
            manifest_id=canonical_sha256(draft),
            **draft.model_dump(),
        )

    def authorizes(self, route: ProviderRouteBinding, *, at: datetime) -> bool:
        """Return whether this manifest authorizes exactly ``route`` at ``at``."""
        checked_at = _utc_timestamp(at, "at")
        assessment = self.compliance_assessment
        if (
            not self.cloud_egress_authorized
            or assessment is None
            or self.provider_route_binding != route
        ):
            return False
        conclusion = assessment.author_conclusion
        return (
            conclusion.authorized
            and conclusion.provider_id == route.provider_id
            and conclusion.endpoint_purpose == route.endpoint_purpose
            and conclusion.authored_at <= checked_at < conclusion.expires_at
        )
