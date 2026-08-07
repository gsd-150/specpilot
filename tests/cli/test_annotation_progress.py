from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.annotation import L1Annotation

RECORD: dict[str, object] = {
    "item_id": "l1-dev-001",
    "split": "dev",
    "question": "Which condition makes a stored response stale?",
    "direction": "clause_first",
    "independent_path": "literal_search",
    "document_id": "ietf-rfc-9111",
    "document_version": "2022-06",
    "gold_clause_ids": ("a" * 64,),
    "gold_section_paths": ("Freshness > Calculating Freshness Lifetime",),
    "expected_refusal": False,
    "question_gold_jaccard": 0.12,
}


@pytest.fixture
def annotations(tmp_path: Path) -> Path:
    directory = tmp_path / "annotations"
    AnnotationStore(directory).create(L1Annotation(**RECORD))
    return directory


def test_progress_is_checkable_with_one_command(
    annotations: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of W1's gate from product plan section 12."""
    code = main(["annotation", "progress", "--annotation-dir", str(annotations)])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "reported"
    assert payload["l1"]["completed_total"] == 1
    assert payload["l1"]["target_total"] == 40
    assert payload["l1"]["independent_paths"] == {"literal_search": 1}
    assert payload["l2"]["completed_total"] == 0


def test_the_progress_output_carries_no_question_text(
    annotations: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["annotation", "progress", "--annotation-dir", str(annotations)])

    captured = capsys.readouterr()
    assert "stored response" not in captured.out
    assert "Calculating" not in captured.out


def test_a_missing_annotation_directory_refuses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["annotation", "progress", "--annotation-dir", str(tmp_path / "absent")]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "annotation_dir_not_found\n"


def test_a_tampered_record_refuses_instead_of_reporting_a_count(
    annotations: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = next(annotations.glob("*.json"))
    path.write_text(
        json.dumps({**json.loads(path.read_text()), "split": "locked"}),
        encoding="utf-8",
    )

    code = main(["annotation", "progress", "--annotation-dir", str(annotations)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "invalid_annotation_record\n"
