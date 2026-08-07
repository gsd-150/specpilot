from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.contracts.manifests import (
    RfcSourceManifestDraft,
    SourceManifestDraft,
)
from specpilot.manifests.canonical import canonical_json, canonical_sha256
from specpilot.manifests.store import ManifestStore, UnsupportedManifestVersionError

# Captured from the shipped v1 contract before v2 existed. If v2 work ever
# shifts a v1 field name, type, or order, these two constants change and this
# test fails — which is the point.
GOLDEN_V1_BYTES = 505
GOLDEN_V1_ID = "3acb2de7218a61ac0136691aba0b75b78feffa025636018ea2e6f2b5f1032953"

V1_FIELDS: dict[str, object] = {
    "document_id": "3gpp-ts-38.300",
    "document_version": "18.10.0",
    "download_url": "https://www.3gpp.org/x.zip",
    "archive_sha256": "a" * 64,
    "docx_sha256": "b" * 64,
    "downloaded_at": "2026-08-06T20:23:31Z",
    "created_at": "2026-08-07T04:55:01Z",
}
V2_FIELDS: dict[str, object] = {
    "document_id": "ietf-rfc-9110",
    "document_version": "2022-06",
    "download_url": "https://www.rfc-editor.org/rfc/rfc9110.xml",
    "text_sha256": "c" * 64,
    "xml_sha256": "d" * 64,
    "downloaded_at": "2026-08-07T16:00:00Z",
    "created_at": "2026-08-07T16:01:00Z",
}


def test_v1_canonical_bytes_and_id_are_unchanged_by_v2() -> None:
    draft = SourceManifestDraft(**V1_FIELDS)
    assert len(canonical_json(draft)) == GOLDEN_V1_BYTES
    assert canonical_sha256(draft) == GOLDEN_V1_ID


def test_v2_declares_its_own_schema_version() -> None:
    draft = RfcSourceManifestDraft(**V2_FIELDS)
    assert draft.schema_version == "source-manifest/v2"


def test_v2_is_default_deny_like_every_initial_manifest() -> None:
    draft = RfcSourceManifestDraft(**V2_FIELDS)
    assert draft.cloud_egress_authorized is False
    assert draft.predecessor_manifest_id is None
    assert draft.compliance_assessment is None
    assert draft.provider_route_binding is None


@pytest.mark.parametrize("field", ["archive_sha256", "docx_sha256"])
def test_v2_refuses_docx_shaped_fields(field: str) -> None:
    """An RFC has no archive and no DOCX; naming one would be a lie."""
    with pytest.raises(ValidationError):
        RfcSourceManifestDraft(**{**V2_FIELDS, field: "e" * 64})


@pytest.mark.parametrize("field", ["text_sha256", "xml_sha256"])
def test_v2_requires_both_document_hashes(field: str) -> None:
    fields = {k: v for k, v in V2_FIELDS.items() if k != field}
    with pytest.raises(ValidationError):
        RfcSourceManifestDraft(**fields)


def test_v2_id_differs_from_v1_carrying_the_same_values() -> None:
    """Schema version is part of the content, so the two can never collide."""
    shared = {
        "document_id": "shared-id",
        "document_version": "1",
        "download_url": "https://example.org/x",
        "downloaded_at": "2026-08-07T16:00:00Z",
        "created_at": "2026-08-07T16:01:00Z",
    }
    v1 = SourceManifestDraft(**shared, archive_sha256="f" * 64, docx_sha256="f" * 64)
    v2 = RfcSourceManifestDraft(**shared, text_sha256="f" * 64, xml_sha256="f" * 64)
    assert canonical_sha256(v1) != canonical_sha256(v2)


def test_v2_requires_https_and_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        RfcSourceManifestDraft(
            **{**V2_FIELDS, "download_url": "http://www.rfc-editor.org/rfc/rfc9110.xml"}
        )
    with pytest.raises(ValidationError):
        RfcSourceManifestDraft(**{**V2_FIELDS, "downloaded_at": "2026-08-07T16:00:00"})


def test_store_round_trips_v2_and_still_reads_v1(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path)
    v1 = store.create_source(SourceManifestDraft(**V1_FIELDS))
    v2 = store.create_source_v2(RfcSourceManifestDraft(**V2_FIELDS))

    assert store.read_source(v1.manifest_id).schema_version == "source-manifest/v1"
    restored = store.read_source(v2.manifest_id)
    assert restored.schema_version == "source-manifest/v2"
    assert restored == v2


def test_store_still_refuses_an_unknown_schema_version(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path)
    unknown = tmp_path / f"{'0' * 64}.json"
    unknown.write_text('{"schema_version":"source-manifest/v3"}', encoding="utf-8")
    unknown.chmod(0o600)
    with pytest.raises(UnsupportedManifestVersionError):
        store.read_source("0" * 64)
