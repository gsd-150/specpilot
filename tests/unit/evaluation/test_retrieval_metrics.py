"""§8.4's retrieval metrics, and the three it deliberately does not compute.

At N=12 a percentage is a description, not a rate, so every figure here has to
carry its numerator and denominator. The tests pin that, and pin the boundaries
where a metric would otherwise report a number about nothing.
"""

from __future__ import annotations

import pytest

from specpilot.evaluation.retrieval import (
    RetrievedItem,
    score_item,
    score_route,
    stratify_by_overlap,
)

GOLD = "g1"


def item(
    item_id: str = "l1-dev-001",
    *,
    gold: tuple[str, ...] = (GOLD,),
    ranked: tuple[str, ...] = (GOLD, "a", "b", "c", "d"),
    overlap: float = 0.2,
) -> RetrievedItem:
    return RetrievedItem(
        item_id=item_id,
        gold_unit_ids=gold,
        ranked_unit_ids=ranked,
        question_gold_jaccard=overlap,
    )


def test_recall_counts_gold_inside_the_cut_off() -> None:
    scored = score_item(
        item(gold=("g1", "g2"), ranked=("g1", "x", "y", "z", "w", "g2")), k=5
    )

    assert scored.found_at_k == 1
    assert scored.gold_count == 2
    assert scored.recall == 0.5
    assert scored.hit is True
    assert scored.all_required_hit is False


def test_the_reciprocal_rank_looks_past_the_cut_off() -> None:
    """A gold clause ranked sixth and one never found are different failures.

    Capping the rank at k would report 0 for both, and the difference is
    exactly what says whether a wider window would help.
    """
    just_missed = score_item(item(ranked=("a", "b", "c", "d", "e", GOLD)), k=5)
    never_found = score_item(item(ranked=("a", "b", "c", "d", "e")), k=5)

    assert just_missed.found_at_k == 0
    assert just_missed.first_gold_rank == 6
    assert just_missed.reciprocal_rank == pytest.approx(1 / 6)
    assert never_found.first_gold_rank is None
    assert never_found.reciprocal_rank == 0.0


def test_an_item_with_no_gold_cannot_be_scored() -> None:
    """§8.4 scopes every retrieval metric to answerable items.

    Scoring an unanswerable item as a zero would quietly move the denominator.
    """
    with pytest.raises(ValueError, match="gold"):
        RetrievedItem(
            item_id="l1-dev-013",
            gold_unit_ids=(),
            ranked_unit_ids=("a",),
            question_gold_jaccard=0.0,
        )


def test_a_ranking_may_not_repeat_a_unit() -> None:
    """A duplicate would count one clause twice toward recall."""
    with pytest.raises(ValueError, match="repeat"):
        item(ranked=(GOLD, GOLD))


def test_every_rate_carries_its_counts() -> None:
    metrics = score_route(
        "bm25",
        [item("a"), item("b", ranked=("x", "y", "z", "p", "q"))],
        k=5,
    )
    payload = metrics.payload()

    assert payload["item_count"] == 2
    assert payload["hit_count"] == 1
    assert payload["hit_rate"] == 0.5
    assert payload["gold_total"] == 2
    assert payload["gold_found"] == 1
    assert [row["item_id"] for row in payload["items"]] == ["a", "b"]


def test_all_required_hit_is_scoped_to_multi_gold_items() -> None:
    """Over the whole set it would equal the hit rate and look like a finding."""
    metrics = score_route(
        "bm25",
        [
            item("single"),
            item("double", gold=("g1", "g2"), ranked=("g1", "g2", "x", "y", "z")),
            item("partial", gold=("g1", "g3"), ranked=("g1", "x", "y", "z", "w")),
        ],
        k=5,
    )

    assert metrics.multi_gold_item_count == 2
    assert metrics.all_required_hit_count == 1
    assert metrics.all_required_hit_rate == 0.5


def test_rates_over_an_empty_denominator_are_absent_not_zero() -> None:
    """Zero reads as "the route found nothing", not "there was nothing to find"."""
    empty = score_route("bm25", [], k=5)

    assert empty.macro_recall is None
    assert empty.hit_rate is None
    assert empty.mrr is None
    assert empty.item_count == 0

    single_gold_only = score_route("bm25", [item()], k=5)
    assert single_gold_only.all_required_hit_rate is None
    assert single_gold_only.multi_gold_item_count == 0


def test_the_overlap_boundary_is_the_median_and_is_reported() -> None:
    """Chosen from the questions before any route runs, not from the outcome."""
    items = [
        item(f"i{n}", overlap=value)
        for n, value in enumerate([0.0, 0.1, 0.4, 0.9])
    ]

    strata = stratify_by_overlap("bm25", items, k=5)

    assert strata is not None
    assert strata.boundary == pytest.approx(0.25)
    assert strata.low.item_count == 2
    assert strata.high.item_count == 2
    assert strata.payload()["boundary_rule"].startswith("median")


def test_a_stratification_with_an_empty_side_is_refused() -> None:
    """Two strata where one is the whole set is not a stratification."""
    identical = [item(f"i{n}", overlap=0.3) for n in range(4)]

    assert stratify_by_overlap("bm25", identical, k=5) is None
    assert stratify_by_overlap("bm25", [item()], k=5) is None


def test_the_metrics_module_does_not_offer_ndcg() -> None:
    """§8.4 rules it out by name: binary single-annotator labels cannot grade.

    Trivial to compute and uninterpretable if computed, which is the exact
    combination that gets a number into a report by accident.
    """
    import specpilot.evaluation.retrieval as module

    assert not [name for name in dir(module) if "ndcg" in name.lower()]
