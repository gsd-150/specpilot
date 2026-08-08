from __future__ import annotations

from datetime import UTC, datetime

import pytest

from specpilot.contracts.manifests import (
    SourceManifest,
    SourceManifestDraft,
)
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from tests.unit.egress.test_disclosure_caps import apply_reservation
from tests.unit.egress.test_policy_projection import (
    NOW,
    FixtureTokenCounter,
    egress_request,
    fixture_enforcer,
    fixture_policy,
    fixture_store,
    online_route,
)
from tests.unit.manifests.test_source_manifest import assessment, initial_fields


def unstored_authorized_manifest() -> SourceManifest:
    """A self-consistent authorized manifest that no store ever accepted.

    Content addressing makes ``manifest_id`` unforgeable for given content, but
    it says nothing about whether a compliance decision was ever recorded. This
    is exactly the object a buggy or hostile caller can hand to the enforcer.

    ``created_at`` deliberately differs from the shared fixture successor's, so
    this manifest hashes to an ID no fixture has ever stored. Reusing the same
    timestamp would produce a byte-identical manifest and prove nothing.
    """
    route = online_route()
    initial = SourceManifest.from_draft(SourceManifestDraft(**initial_fields()))
    return SourceManifest.from_draft(
        SourceManifestDraft(
            **{
                **initial_fields(),
                "created_at": datetime(2026, 8, 6, 3, 30, tzinfo=UTC),
                "predecessor_manifest_id": initial.manifest_id,
                "cloud_egress_authorized": True,
                "compliance_assessment": assessment(
                    provider_id=route.provider_id,
                    endpoint_purpose=route.endpoint_purpose,
                ),
                "provider_route_binding": route,
            }
        )
    )


def test_unstored_manifest_claiming_authorization_cannot_reach_a_provider() -> None:
    manifest = unstored_authorized_manifest()
    assert manifest.authorizes(online_route(), at=NOW), (
        "fixture must be self-consistent, otherwise the test proves nothing"
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        fixture_enforcer().prepare(
            egress_request(manifest=manifest),
            FixtureTokenCounter(),
        )

    assert caught.value.code == "source_manifest_unresolvable"


def test_apply_also_resolves_the_manifest_and_does_not_trust_the_reservation() -> None:
    """A reservation minted against a permissive resolver still fails at apply."""
    manifest = unstored_authorized_manifest()
    reservation = EgressPolicyEnforcer(
        fixture_policy(),
        manifests=_ResolverAlwaysReturning(manifest),
        clock=lambda: NOW,
    ).prepare(egress_request(manifest=manifest), FixtureTokenCounter())

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_reservation(None, reservation)

    assert caught.value.code == "source_manifest_unresolvable"


def test_resolver_returning_a_different_manifest_is_rejected() -> None:
    stored = egress_request().source_manifest
    substitute = unstored_authorized_manifest()
    assert substitute.manifest_id != stored.manifest_id
    enforcer = EgressPolicyEnforcer(
        fixture_policy(),
        manifests=_ResolverAlwaysReturning(substitute),
        clock=lambda: NOW,
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        enforcer.prepare(
            egress_request(manifest=stored),
            FixtureTokenCounter(),
        )

    assert caught.value.code == "source_manifest_untrusted"


def test_stored_default_deny_manifest_still_authorizes_nothing() -> None:
    initial = fixture_store().create_source(SourceManifestDraft(**initial_fields()))

    with pytest.raises(EgressPolicyViolation) as caught:
        fixture_enforcer().prepare(
            egress_request(manifest=initial),
            FixtureTokenCounter(),
        )

    assert caught.value.code == "route_unauthorized"


def test_enforcer_requires_a_resolver_and_has_no_trusting_default() -> None:
    with pytest.raises(TypeError):
        EgressPolicyEnforcer(fixture_policy())  # type: ignore[call-arg]


class _ResolverAlwaysReturning:
    """A deliberately permissive resolver: answers every ID with one manifest."""

    def __init__(self, manifest: SourceManifest) -> None:
        self._manifest = manifest

    def read_source(self, manifest_id: str) -> SourceManifest:
        return self._manifest
