from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.annotation.adversarial import AdversarialGroupStore
from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.annotation import (
    AnnotationOrigin,
    GoldOrigin,
    GoldOriginEvent,
    Split,
    Verdict,
)
from specpilot.contracts.l2_adv import AdversarialDimension, AdversarialGroup

L1_BASE: dict[str, object] = {
    "split": "dev",
    "question": "Which condition makes a stored response stale?",
    "direction": "clause_first",
    "content_origin": "mixed",
    "label_origin": "mixed",
    "document_id": "ietf-rfc-9110",
    "document_version": "2022-06",
    "gold_clause_ids": ("a" * 64,),
    "gold_section_paths": ("Freshness > Calculating Freshness Lifetime",),
    "key_points": (
        {"point_id": "kp-1", "criterion": "names the freshness lifetime input"},
    ),
    "expected_refusal": False,
    "question_gold_jaccard": 0.12,
    "gold_origins": (
        {"origin": "model_proposal", "producer": "openai-codex"},
        {"origin": "human_source_review"},
    ),
}
L2_EXTRA: dict[str, object] = {
    "claim_id": "l2-dev-001-c1",
    "expected_verdict": "violating",
    "proposed_verdict": "violating",
    "supports_verdict": True,
}
_DIMENSIONS = tuple(AdversarialDimension)


@pytest.fixture
def annotations(tmp_path: Path) -> Path:
    from specpilot.contracts.annotation import L1Annotation, L2Annotation

    directory = tmp_path / "annotations"
    store = AnnotationStore(directory)
    for index in range(2):
        store.create(L1Annotation(**{**L1_BASE, "item_id": f"l1-dev-{index:03d}"}))
    store.create(
        L1Annotation(
            **{
                **L1_BASE,
                "item_id": "l1-dev-900",
                "expected_refusal": True,
                "gold_clause_ids": (),
                "gold_section_paths": (),
                "question_gold_jaccard": None,
                "gold_origins": (),
            }
        )
    )
    store.create(
        L2Annotation(**{**L1_BASE, **L2_EXTRA, "item_id": "l2-dev-000"})
    )
    return directory


@pytest.fixture
def groups(tmp_path: Path) -> Path:
    directory = tmp_path / "l2-adv"
    store = AdversarialGroupStore(directory)
    for index in range(2):
        tag = f"dev-{index:03d}"
        store.create(
            AdversarialGroup(
                group_id=f"adv-{tag}",
                family=f"family-{tag}",
                split=Split.DEV,
                dimension=_DIMENSIONS[index % len(_DIMENSIONS)],
                negative_claim_id=f"adv-{tag}-neg",
                negative_claim=f"the proxy must reject request {tag}",
                distractor_clause_ids=(f"{index:064x}",),
                positive_claim_id=f"adv-{tag}-pos",
                positive_claim=f"the origin server must reject request {tag}",
                supporting_clause_ids=(f"{index + 100:064x}",),
                proposed_verdict=Verdict.VIOLATING,
                content_origin=AnnotationOrigin.HUMAN,
                label_origin=AnnotationOrigin.HUMAN,
                construction_origins=(
                    GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),
                ),
            )
        )
    return directory


def lines(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines() if line.strip()]


def test_the_plan_lists_one_level_and_split(
    annotations: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "sweep", "plan",
                "--level", "l1", "--split", "dev", "--expected", "2",
                "--annotation-dir", str(annotations),
            ]
        )
        == 0
    )

    emitted = lines(capsys.readouterr().out)
    assert [item["case_id"] for item in emitted] == ["l1-dev-000", "l1-dev-001"]
    assert emitted[0]["document_id"] == "ietf-rfc-9110"


def test_the_unanswerable_items_are_included_only_when_asked_for(
    annotations: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "sweep", "plan",
                "--level", "l1", "--split", "dev", "--expected", "3",
                "--annotation-dir", str(annotations),
                "--include-unanswerable",
            ]
        )
        == 0
    )

    emitted = lines(capsys.readouterr().out)
    assert [item["case_id"] for item in emitted][-1] == "l1-dev-900"
    assert emitted[-1]["expected_refusal"] is True


def test_a_wrong_count_refuses_before_anything_is_sent(
    annotations: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The driver treats a non-zero exit as "nothing was sent", so the count
    has to fail here rather than partway through a live sweep."""
    assert (
        main(
            [
                "sweep", "plan",
                "--level", "l1", "--split", "dev", "--expected", "25",
                "--annotation-dir", str(annotations),
            ]
        )
        != 0
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sweep_count_mismatch" in captured.err


def test_an_empty_split_refuses(
    annotations: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "sweep", "plan",
                "--level", "l1", "--split", "locked", "--expected", "0",
                "--annotation-dir", str(annotations),
            ]
        )
        != 0
    )

    assert "sweep_empty_selection" in capsys.readouterr().err


def test_an_adversarial_split_plans_two_cases_per_group(
    groups: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "sweep", "plan",
                "--level", "l2-adv", "--split", "dev", "--expected", "2",
                "--group-dir", str(groups),
            ]
        )
        == 0
    )

    emitted = lines(capsys.readouterr().out)
    assert len(emitted) == 4
    assert [item["group_id"] for item in emitted] == [
        "adv-dev-000", "adv-dev-000", "adv-dev-001", "adv-dev-001",
    ]
    assert [item["role"] for item in emitted[:2]] == ["negative", "positive"]
    assert emitted[0]["expected_verdict"] == "insufficient_evidence"
    assert emitted[1]["expected_verdict"] == "violating"
    # A group spans documents by construction, so the record names none and the
    # driver requires --source-manifest instead of deriving one.
    assert emitted[0]["document_id"] is None


def test_the_adversarial_level_requires_a_group_directory(
    annotations: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "sweep", "plan",
                "--level", "l2-adv", "--split", "dev", "--expected", "2",
                "--annotation-dir", str(annotations),
            ]
        )
        != 0
    )

    assert "sweep_group_dir_required" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["--level", "--split", "--expected"])
def test_no_selector_may_be_omitted(annotations: Path, missing: str) -> None:
    """§8.5 keeps the locked splits unread until W6. Each of these three, left
    to a default, is a way to run a set nobody chose."""
    argv = [
        "sweep", "plan",
        "--level", "l1", "--split", "dev", "--expected", "2",
        "--annotation-dir", str(annotations),
    ]
    index = argv.index(missing)
    del argv[index : index + 2]

    with pytest.raises(SystemExit):
        main(argv)
