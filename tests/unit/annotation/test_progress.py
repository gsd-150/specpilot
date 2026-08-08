from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.annotation.progress import (
    L1_TARGET,
    L2_TARGET,
    build_progress,
    read_progress,
)
from specpilot.annotation.store import Annotation, AnnotationStore
from specpilot.contracts.annotation import GoldOriginEvent, L1Annotation, L2Annotation

L1_BASE: dict[str, object] = {
    "split": "dev",
    "question": "Which condition makes a stored response stale?",
    "direction": "clause_first",
    "content_origin": "mixed",
    "label_origin": "mixed",
    "document_id": "ietf-rfc-9111",
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


def test_the_report_counts_completed_items_against_the_section_8_1_targets(
    store: AnnotationStore,
) -> None:
    stored(
        store,
        l1("l1-dev-001"),
        l1("l1-dev-002"),
        l1("l1-locked-001", split="locked"),
        l2("l2-dev-001"),
    )

    report = build_progress(store.iter_records())

    assert report.l1.target_total == L1_TARGET.total == 40
    assert report.l1.target_dev == 15
    assert report.l1.target_locked == 25
    assert report.l1.completed_total == 3
    assert report.l1.completed_dev == 2
    assert report.l1.completed_locked == 1

    assert report.l2.target_total == L2_TARGET.total == 20
    assert report.l2.target_dev == 8
    assert report.l2.target_locked == 12
    assert report.l2.completed_total == 1
    assert report.l2.completed_dev == 1
    assert report.l2.completed_locked == 0


def test_an_amended_item_is_counted_once_not_once_per_file(
    store: AnnotationStore,
) -> None:
    """A pooling amendment is a successor file, not a second annotated item.

    Counting files would report the corpus as more annotated than it is, and
    the overcount grows with every completeness-audit pass.
    """
    (record,) = stored(store, l1("l1-dev-001"))
    assert record.annotation_id is not None
    store.amend(
        record.annotation_id,
        added_gold_clause_ids=("b" * 64,),
        added_gold_section_paths=("Freshness > Expiration",),
        added_gold_origins=(
            GoldOriginEvent(origin="bm25_retrieval", producer="bm25-pool-r0"),
        ),
        adjudication="checked the candidate against the frozen text; it is gold",
    )

    report = build_progress(store.iter_records())

    assert report.l1.completed_total == 1
    assert report.superseded_count == 1


def test_the_clause_first_split_is_measured_against_the_60_40_target(
    store: AnnotationStore,
) -> None:
    stored(
        store,
        l1("l1-dev-001", direction="clause_first"),
        l1("l1-dev-002", direction="clause_first"),
        l1("l1-dev-003", direction="clause_first"),
        l1("l1-dev-004", direction="scenario_first"),
        l1("l1-dev-005", direction="scenario_first"),
    )

    report = build_progress(store.iter_records())

    assert report.l1.clause_first == 3
    assert report.l1.scenario_first == 2
    assert report.l1.clause_first_share == pytest.approx(0.6)
    assert report.l1.clause_first_target == pytest.approx(0.6)


def test_l2_carries_no_direction_target_because_section_8_2_2_excludes_it(
    store: AnnotationStore,
) -> None:
    """Section 8.2.2 opens by scoping the ratio to L1.

    L2's input is a design description rather than a retrieval question, so
    reporting it against 60/40 would invent a requirement the plan disclaims.
    """
    stored(store, l2("l2-dev-001", direction="clause_first"))

    report = build_progress(store.iter_records())

    assert report.l2.clause_first == 1
    assert report.l2.clause_first_target is None


def test_the_unanswerable_floors_hold_per_split_not_only_in_total(
    store: AnnotationStore,
) -> None:
    stored(
        store,
        unanswerable("l1-dev-001"),
        unanswerable("l1-dev-002"),
        unanswerable("l1-dev-003"),
        unanswerable("l1-dev-004"),
        unanswerable("l1-locked-001", split="locked"),
        l1("l1-locked-002", split="locked"),
    )

    report = build_progress(store.iter_records())

    assert report.l1.unanswerable_dev == 4
    assert report.l1.unanswerable_floor_dev == 3
    assert report.l1.unanswerable_dev_met is True
    assert report.l1.unanswerable_locked == 1
    assert report.l1.unanswerable_floor_locked == 5
    assert report.l1.unanswerable_locked_met is False


def test_l2_progress_counts_provenance_and_expected_verdicts(
    store: AnnotationStore,
) -> None:
    stored(
        store,
        l2("l2-dev-001", expected_verdict="compliant"),
        l2("l2-dev-002", expected_verdict="insufficient_evidence"),
        l2("l2-dev-003", expected_verdict="violating"),
    )

    report = build_progress(store.iter_records())

    assert report.l2.provenance.content_origins == {"mixed": 3}
    assert report.l2.provenance.label_origins == {"mixed": 3}
    assert report.l2.provenance.gold_origins == {
        "human_source_review": 3,
        "model_proposal": 3,
    }
    assert report.l2.provenance.gold_origin_chains == {
        "model_proposal@openai-codex > human_source_review": 3,
    }
    assert report.l2.provenance.retrieval_originated_gold_items == 0
    assert report.l2.verdict_counts == {
        "compliant": 1,
        "insufficient_evidence": 1,
        "violating": 1,
    }
    assert report.l2.unanswerable_dev == 1
    assert report.l2.unanswerable_floor_dev == 2
    assert report.l2.unanswerable_dev_met is False
    assert report.l2.unanswerable_floor_locked == 4


def test_gold_origin_chain_keys_escape_producer_delimiters(
    store: AnnotationStore,
) -> None:
    stored(
        store,
        l1(
            "l1-dev-001",
            gold_origins=(
                {
                    "origin": "model_proposal",
                    "producer": "foo > human_source_review",
                },
            ),
        ),
        l1(
            "l1-dev-002",
            gold_origins=(
                {"origin": "model_proposal", "producer": "foo"},
                {"origin": "human_source_review"},
            ),
        ),
    )

    report = build_progress(store.iter_records())

    assert report.l1.provenance.gold_origin_chains == {
        "model_proposal@foo > human_source_review": 1,
        "model_proposal@foo%20%3E%20human_source_review": 1,
    }


def test_gold_free_items_do_not_emit_an_empty_origin_chain(
    store: AnnotationStore,
) -> None:
    stored(store, unanswerable("l1-dev-001"))

    report = build_progress(store.iter_records())

    assert report.l1.provenance.gold_origin_chains == {}


def test_l2_verdict_counts_include_zeroes_for_an_empty_set() -> None:
    report = build_progress(())

    assert report.l1.verdict_counts == {}
    assert report.l2.verdict_counts == {
        "compliant": 0,
        "insufficient_evidence": 0,
        "violating": 0,
    }


def test_l2_verdict_counts_include_zeroes_for_missing_verdicts(
    store: AnnotationStore,
) -> None:
    stored(store, l2("l2-dev-001", expected_verdict="compliant"))

    report = build_progress(store.iter_records())

    assert report.l2.verdict_counts == {
        "compliant": 1,
        "insufficient_evidence": 0,
        "violating": 0,
    }


def test_items_without_an_adjudication_record_are_counted(
    store: AnnotationStore,
) -> None:
    (first,) = stored(store, l1("l1-dev-001"))
    stored(store, l1("l1-dev-002"), l1("l1-dev-003"))
    assert first.annotation_id is not None
    store.amend(
        first.annotation_id,
        added_gold_clause_ids=("b" * 64,),
        added_gold_section_paths=("Freshness > Expiration",),
        added_gold_origins=(
            GoldOriginEvent(origin="bm25_retrieval", producer="bm25-pool-r0"),
        ),
        adjudication="checked the candidate against the frozen text; it is gold",
    )

    report = build_progress(store.iter_records())

    assert report.l1.completed_total == 3
    assert report.l1.awaiting_adjudication == 2


def test_gold_added_by_pooling_is_reported_apart_from_gold_found_independently(
    store: AnnotationStore,
) -> None:
    """Section 8.2.3 requires disclosing the pooled share of gold."""
    (record,) = stored(store, l1("l1-dev-001"))
    assert record.annotation_id is not None
    store.amend(
        record.annotation_id,
        added_gold_clause_ids=("b" * 64, "c" * 64),
        added_gold_section_paths=("Freshness > Expiration",),
        added_gold_origins=(
            GoldOriginEvent(origin="bm25_retrieval", producer="bm25-pool-r0"),
        ),
        adjudication="both candidates check out against the frozen text",
    )

    report = build_progress(store.iter_records())

    assert report.l1.gold_clauses == 3
    assert report.l1.pooled_gold_clauses == 2


def test_the_report_carries_no_question_key_point_or_section_text(
    store: AnnotationStore,
) -> None:
    stored(
        store,
        l1(
            "l1-dev-001",
            question="Which condition makes a stored response stale?",
            key_points=(
                {"point_id": "kp-1", "criterion": "names the freshness lifetime"},
            ),
            gold_section_paths=("Freshness > Calculating Freshness Lifetime",),
        ),
    )

    payload = json.dumps(build_progress(store.iter_records()).payload())

    assert "stored response" not in payload
    assert "freshness lifetime" not in payload
    assert "Calculating" not in payload


def test_reading_progress_from_a_directory_matches_reading_the_records(
    store: AnnotationStore,
    tmp_path: Path,
) -> None:
    stored(store, l1("l1-dev-001"), l2("l2-dev-001"))

    assert read_progress(tmp_path / "annotations") == build_progress(
        store.iter_records()
    )


def test_a_chain_whose_root_is_gone_is_refused_rather_than_read_as_a_root(
    store: AnnotationStore,
    tmp_path: Path,
) -> None:
    """Otherwise pooled gold would be reported as independently found gold."""
    (record,) = stored(store, l1("l1-dev-001"))
    assert record.annotation_id is not None
    store.amend(
        record.annotation_id,
        added_gold_clause_ids=("b" * 64,),
        added_gold_section_paths=("Freshness > Expiration",),
        added_gold_origins=(
            GoldOriginEvent(origin="bm25_retrieval", producer="bm25-pool-r0"),
        ),
        adjudication="checked the candidate against the frozen text; it is gold",
    )
    (tmp_path / "annotations" / f"{record.annotation_id}.json").unlink()

    with pytest.raises(ValueError, match="missing predecessor"):
        read_progress(tmp_path / "annotations")


def test_hybrid_and_pooling_origins_are_counted_from_the_final_head(
    store: AnnotationStore,
) -> None:
    (record,) = stored(
        store,
        l1(
            "l1-dev-001",
            gold_origins=(
                {"origin": "hybrid_retrieval", "producer": "hybrid-pool-r0"},
            ),
        ),
    )
    assert record.annotation_id is not None
    store.amend(
        record.annotation_id,
        added_gold_clause_ids=("b" * 64,),
        added_gold_section_paths=("Freshness > Expiration",),
        added_gold_origins=(GoldOriginEvent(origin="human_source_review"),),
        adjudication="checked the candidate against the frozen text; it is gold",
    )

    report = build_progress(store.iter_records())

    assert report.l1.provenance.retrieval_originated_gold_items == 1
    assert report.l1.provenance.gold_origin_chains == {
        "hybrid_retrieval@hybrid-pool-r0 > human_source_review": 1,
    }


def test_two_chains_claiming_one_item_are_refused_rather_than_counted_twice() -> None:
    from specpilot.manifests.canonical import canonical_sha256

    def addressed(record: L1Annotation) -> L1Annotation:
        return record.model_copy(update={"annotation_id": canonical_sha256(record)})

    records = (
        addressed(l1("l1-dev-001", direction="clause_first")),
        addressed(l1("l1-dev-001", direction="scenario_first")),
    )

    with pytest.raises(ValueError, match="more than one chain"):
        build_progress(records)


def test_a_tampered_annotation_file_is_refused_rather_than_counted(
    store: AnnotationStore,
    tmp_path: Path,
) -> None:
    """Progress is only meaningful over records that still hash to their name."""
    (record,) = stored(store, l1("l1-dev-001"))
    assert record.annotation_id is not None
    path = tmp_path / "annotations" / f"{record.annotation_id}.json"
    path.write_text(
        json.dumps({**json.loads(path.read_text()), "split": "locked"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        read_progress(tmp_path / "annotations")
