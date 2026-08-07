from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import ValidationError

from specpilot.contracts.compliance import (
    ComplianceEvidenceIndex,
    DeepSeekAccountNotCaptured,
    DeepSeekAccountObservation,
    SourceBoundAssessment,
)
from specpilot.contracts.manifests import (
    AuthorizationConclusion,
    ComplianceAssessment,
    ProviderRouteBinding,
    ProviderUse,
    SourceManifest,
)
from specpilot.manifests.canonical import canonical_sha256
from specpilot.manifests.store import ManifestStore

TASK8_DEEPSEEK_ROUTE = ProviderRouteBinding(
    provider_id="deepseek",
    endpoint_purpose="online-main-deepseek-v4-flash-api",
    use=ProviderUse.ONLINE_MAIN,
)
TASK8_CHATANYWHERE_ROUTE = ProviderRouteBinding(
    provider_id="chatanywhere",
    endpoint_purpose="offline-judge-glm-5-2-api",
    use=ProviderUse.OFFLINE_JUDGE,
)
TASK8_MODELS_BY_ROUTE = {
    TASK8_DEEPSEEK_ROUTE: "deepseek-v4-flash",
    TASK8_CHATANYWHERE_ROUTE: "glm-5.2",
}
TASK8_DEEPSEEK_CONCLUSION = AuthorizationConclusion(
    authorized=True,
    authorization_statement=(
        "基于截至2026-08-07记录的3GPP/ETSI官方条款、适用于本账户的服务商数据政策，"
        "以及default-v1强制出站限制，我授权仅将该冻结源文档的白名单字段和受限片段通过"
        "DeepSeek官方API路由用于SpecPilot在线主链处理。"
    ),
    author_id="chunxue",
    provider_id="deepseek",
    endpoint_purpose="online-main-deepseek-v4-flash-api",
    authored_at=datetime(2026, 8, 6, 20, tzinfo=UTC),
    expires_at=datetime(2026, 9, 5, 20, tzinfo=UTC),
)
_TASK8_DEEPSEEK_STATEMENT_BYTES = 268
_TASK8_DEEPSEEK_STATEMENT_SHA256 = (
    "b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215"
)


class AssessmentBindingError(ValueError):
    """A source-bound assessment failed mechanical binding validation."""


def validate_task8_source_bound_assessment(
    envelope: SourceBoundAssessment,
    *,
    manifest_store: ManifestStore,
    evidence_index: ComplianceEvidenceIndex,
    account_evidence: (
        DeepSeekAccountObservation | DeepSeekAccountNotCaptured | None
    ) = None,
) -> SourceManifest:
    try:
        manifest = manifest_store.read_source(envelope.source_manifest_id)
    except (OSError, RuntimeError, ValueError) as error:
        raise AssessmentBindingError("source binding is invalid") from error
    if (
        manifest.predecessor_manifest_id is not None
        or manifest.cloud_egress_authorized
        or manifest.compliance_assessment is not None
        or manifest.provider_route_binding is not None
    ):
        raise AssessmentBindingError("source state is invalid")
    if canonical_sha256(evidence_index) != envelope.evidence_index_id:
        raise AssessmentBindingError("evidence index binding is invalid")
    if evidence_index.route != envelope.route_binding:
        raise AssessmentBindingError("route binding is invalid")
    if evidence_index.model_slug != envelope.model_slug:
        raise AssessmentBindingError("model binding is invalid")
    expected_model = TASK8_MODELS_BY_ROUTE.get(envelope.route_binding)
    if expected_model is None or expected_model != envelope.model_slug:
        raise AssessmentBindingError("task8 route is invalid")
    conclusion = envelope.assessment.author_conclusion
    if conclusion is not None:
        if envelope.route_binding != TASK8_DEEPSEEK_ROUTE:
            raise AssessmentBindingError("conclusion route is invalid")
        if conclusion != TASK8_DEEPSEEK_CONCLUSION:
            raise AssessmentBindingError("conclusion content is invalid")
        statement = conclusion.authorization_statement.encode("utf-8")
        if len(statement) != _TASK8_DEEPSEEK_STATEMENT_BYTES or hashlib.sha256(
            statement
        ).hexdigest() != _TASK8_DEEPSEEK_STATEMENT_SHA256:
            raise AssessmentBindingError("conclusion content is invalid")
        if not isinstance(account_evidence, DeepSeekAccountObservation):
            raise AssessmentBindingError("account evidence is invalid")
        account_entry = next(
            (
                entry
                for entry in evidence_index.entries
                if entry.kind == "deepseek-account-setting"
            ),
            None,
        )
        if (
            account_entry is None
            or account_entry.sha256 != canonical_sha256(account_evidence)
            or str(account_entry.url) != str(account_evidence.url)
            or account_entry.captured_at != account_evidence.captured_at
        ):
            raise AssessmentBindingError("account evidence is invalid")
        try:
            ComplianceAssessment.model_validate(envelope.assessment.model_dump())
        except ValidationError as error:
            raise AssessmentBindingError("conclusion content is invalid") from error
    return manifest
