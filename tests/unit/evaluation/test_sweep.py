from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.annotation.store import Annotation, AnnotationStore
from specpilot.contracts.annotation import (
    GoldOriginEvent,
    L1Annotation,
    L2Annotation,
    Split,
)
from specpilot.evaluation.sweep import (
    SweepLevel,
    SweepSelectionError,
    select_cases,
)

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
REVIEWED = (GoldOriginEvent(origin="human_source_review"),)


def l1(item_id: str, **overrides: object) -> L1Annotation:
    return L1Annotation(**{**L1_BASE, "item_id": item_id, **overrides})


def l2(item_id: str, **overrides: object) -> L2Annotation:
    return L2Annotation(**{**L1_BASE, **L2_EXTRA, "item_id": item_id, **overrides})


def unanswerable(item_id: str, **overrides: object) -> L1Annotation:
    return l1(
        item_id,
        expected_refusal=True,
        gold_clause_ids=(),
        gold_section_paths=(),
        question_gold_jaccard=None,
        gold_origins=(),
        **overrides,
    )


@pytest.fixture
def store(tmp_path: Path) -> AnnotationStore:
    return AnnotationStore(tmp_path / "annotations")


def stored(store: AnnotationStore, *records: Annotation) -> tuple[Annotation, ...]:
    return tuple(store.create(record) for record in records)


def test_selection_returns_one_level_and_one_split(store: AnnotationStore) -> None:
    stored(
        store,
        l1("l1-dev-001"),
        l1("l1-dev-002"),
        l1("l1-locked-001", split=Split.LOCKED),
        l2("l2-dev-001"),
    )

    cases = select_cases(
        store, level=SweepLevel.L1, split=Split.DEV, expected=2
    )

    assert [case.case_id for case in cases] == ["l1-dev-001", "l1-dev-002"]


def test_the_other_level_is_never_swept_in(store: AnnotationStore) -> None:
    """L2 extends L1, so an isinstance test written the wrong way round
    silently pools the two sets into one run."""
    stored(store, l1("l1-dev-001"), l2("l2-dev-001"))

    cases = select_cases(
        store, level=SweepLevel.L2, split=Split.DEV, expected=1
    )

    assert [case.case_id for case in cases] == ["l2-dev-001"]


def test_selection_reads_the_amended_record_not_the_original(
    store: AnnotationStore,
) -> None:
    """The defect this function exists to close.

    `tmp/dump_dev_items.py` and `tmp/run_l2_dev.sh` both select on
    `predecessor_annotation_id is None`, which is the chain *root* — the
    original record, not the current one. The real store holds 81 amendments
    over 61 items, and nine items carry gold the root does not have, six of
    them locked L2. A sweep that only reads `question` survives this because
    `amend` cannot change a question; anything that also reads gold does not.
    """
    (created,) = stored(store, l1("l1-dev-001"))
    assert created.annotation_id is not None
    store.amend(
        created.annotation_id,
        added_gold_clause_ids=("b" * 64,),
        added_gold_section_paths=("Caching > Freshness",),
        added_gold_origins=REVIEWED,
        adjudication="pooling found a second supporting clause",
    )

    (case,) = select_cases(store, level=SweepLevel.L1, split=Split.DEV, expected=1)

    assert case.gold_clause_ids == ("a" * 64, "b" * 64)


def test_a_retired_item_is_not_swept(store: AnnotationStore) -> None:
    stored(store, l1("l1-dev-001"))
    (retired,) = stored(store, l1("l1-dev-002"))
    assert retired.annotation_id is not None
    store.retire(
        retired.annotation_id, reason="ambiguous question", author_id="chunxue"
    )

    cases = select_cases(store, level=SweepLevel.L1, split=Split.DEV, expected=1)

    assert [case.case_id for case in cases] == ["l1-dev-001"]


def test_an_unanswerable_item_is_swept_only_when_asked_for(
    store: AnnotationStore,
) -> None:
    """The judge scores answered cases only, so the default sweep omits the
    expected-refusal items; the locked L1 run needs them and says so."""
    stored(store, l1("l1-dev-001"), unanswerable("l1-dev-002"))

    answerable = select_cases(
        store, level=SweepLevel.L1, split=Split.DEV, expected=1
    )
    everything = select_cases(
        store,
        level=SweepLevel.L1,
        split=Split.DEV,
        expected=2,
        include_unanswerable=True,
    )

    assert [case.case_id for case in answerable] == ["l1-dev-001"]
    assert [case.case_id for case in everything] == ["l1-dev-001", "l1-dev-002"]
    assert everything[1].expected_refusal is True


def test_a_count_that_differs_from_the_expected_one_refuses(
    store: AnnotationStore,
) -> None:
    """`tmp/run_l1_dev.sh` computes this count, prints it, and never checks it.

    On a one-shot locked run, a filter that matches eleven of twelve items
    prints `running 11 cases` and exits 0.
    """
    stored(store, l1("l1-dev-001"), l1("l1-dev-002"))

    with pytest.raises(SweepSelectionError, match="sweep_count_mismatch") as caught:
        select_cases(store, level=SweepLevel.L1, split=Split.DEV, expected=12)

    assert "2" in str(caught.value)
    assert "12" in str(caught.value)


def test_an_empty_selection_refuses_rather_than_sweeping_nothing(
    store: AnnotationStore,
) -> None:
    """`expected=0` is not a way to ask for a no-op run.

    A filter matching nothing is the failure that reads as success: the sweep
    prints a count, runs no case, and exits 0.
    """
    stored(store, l1("l1-dev-001"))

    with pytest.raises(SweepSelectionError, match="sweep_empty_selection"):
        select_cases(store, level=SweepLevel.L1, split=Split.LOCKED, expected=0)


def test_selection_is_ordered_by_item_id(store: AnnotationStore) -> None:
    """Two invocations must produce the same order, so the ledger's roots stay
    comparable across a re-run."""
    stored(store, l1("l1-dev-003"), l1("l1-dev-001"), l1("l1-dev-002"))

    cases = select_cases(store, level=SweepLevel.L1, split=Split.DEV, expected=3)

    assert [case.case_id for case in cases] == [
        "l1-dev-001",
        "l1-dev-002",
        "l1-dev-003",
    ]


def test_the_case_carries_the_document_it_belongs_to(
    store: AnnotationStore,
) -> None:
    """The L2 driver picks an authorized source manifest per document, and it
    did so by branching on a string it re-derived from the record each time."""
    stored(store, l2("l2-dev-001", document_id="ietf-rfc-9112"))

    (case,) = select_cases(store, level=SweepLevel.L2, split=Split.DEV, expected=1)

    assert case.document_id == "ietf-rfc-9112"
    assert case.question == L1_BASE["question"]


def test_a_split_is_never_defaulted() -> None:
    """§8.5 keeps the locked splits unread until W6. A split that can be
    omitted is one that gets omitted."""
    with pytest.raises(TypeError):
        select_cases(  # type: ignore[call-arg]
            AnnotationStore(Path("unused")), level=SweepLevel.L1, expected=1
        )
