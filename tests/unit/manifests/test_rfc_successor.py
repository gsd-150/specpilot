"""Authorizing an RFC source, which until now was impossible.

§3.2 makes source manifests default-deny and expresses authorization as a
successor carrying a recorded compliance decision. The v1 family has had that
path since W0; the v2 family — the one holding the only corpus this project has
— did not, so no RFC could ever be sent from. These tests pin the path and the
things it must refuse.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    ProviderUse,
    RfcSourceManifestDraft,
    SourceManifestDraft,
)
from specpilot.manifests.store import ManifestStore
from tests.helpers import rfc_factory
from tests.unit.manifests.test_source_manifest import assessment, initial_fields

CREATED = datetime(2026, 8, 6, 3, tzinfo=UTC)


def binding() -> ProviderRouteBinding:
    return ProviderRouteBinding(
        provider_id="provider-a",
        endpoint_purpose="evidence-review",
        use=ProviderUse.ONLINE_MAIN,
    )


@pytest.fixture
def store(tmp_path: Path) -> ManifestStore:
    return ManifestStore(tmp_path / "manifests")


def rfc_draft(tmp_path: Path, **overrides: object) -> RfcSourceManifestDraft:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700, exist_ok=True)
    xml = rfc_factory.write_safe(directory)
    fields: dict[str, object] = {
        "document_id": "ietf-rfc-9999",
        "document_version": "2026-08",
        "text_url": "https://www.rfc-editor.org/rfc/rfc9999.txt",
        "xml_url": "https://www.rfc-editor.org/rfc/rfc9999.xml",
        "text_sha256": "a" * 64,
        "xml_sha256": hashlib.sha256(xml.read_bytes()).hexdigest(),
        "downloaded_at": "2026-08-06T02:00:00Z",
        "created_at": "2026-08-06T02:01:00Z",
        **overrides,
    }
    return RfcSourceManifestDraft(**fields)  # type: ignore[arg-type]


def test_a_fresh_rfc_manifest_is_default_deny(
    store: ManifestStore, tmp_path: Path
) -> None:
    initial = store.create_source_v2(rfc_draft(tmp_path))

    assert initial.cloud_egress_authorized is False
    assert initial.provider_route_binding is None
    assert initial.compliance_assessment is None


def test_a_successor_carries_the_recorded_authorization(
    store: ManifestStore, tmp_path: Path
) -> None:
    initial = store.create_source_v2(rfc_draft(tmp_path))
    route = binding()

    authorized = store.create_successor_v2(
        initial,
        assessment=assessment(
            provider_id=route.provider_id,
            endpoint_purpose=route.endpoint_purpose,
        ),
        route_binding=route,
        created_at=CREATED,
    )

    assert authorized.cloud_egress_authorized is True
    assert authorized.provider_route_binding == route
    assert authorized.compliance_assessment is not None
    assert authorized.predecessor_manifest_id == initial.manifest_id
    assert authorized.manifest_id != initial.manifest_id


def test_the_frozen_document_bytes_carry_forward_unchanged(
    store: ManifestStore, tmp_path: Path
) -> None:
    """Authorization is a decision about a document, not a new document.

    A successor that re-stated the hashes would let an authorization silently
    attach to different bytes than the ones assessed.
    """
    initial = store.create_source_v2(rfc_draft(tmp_path))
    route = binding()

    authorized = store.create_successor_v2(
        initial,
        assessment=assessment(
            provider_id=route.provider_id,
            endpoint_purpose=route.endpoint_purpose,
        ),
        route_binding=route,
        created_at=CREATED,
    )

    assert authorized.xml_sha256 == initial.xml_sha256
    assert authorized.text_sha256 == initial.text_sha256
    assert authorized.document_id == initial.document_id
    assert authorized.document_version == initial.document_version


def test_the_successor_is_readable_from_the_store(
    store: ManifestStore, tmp_path: Path
) -> None:
    """The enforcer authorizes on the stored copy, never the caller's."""
    initial = store.create_source_v2(rfc_draft(tmp_path))
    route = binding()
    authorized = store.create_successor_v2(
        initial,
        assessment=assessment(
            provider_id=route.provider_id,
            endpoint_purpose=route.endpoint_purpose,
        ),
        route_binding=route,
        created_at=CREATED,
    )

    assert store.read_source(authorized.manifest_id) == authorized


def test_a_v1_predecessor_is_refused(store: ManifestStore) -> None:
    """The two families do not share a base class, so neither shares a path."""
    v1 = store.create_source(SourceManifestDraft(**initial_fields()))
    route = binding()

    with pytest.raises(ValueError, match="v2 predecessor"):
        store.create_successor_v2(
            v1,  # type: ignore[arg-type]
            assessment=assessment(
                provider_id=route.provider_id,
                endpoint_purpose=route.endpoint_purpose,
            ),
            route_binding=route,
            created_at=CREATED,
        )


def test_a_predecessor_that_was_never_stored_is_refused(
    store: ManifestStore, tmp_path: Path
) -> None:
    """A caller can build a self-consistent manifest that no decision covers.

    Refused by the store failing to resolve it, which is an OSError rather than
    a ValueError — the manifest is not there, which is an I/O fact before it is
    a validation one. Either way nothing is authorized.
    """
    from specpilot.contracts.manifests import RfcSourceManifest

    # A populated store, so this exercises the identity guard rather than a
    # missing directory.
    store.create_source_v2(rfc_draft(tmp_path))
    unstored = RfcSourceManifest.from_draft(
        rfc_draft(tmp_path, document_version="2099-01")
    )
    route = binding()

    with pytest.raises((ValueError, OSError)):
        store.create_successor_v2(
            unstored,
            assessment=assessment(
                provider_id=route.provider_id,
                endpoint_purpose=route.endpoint_purpose,
            ),
            route_binding=route,
            created_at=CREATED,
        )


def test_an_authorized_rfc_manifest_can_build_an_egress_request(
    store: ManifestStore, tmp_path: Path
) -> None:
    """The point of the whole task: the RFC corpus can now reach the gate.

    Before this path existed `build_request` refused every RFC manifest, which
    was correct behaviour against a system that had no way to authorize one.
    """
    from specpilot.answer.chain import build_request
    from specpilot.answer.evidence import build_evidence_set
    from specpilot.contracts.rfc import RfcLimits
    from specpilot.corpus.clauses import ClauseLimits, iter_clause_texts

    initial = store.create_source_v2(rfc_draft(tmp_path))
    route = binding()
    authorized = store.create_successor_v2(
        initial,
        assessment=assessment(
            provider_id=route.provider_id,
            endpoint_purpose=route.endpoint_purpose,
        ),
        route_binding=route,
        created_at=CREATED,
    )
    xml = tmp_path / "corpus" / "rfc9999.xml"
    pairs = list(iter_clause_texts(xml, RfcLimits(), ClauseLimits()))[:1]
    evidence = build_evidence_set(pairs, corpus_manifest_id="c" * 64)

    request = build_request(
        "Which paragraph exists?",
        evidence,
        source_manifest=authorized,
        corpus_manifest_id="c" * 64,
        model_id="deepseek-v4-flash",
        evaluation_root_id="slice-1",
        run_id="run-1",
    )

    assert request.route == route
    assert request.version.document_id == "ietf-rfc-9999"
    assert request.stage.value == "evidence"


def test_an_unauthorized_rfc_manifest_still_cannot(
    store: ManifestStore, tmp_path: Path
) -> None:
    from specpilot.answer.chain import build_request
    from specpilot.answer.evidence import build_evidence_set
    from specpilot.contracts.rfc import RfcLimits
    from specpilot.corpus.clauses import ClauseLimits, iter_clause_texts

    initial = store.create_source_v2(rfc_draft(tmp_path))
    xml = tmp_path / "corpus" / "rfc9999.xml"
    pairs = list(iter_clause_texts(xml, RfcLimits(), ClauseLimits()))[:1]
    evidence = build_evidence_set(pairs, corpus_manifest_id="c" * 64)

    with pytest.raises(ValueError, match="no authorized provider route"):
        build_request(
            "Which paragraph exists?",
            evidence,
            source_manifest=initial,
            corpus_manifest_id="c" * 64,
            model_id="deepseek-v4-flash",
            evaluation_root_id="slice-1",
            run_id="run-1",
        )
