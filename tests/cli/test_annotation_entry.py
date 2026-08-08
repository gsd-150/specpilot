from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.manifests.store import ManifestStore
from tests.helpers import rfc_factory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


def stored_manifest(tmp_path: Path, xml: Path) -> tuple[Path, str]:
    directory = tmp_path / "manifests"
    manifest = ManifestStore(directory).create_source_v2(
        RfcSourceManifestDraft(
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            text_url="https://www.rfc-editor.org/rfc/rfc9999.txt",
            xml_url="https://www.rfc-editor.org/rfc/rfc9999.xml",
            text_sha256="a" * 64,
            xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest(),
            downloaded_at="2026-08-07T09:00:00Z",
            created_at="2026-08-07T09:01:00Z",
        )
    )
    return directory, manifest.manifest_id


def source(tmp_path: Path, corpus: Path) -> list[str]:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)
    return [
        "--manifest", manifest_id,
        "--manifest-dir", str(directory),
        "--xml", str(xml),
    ]


def test_the_clause_index_gives_ids_a_gold_field_can_reference(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["corpus", "clauses", *source(tmp_path, corpus)])

    assert code == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows
    for row in rows:
        assert len(row["clause_id"]) == 64
        assert set(row) == {
            "clause_id",
            "section_number",
            "section_path",
            "ordinal",
            "anchor",
            "word_count",
            "byte_count",
        }


def test_the_clause_index_carries_no_clause_text(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["corpus", "clauses", *source(tmp_path, corpus)])

    assert "This paragraph exists" not in capsys.readouterr().out


def test_the_clause_index_can_be_narrowed_to_one_section(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = source(tmp_path, corpus)
    main(["corpus", "clauses", *arguments])
    everything = capsys.readouterr().out.splitlines()

    main(["corpus", "clauses", *arguments, "--section", "1.1"])
    narrowed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert 0 < len(narrowed) < len(everything)
    assert all(row["section_number"] == "1.1" for row in narrowed)


def test_a_document_that_is_not_the_frozen_one_has_no_clause_index(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = source(tmp_path, corpus)
    Path(arguments[-1]).write_text(
        rfc_factory.SAFE_RFC_XML.replace("Introduction", "Introduction "),
        encoding="utf-8",
    )

    code = main(["corpus", "clauses", *arguments])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_hash_mismatch\n"


def test_the_overlap_command_reports_the_figure_the_contract_requires(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = source(tmp_path, corpus)
    main(["corpus", "clauses", *arguments])
    first = json.loads(capsys.readouterr().out.splitlines()[0])

    code = main(
        [
            "corpus", "overlap", *arguments,
            "--clause-id", first["clause_id"],
            "--question", "Which paragraph exists in this document?",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert 0.0 < payload["question_gold_jaccard"] <= 1.0
    assert payload["gold_clause_count"] == 1


def test_the_overlap_command_refuses_a_clause_that_is_not_in_the_document(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "corpus", "overlap", *source(tmp_path, corpus),
            "--clause-id", "f" * 64,
            "--question", "anything",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unknown_clause_id\n"


def test_the_template_is_a_record_the_contract_would_reject_until_filled_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A template that validates as-is invites twenty-three copies of itself."""
    code = main(["annotation", "template", "--level", "l1"])

    assert code == 0
    template = json.loads(capsys.readouterr().out)
    assert template["schema_version"] == "annotation-l1/v1"
    assert "question" in template
    assert "gold_clause_ids" in template


def test_adding_a_valid_record_stores_it_and_reports_its_id(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = tmp_path / "l1-dev-001.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "annotation-l1/v1",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": "Which condition makes a stored response stale?",
                "direction": "clause_first",
                "independent_path": "literal_search",
                "document_id": "ietf-rfc-9111",
                "document_version": "2022-06",
                "gold_clause_ids": ["a" * 64],
                "gold_section_paths": ["Freshness"],
                "expected_refusal": False,
                "question_gold_jaccard": 0.12,
            }
        ),
        encoding="utf-8",
    )
    directory = tmp_path / "annotations"

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "stored"
    assert len(payload["annotation_id"]) == 64
    assert AnnotationStore(directory).read(payload["annotation_id"]).item_id == (
        "l1-dev-001"
    )


def test_adding_a_record_the_contract_rejects_stores_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An answerable item without gold is the mistake this catches."""
    record = tmp_path / "bad.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "annotation-l1/v1",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": "Which condition makes a stored response stale?",
                "direction": "clause_first",
                "independent_path": "literal_search",
                "document_id": "ietf-rfc-9111",
                "document_version": "2022-06",
                "expected_refusal": False,
            }
        ),
        encoding="utf-8",
    )
    directory = tmp_path / "annotations"

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "invalid_annotation_record\n"
    assert not directory.exists()


def test_a_record_naming_the_retriever_as_its_source_cannot_be_added(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Section 8.2.1's rule holds at the entry point, not only in the type."""
    record = tmp_path / "pooled.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "annotation-l1/v1",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": "Which condition makes a stored response stale?",
                "direction": "clause_first",
                "independent_path": "search_clauses",
                "document_id": "ietf-rfc-9111",
                "document_version": "2022-06",
                "gold_clause_ids": ["a" * 64],
                "expected_refusal": False,
                "question_gold_jaccard": 0.12,
            }
        ),
        encoding="utf-8",
    )

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "invalid_annotation_record\n"
