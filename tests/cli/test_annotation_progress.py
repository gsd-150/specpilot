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
    "content_origin": "mixed",
    "label_origin": "mixed",
    "document_id": "ietf-rfc-9111",
    "document_version": "2022-06",
    "gold_clause_ids": ("a" * 64,),
    "gold_section_paths": ("Freshness > Calculating Freshness Lifetime",),
    "expected_refusal": False,
    "question_gold_jaccard": 0.12,
    "gold_origins": (
        {"origin": "model_proposal", "producer": "openai-codex"},
        {"origin": "human_source_review"},
    ),
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
    assert "independent_paths" not in payload["l1"]
    assert payload["l1"]["provenance"] == {
        "content_origins": {"mixed": 1},
        "label_origins": {"mixed": 1},
        "gold_origins": {"human_source_review": 1, "model_proposal": 1},
        "gold_origin_chains": {
            "model_proposal@openai-codex > human_source_review": 1,
        },
        "retrieval_originated_gold_items": 0,
    }
    assert payload["l1"]["verdict_counts"] == {}
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


SALT = "r1-2026-08"


def reviews(tmp_path: Path, *outcomes: str) -> Path:
    """A review store holding one decision per outcome named."""
    from specpilot.annotation.review import ReviewStore, deep_review_required
    from specpilot.contracts.annotation import ReviewDecision

    directory = tmp_path / "reviews"
    store = ReviewStore(directory)
    for index, outcome in enumerate(outcomes, start=1):
        item_id = f"l1-dev-{index:03d}"
        store.create(
            ReviewDecision(
                reviewed_annotation_id=(
                    None if outcome == "item_rejected" else f"{index:064d}"
                ),
                item_id=item_id,
                outcome=outcome,
                candidates_shown=4,
                chose_proposal=outcome == "accepted_as_proposed",
                reviewer_id="chunxue",
                proposal_producer="claude-opus-5",
                deep_reviewed=deep_review_required(item_id, rate=0.25, salt=SALT),
            )
        )
    return directory


def sample_flags() -> list[str]:
    return ["--deep-review-rate", "0.25", "--deep-review-salt", SALT]


def test_progress_without_a_review_store_reports_no_review_block(
    annotations: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The key's presence means reviews were asked for, so absence is not zero."""
    main(["annotation", "progress", "--annotation-dir", str(annotations)])

    assert "gold_review" not in json.loads(capsys.readouterr().out)


def test_the_review_block_stands_apart_from_the_set_progress(
    annotations: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§8.1: these describe the gold, not the system.

    Folded into `l1` they would read as a result about SpecPilot's answers.
    """
    store = reviews(tmp_path, "accepted_as_proposed", "gold_changed", "item_rejected")

    code = main(
        [
            "annotation", "progress",
            "--annotation-dir", str(annotations),
            "--review-dir", str(store),
            *sample_flags(),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["gold_review"]["measures"] == "gold_quality"
    assert "gold_review" not in payload["l1"]
    assert "gold_review" not in payload["l2"]
    assert payload["gold_review"]["proposal_acceptance_rate"] == pytest.approx(1 / 3)


def test_a_rejected_proposal_counts_in_the_denominator_not_toward_the_targets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rejection is evidence about the drafting, not progress toward §8.1."""
    empty = tmp_path / "annotations"
    empty.mkdir()
    store = reviews(tmp_path, "item_rejected", "item_rejected")

    code = main(
        [
            "annotation", "progress",
            "--annotation-dir", str(empty),
            "--review-dir", str(store),
            *sample_flags(),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["annotated_items"] == 0
    assert payload["l1"]["completed_total"] == 0
    assert payload["gold_review"]["rejected"] == 2
    assert payload["gold_review"]["proposal_acceptance_rate"] == 0.0


def test_reporting_reviews_without_declaring_the_sample_is_refused(
    annotations: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Coverage against an undeclared rate is a number about nothing."""
    store = reviews(tmp_path, "accepted_as_proposed")

    code = main(
        [
            "annotation", "progress",
            "--annotation-dir", str(annotations),
            "--review-dir", str(store),
        ]
    )

    captured = capsys.readouterr()
    assert code == 4
    assert captured.out == ""
    assert captured.err == "deep_review_sample_undeclared\n"


def test_a_review_store_that_is_not_there_is_refused(
    annotations: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reporting zero reviews for a missing store would read as zero reviews."""
    code = main(
        [
            "annotation", "progress",
            "--annotation-dir", str(annotations),
            "--review-dir", str(tmp_path / "absent"),
            *sample_flags(),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "review_dir_not_found\n"


def test_the_review_block_carries_no_question_or_criterion_text(
    annotations: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The decisions hold no prose, so neither can a report built from them."""
    store = reviews(tmp_path, "accepted_as_proposed", "gold_changed")

    main(
        [
            "annotation", "progress",
            "--annotation-dir", str(annotations),
            "--review-dir", str(store),
            *sample_flags(),
        ]
    )

    captured = capsys.readouterr()
    assert "stored response" not in captured.out
    assert "Calculating" not in captured.out


def test_v1_annotation_records_are_refused_as_unsupported_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = tmp_path / "annotations"
    directory.mkdir()
    (directory / f"{'a' * 64}.json").write_text(
        json.dumps({"schema_version": "annotation-l1/v1"}), encoding="utf-8"
    )

    code = main(["annotation", "progress", "--annotation-dir", str(directory)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unsupported_annotation_schema\n"
