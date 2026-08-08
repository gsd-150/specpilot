from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import specpilot.cli as cli_module
from specpilot.cli import main
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.ingestion.rfc import RfcByteSnapshot
from specpilot.manifests.store import ManifestStore
from tests.helpers import rfc_factory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


def stored_manifest(
    tmp_path: Path,
    xml: Path,
    *,
    document_id: str = "ietf-rfc-9999",
    document_version: str = "2026-08",
) -> tuple[Path, str]:
    directory = tmp_path / "manifests"
    manifest = ManifestStore(directory).create_source_v2(
        RfcSourceManifestDraft(
            document_id=document_id,
            document_version=document_version,
            text_url="https://www.rfc-editor.org/rfc/rfc9999.txt",
            xml_url="https://www.rfc-editor.org/rfc/rfc9999.xml",
            text_sha256="a" * 64,
            xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest(),
            downloaded_at="2026-08-07T09:00:00Z",
            created_at="2026-08-07T09:01:00Z",
        )
    )
    return directory, manifest.manifest_id


def test_one_command_parses_one_specification(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """W1's hard gate from product plan section 12."""
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)

    code = main(
        [
            "corpus",
            "parse",
            "--manifest",
            manifest_id,
            "--manifest-dir",
            str(directory),
            "--xml",
            str(xml),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "parsed"
    assert payload["document_id"] == "ietf-rfc-9999"
    assert payload["section_count"] >= 1
    assert payload["clause_count"] >= 1
    assert payload["cross_reference_count"] >= 1
    assert payload["dangling_cross_references"] == 0


def test_the_parse_output_carries_no_clause_text(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)

    main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert "This paragraph exists" not in captured.out
    assert "Introduction" not in captured.out


def test_a_document_that_is_not_the_frozen_one_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The manifest froze specific bytes; anything else is a different document."""
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)
    xml.write_text(
        rfc_factory.SAFE_RFC_XML.replace("Introduction", "Introduction "),
        encoding="utf-8",
    )

    code = main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_hash_mismatch\n"


def test_corpus_parse_keeps_using_the_verified_snapshot_after_a_path_swap(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing the pathname after verification cannot change the corpus."""
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)
    replacement_xml = rfc_factory.SAFE_RFC_XML.replace(
        '      <section anchor="scope"',
        '      <t>A replacement-only paragraph.</t>\n'
        '      <section anchor="scope"',
    )
    original_read = cli_module.read_rfc_snapshot

    def read_then_replace(path: Path, limits: RfcLimits) -> RfcByteSnapshot:
        snapshot = original_read(path, limits)
        path.write_text(replacement_xml, encoding="utf-8")
        return snapshot

    monkeypatch.setattr(cli_module, "read_rfc_snapshot", read_then_replace)

    code = main(
        [
            "corpus",
            "parse",
            "--manifest",
            manifest_id,
            "--manifest-dir",
            str(directory),
            "--xml",
            str(xml),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["clause_count"] == 2


def test_hash_mismatch_precedes_an_invalid_document_identity(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)
    xml.write_text(
        rfc_factory.SAFE_RFC_XML.replace(
            '<date month="08" year="2026"/>',
            "",
        ),
        encoding="utf-8",
    )

    code = main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_hash_mismatch\n"


def test_hash_mismatch_precedes_an_unsupported_rfcxml_version(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)
    xml.write_text(
        rfc_factory.SAFE_RFC_XML.replace('version="3"', 'version="4"'),
        encoding="utf-8",
    )

    code = main(
        [
            "corpus",
            "parse",
            "--manifest",
            manifest_id,
            "--manifest-dir",
            str(directory),
            "--xml",
            str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_hash_mismatch\n"


@pytest.mark.parametrize(
    "grammar_version",
    [None, "4"],
)
def test_matching_unsupported_rfcxml_grammar_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    grammar_version: str | None,
) -> None:
    attribute = ' version="3"'
    replacement = "" if grammar_version is None else f' version="{grammar_version}"'
    xml = rfc_factory.write(
        corpus,
        "unsupported-grammar.xml",
        rfc_factory.SAFE_RFC_XML.replace(attribute, replacement),
    )
    directory, manifest_id = stored_manifest(tmp_path, xml)

    code = main(
        [
            "corpus",
            "parse",
            "--manifest",
            manifest_id,
            "--manifest-dir",
            str(directory),
            "--xml",
            str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unsupported_rfcxml_version\n"


def test_manifest_version_must_name_the_xml_publication_version(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(
        tmp_path,
        xml,
        document_version="2025-01",
    )

    code = main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_version_mismatch\n"


def test_manifest_document_id_must_name_the_xml_rfc_number(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(
        tmp_path,
        xml,
        document_id="ietf-rfc-9112",
    )

    code = main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_id_mismatch\n"


def test_a_source_without_a_publication_identity_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write(
        corpus,
        "missing-date.xml",
        rfc_factory.SAFE_RFC_XML.replace(
            '<date month="08" year="2026"/>',
            "",
        ),
    )
    directory, manifest_id = stored_manifest(tmp_path, xml)

    code = main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "invalid_document_identity\n"


def test_a_hostile_document_is_refused_with_its_boundary_code(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write(
        corpus, "hostile.xml", rfc_factory.EXTERNAL_ENTITY_XML
    )
    directory, manifest_id = stored_manifest(tmp_path, xml)

    code = main(
        [
            "corpus", "parse",
            "--manifest", manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "external_entity\n"


def test_a_v1_manifest_cannot_stand_in_for_an_rfc_source(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A DOCX-shaped manifest describes a different corpus entirely."""
    from specpilot.contracts.manifests import SourceManifestDraft

    xml = rfc_factory.write_safe(corpus)
    directory = tmp_path / "manifests"
    v1 = ManifestStore(directory).create_source(
        SourceManifestDraft(
            document_id="3gpp-ts-38.300",
            document_version="18.10.0",
            download_url="https://www.3gpp.org/x.zip",
            archive_sha256="a" * 64,
            docx_sha256="b" * 64,
            downloaded_at="2026-08-06T20:23:31Z",
            created_at="2026-08-07T04:55:01Z",
        )
    )

    code = main(
        [
            "corpus", "parse",
            "--manifest", v1.manifest_id,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unsupported_manifest_version\n"


def test_a_missing_manifest_refuses_without_parsing(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = rfc_factory.write_safe(corpus)
    directory, _ = stored_manifest(tmp_path, xml)

    code = main(
        [
            "corpus", "parse",
            "--manifest", "0" * 64,
            "--manifest-dir", str(directory),
            "--xml", str(xml),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "manifest_not_found\n"
