from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import ValidationError

from specpilot.contracts.compliance import (
    ComplianceEvidenceIndex,
    ProviderPolicyEvidence,
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
    authored_at=datetime(2026, 8, 7, 14, 44, tzinfo=UTC),
    expires_at=datetime(2026, 9, 6, 14, 44, tzinfo=UTC),
)
_TASK8_DEEPSEEK_STATEMENT_BYTES = 268
_TASK8_DEEPSEEK_STATEMENT_SHA256 = (
    "b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215"
)

# A conclusion authorizes an API route, so the evidence that gates it must be
# the provider's own documents governing that API. An account-level toggle in a
# provider's consumer chat product governs a different surface and is recorded
# as optional context, never as the gate.
TASK8_REQUIRED_POLICY_EVIDENCE_KINDS: dict[ProviderRouteBinding, frozenset[str]] = {
    TASK8_DEEPSEEK_ROUTE: frozenset(
        {"deepseek-api-docs", "deepseek-privacy", "deepseek-terms"}
    ),
}


class AssessmentBindingError(ValueError):
    """A source-bound assessment failed mechanical binding validation."""


def _verify_policy_evidence(
    route: ProviderRouteBinding,
    evidence_index: ComplianceEvidenceIndex,
    policy_evidence: tuple[ProviderPolicyEvidence, ...],
    *,
    authored_at: datetime,
) -> None:
    """Require the index to hash-bind every document the route's gate needs.

    Coverage alone is not enough: each required index entry must be backed by a
    supplied record whose document hash, URL, and capture time all agree, and no
    required document may have been frozen after the conclusion was written.
    """
    # An empty required set must fail closed too: a route whose gate demands
    # nothing would accept a conclusion resting on no evidence at all.
    required = TASK8_REQUIRED_POLICY_EVIDENCE_KINDS.get(route)
    if not required:
        raise AssessmentBindingError("policy evidence is invalid")
    supplied = {record.kind: record for record in policy_evidence}
    if len(supplied) != len(policy_evidence):
        raise AssessmentBindingError("policy evidence is invalid")
    entries = {entry.kind: entry for entry in evidence_index.entries}
    for kind in sorted(required):
        entry = entries.get(kind)
        record = supplied.get(kind)
        if entry is None or record is None:
            raise AssessmentBindingError("policy evidence is invalid")
        if (
            entry.sha256 != record.document_sha256
            or str(entry.url) != str(record.url)
            or entry.captured_at != record.captured_at
        ):
            raise AssessmentBindingError("policy evidence is invalid")
        if entry.captured_at > authored_at:
            raise AssessmentBindingError("policy evidence postdates the conclusion")


def validate_task8_source_bound_assessment(
    envelope: SourceBoundAssessment,
    *,
    manifest_store: ManifestStore,
    evidence_index: ComplianceEvidenceIndex,
    policy_evidence: tuple[ProviderPolicyEvidence, ...] = (),
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
        _verify_policy_evidence(
            envelope.route_binding,
            evidence_index,
            policy_evidence,
            authored_at=conclusion.authored_at,
        )
        try:
            ComplianceAssessment.model_validate(envelope.assessment.model_dump())
        except ValidationError as error:
            raise AssessmentBindingError("conclusion content is invalid") from error
    return manifest
