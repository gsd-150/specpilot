"""End-to-end smoke for the RFC corpus path.

The DOCX path has `test_fixture_pipeline`; this is its counterpart. Nothing
here reports a quality figure of any kind, and no real RFC text is used — the
fixtures are synthetic, exactly as the DOCX smoke uses a synthetic package.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits, RfcRejectionCode, UnsafeRfcError
from specpilot.ingestion.rfc import inspect_rfc_xml
from specpilot.manifests.store import ManifestStore
from specpilot.rfc.structure import extract_structure
from tests.helpers import rfc_factory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


def test_a_safe_rfc_passes_inspection_and_yields_structure(corpus: Path) -> None:
    path = rfc_factory.write_safe(corpus)

    inspection = inspect_rfc_xml(path, RfcLimits())
    structure = extract_structure(path, RfcLimits())

    assert inspection.root_tag == "rfc"
    assert structure.document_sha256 == inspection.document_sha256
    assert structure.section_count >= 1
    assert structure.cross_references
    assert structure.dangling_count == 0


def test_a_hostile_rfc_is_refused_before_any_structure_is_read(corpus: Path) -> None:
    path = rfc_factory.write(
        corpus, "hostile.xml", rfc_factory.EXTERNAL_ENTITY_XML
    )

    with pytest.raises(UnsafeRfcError) as raised:
        extract_structure(path, RfcLimits())

    assert raised.value.code is RfcRejectionCode.EXTERNAL_ENTITY


def test_a_verified_document_becomes_a_default_deny_v2_manifest(
    corpus: Path,
    tmp_path: Path,
) -> None:
    path = rfc_factory.write_safe(corpus)
    inspection = inspect_rfc_xml(path, RfcLimits())
    store = ManifestStore(tmp_path / "manifests")

    manifest = store.create_source_v2(
        RfcSourceManifestDraft(
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            text_url="https://www.rfc-editor.org/rfc/rfc9999.txt",
            xml_url="https://www.rfc-editor.org/rfc/rfc9999.xml",
            text_sha256="a" * 64,
            xml_sha256=inspection.document_sha256,
            downloaded_at="2026-08-07T09:00:00Z",
            created_at="2026-08-07T09:01:00Z",
        )
    )

    assert manifest.cloud_egress_authorized is False
    assert manifest.predecessor_manifest_id is None
    assert store.read_source(manifest.manifest_id) == manifest


def test_the_rfc_smoke_path_reports_no_quality_metric(corpus: Path) -> None:
    """W0's rule outlives the corpus change: this path measures no quality.

    Matched on word boundaries, not substrings. A SHA-256 digest reliably
    contains "f1" somewhere in its hex, and a scan that cannot tell that from
    an F1 score is a scan that gets switched off the first time it cries wolf.
    """
    path = rfc_factory.write_safe(corpus)

    rendered = f"{extract_structure(path, RfcLimits())!r}"

    found = re.findall(
        r"\b(recall|accuracy|f1|precision|ndcg|mrr|bleu|rouge)\b",
        rendered,
        re.IGNORECASE,
    )
    assert found == []
