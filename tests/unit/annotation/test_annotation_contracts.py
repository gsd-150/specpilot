from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.annotation.store import AnnotationStore, GoldRemovalError
from specpilot.contracts.annotation import (
    IndependentPath,
    KeyPoint,
    L1Annotation,
    L2Annotation,
    QuestionDirection,
    Verdict,
)

L1_FIELDS: dict[str, object] = {
    "item_id": "l1-dev-001",
    "split": "dev",
    "question": "Which condition makes a stored response stale?",
    "direction": "clause_first",
    "independent_path": "literal_search",
    "document_id": "ietf-rfc-9111",
    "document_version": "2022-06",
    "gold_clause_ids": ("a" * 64,),
    "gold_section_paths": ("Freshness > Calculating Freshness Lifetime",),
    "key_points": (
        {"point_id": "kp-1", "criterion": "names the freshness lifetime input"},
    ),
    "expected_refusal": False,
    "question_gold_jaccard": 0.12,
}
L2_EXTRA: dict[str, object] = {
    "claim_id": "l2-dev-001-c1",
    "expected_verdict": "violating",
    "proposed_verdict": "violating",
    "supports_verdict": True,
}


def l1(**overrides: object) -> L1Annotation:
    return L1Annotation(**{**L1_FIELDS, **overrides})


def l2(**overrides: object) -> L2Annotation:
    return L2Annotation(**{**L1_FIELDS, **L2_EXTRA, **overrides})


def test_an_l1_annotation_records_only_committable_fields() -> None:
    record = l1()

    assert set(record.model_dump()) == {
        "schema_version",
        "item_id",
        "split",
        "question",
        "direction",
        "independent_path",
        "document_id",
        "document_version",
        "gold_clause_ids",
        "gold_section_paths",
        "key_points",
        "expected_refusal",
        "question_gold_jaccard",
        "predecessor_annotation_id",
        "adjudications",
        "annotation_id",
    }


@pytest.mark.parametrize(
    "forbidden",
    ["quote", "clause_text", "excerpt", "gold_text", "source_text"],
)
def test_no_field_can_hold_clause_prose(forbidden: str) -> None:
    """The record's shape is the enforcement, not a reviewer's memory."""
    with pytest.raises(ValidationError):
        l1(**{forbidden: "Any stored response with a Cache-Control..."})


def test_the_independent_path_enum_cannot_name_retrieval() -> None:
    """Section 8.2.1's rule, expressed so the forbidden state is unrepresentable."""
    values = {member.value for member in IndependentPath}

    assert values == {
        "source_text_navigation",
        "literal_search",
        "cross_reference_trace",
        "terminology_index",
    }
    for banned in ("retrieval", "search_clauses", "dense", "bm25", "hybrid", "pooling"):
        assert banned not in values
    with pytest.raises(ValidationError):
        l1(independent_path="retrieval")


def test_a_key_point_is_a_criterion_not_a_sentence_of_clause_prose() -> None:
    """Key points may carry factual values but are bounded well under a clause."""
    long_prose = " ".join(f"word{n}" for n in range(200))
    with pytest.raises(ValidationError):
        KeyPoint(point_id="kp-1", criterion=long_prose)

    allowed = KeyPoint(
        point_id="kp-2",
        criterion="default timer is 5 seconds",
        factual_values=("5s",),
    )
    assert allowed.factual_values == ("5s",)


def test_an_unanswerable_item_carries_no_gold_and_no_overlap() -> None:
    record = l1(
        expected_refusal=True,
        gold_clause_ids=(),
        gold_section_paths=(),
        question_gold_jaccard=None,
    )

    assert record.expected_refusal is True
    assert record.gold_clause_ids == ()


def test_an_answerable_item_must_have_gold_and_an_overlap_figure() -> None:
    with pytest.raises(ValidationError):
        l1(gold_clause_ids=(), gold_section_paths=())
    with pytest.raises(ValidationError):
        l1(question_gold_jaccard=None)


def test_an_unanswerable_item_may_not_carry_gold() -> None:
    with pytest.raises(ValidationError):
        l1(expected_refusal=True, question_gold_jaccard=None)


def test_l2_keeps_task_gold_and_verifier_gold_in_separate_fields() -> None:
    """Section 8.1: the two label families must not substitute for one another."""
    record = l2()

    assert record.expected_verdict is Verdict.VIOLATING
    assert record.proposed_verdict is Verdict.VIOLATING
    assert record.supports_verdict is True
    assert "expected_verdict" in record.model_dump()
    assert "supports_verdict" in record.model_dump()


def test_l2_rejects_a_non_strict_supports_verdict() -> None:
    with pytest.raises(ValidationError):
        l2(supports_verdict=1)


def test_direction_is_one_of_the_two_mixed_question_paths() -> None:
    assert {member.value for member in QuestionDirection} == {
        "clause_first",
        "scenario_first",
    }


def test_the_store_round_trips_a_record(tmp_path: Path) -> None:
    store = AnnotationStore(tmp_path)

    stored = store.create(l1())

    assert store.read(stored.annotation_id) == stored
    assert len(stored.annotation_id) == 64


def test_an_amendment_may_add_gold_but_never_remove_it(tmp_path: Path) -> None:
    """Section 8.2.3: pooling proposes, the author adjudicates, gold never shrinks."""
    store = AnnotationStore(tmp_path)
    original = store.create(l1())

    amended = store.amend(
        original.annotation_id,
        added_gold_clause_ids=("b" * 64,),
        added_gold_section_paths=("Freshness > Heuristic Freshness",),
        adjudication="pooling candidate confirmed against the frozen text",
    )

    assert set(amended.gold_clause_ids) == {"a" * 64, "b" * 64}
    assert amended.predecessor_annotation_id == original.annotation_id
    assert amended.adjudications

    with pytest.raises(GoldRemovalError):
        store.amend(
            amended.annotation_id,
            added_gold_clause_ids=(),
            added_gold_section_paths=(),
            adjudication="removing gold",
            removed_gold_clause_ids=("a" * 64,),
        )


def test_one_item_id_owns_one_lineage(tmp_path: Path) -> None:
    """Re-creating the identical record is a replay; a different one is a clash."""
    store = AnnotationStore(tmp_path)
    first = store.create(l1())

    assert store.create(l1()).annotation_id == first.annotation_id

    with pytest.raises(ValueError):
        store.create(l1(question="A different question entirely?"))


def test_a_verdict_label_cannot_hide_in_a_key_points_factual_values() -> None:
    """§8.1 keeps task-level gold and scoring criteria in separate fields.

    A verdict inside a key point makes an answer scoreable for producing the
    word rather than the reasoning, and duplicates a value expected_verdict
    already owns — so the two could disagree.
    """
    for label in ("violating", "compliant", "Insufficient_Evidence"):
        with pytest.raises(ValidationError):
            KeyPoint(
                point_id="kp-1",
                criterion="names the rule",
                factual_values=(label,),
            )


def test_a_real_factual_value_is_still_accepted() -> None:
    point = KeyPoint(
        point_id="kp-1",
        criterion="names the default",
        factual_values=("5 octets", "RRC_CONNECTED"),
    )

    assert point.factual_values == ("5 octets", "RRC_CONNECTED")
