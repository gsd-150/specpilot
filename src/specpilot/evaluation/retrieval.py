"""Section 8.4's retrieval metrics, computed over one evaluation split.

Three things this deliberately does not report, each because the evidence for it
does not exist rather than because it was forgotten:

**nDCG@10.** §8.4 rules it out by name: a single annotator's binary relevance
labels cannot support graded gain, so the number would be uninterpretable even
though it is trivial to compute.

**The unanswerable items' false-trigger rate.** §8.4 defines it as the share of
unanswerable items that clear a frozen confidence threshold *and enter the
deterministic answer path*. There is no frozen threshold and no answer path yet.
Reporting "retrieval returned something for an unanswerable question" instead
would be a different and much easier statistic wearing this one's name —
retrieval always returns something.

**The cross-reference expansion hit rate.** Its denominator is the items an
author marked as requiring a reference to be followed before all gold can be
found. No annotation field carries that mark, so the denominator does not exist.

Everything reported carries its numerator, its denominator, and the per-item
values behind it, because §8.4 requires that and because at N=12 a percentage
without its counts invites being read as a rate.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetrievedItem:
    """One evaluated question: its gold, and what a route ranked."""

    item_id: str
    gold_unit_ids: tuple[str, ...]
    ranked_unit_ids: tuple[str, ...]
    question_gold_jaccard: float

    def __post_init__(self) -> None:
        if not self.gold_unit_ids:
            # §8.4 scopes every retrieval metric to answerable items. An item
            # with no gold has no recall, and including it as a zero would
            # silently move the denominator.
            raise ValueError("a scored item must carry at least one gold clause")
        if len(set(self.gold_unit_ids)) != len(self.gold_unit_ids):
            raise ValueError("gold clauses must be distinct")
        if len(set(self.ranked_unit_ids)) != len(self.ranked_unit_ids):
            raise ValueError("a ranking may not repeat a unit")


@dataclass(frozen=True, slots=True)
class ItemScore:
    item_id: str
    gold_count: int
    found_at_k: int
    hit: bool
    all_required_hit: bool
    reciprocal_rank: float
    first_gold_rank: int | None
    question_gold_jaccard: float

    @property
    def recall(self) -> float:
        return self.found_at_k / self.gold_count

    def payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "gold_count": self.gold_count,
            "found_at_k": self.found_at_k,
            "recall": round(self.recall, 4),
            "hit": self.hit,
            "all_required_hit": self.all_required_hit,
            "first_gold_rank": self.first_gold_rank,
            "reciprocal_rank": round(self.reciprocal_rank, 4),
            "question_gold_jaccard": self.question_gold_jaccard,
        }


def score_item(item: RetrievedItem, *, k: int) -> ItemScore:
    """Score one item at cut-off `k`, with the rank taken over the full list.

    Recall and hit are measured inside the top `k` because that is what the
    answer chain would see. The reciprocal rank is measured over everything the
    route returned: capping it at `k` would report the same 0 for a gold clause
    ranked sixth and one the route never found at all.
    """
    if k < 1:
        raise ValueError("the cut-off must be at least one")
    gold = set(item.gold_unit_ids)
    top = item.ranked_unit_ids[:k]
    found = sum(1 for unit_id in top if unit_id in gold)

    first_rank: int | None = None
    for rank, unit_id in enumerate(item.ranked_unit_ids, start=1):
        if unit_id in gold:
            first_rank = rank
            break

    return ItemScore(
        item_id=item.item_id,
        gold_count=len(gold),
        found_at_k=found,
        hit=found > 0,
        all_required_hit=found == len(gold),
        reciprocal_rank=1.0 / first_rank if first_rank is not None else 0.0,
        first_gold_rank=first_rank,
        question_gold_jaccard=item.question_gold_jaccard,
    )


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    """Aggregates for one route at one cut-off, with the counts behind them."""

    route: str
    k: int
    item_count: int
    macro_recall: float | None
    hit_rate: float | None
    hit_count: int
    all_required_hit_rate: float | None
    all_required_hit_count: int
    multi_gold_item_count: int
    mrr: float | None
    gold_total: int
    gold_found: int
    items: tuple[ItemScore, ...]

    def payload(self, *, include_items: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {
            "route": self.route,
            "k": self.k,
            "item_count": self.item_count,
            "macro_recall": _rounded(self.macro_recall),
            "hit_rate": _rounded(self.hit_rate),
            "hit_count": self.hit_count,
            "all_required_hit_rate": _rounded(self.all_required_hit_rate),
            "all_required_hit_count": self.all_required_hit_count,
            "multi_gold_item_count": self.multi_gold_item_count,
            "mrr": _rounded(self.mrr),
            "gold_total": self.gold_total,
            "gold_found": self.gold_found,
        }
        if include_items:
            body["items"] = [score.payload() for score in self.items]
        return body


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def score_route(
    route: str, items: Sequence[RetrievedItem], *, k: int
) -> RouteMetrics:
    """Aggregate §8.4's retrieval figures for one route.

    Every rate is ``None`` rather than 0 over an empty denominator. Zero reads
    as "the route found nothing", which is a different claim from "there was
    nothing to find".
    """
    scores = tuple(score_item(item, k=k) for item in items)
    multi = tuple(score for score in scores if score.gold_count > 1)
    count = len(scores)
    return RouteMetrics(
        route=route,
        k=k,
        item_count=count,
        macro_recall=(
            statistics.fmean(score.recall for score in scores) if scores else None
        ),
        hit_rate=(sum(score.hit for score in scores) / count) if count else None,
        hit_count=sum(score.hit for score in scores),
        # Scoped to items that actually have more than one gold clause. Over the
        # whole set it would equal the hit rate wherever most items have one
        # gold, which is the case here, and would look like a second finding.
        all_required_hit_rate=(
            sum(score.all_required_hit for score in multi) / len(multi)
            if multi
            else None
        ),
        all_required_hit_count=sum(score.all_required_hit for score in multi),
        multi_gold_item_count=len(multi),
        mrr=(
            statistics.fmean(score.reciprocal_rank for score in scores)
            if scores
            else None
        ),
        gold_total=sum(score.gold_count for score in scores),
        gold_found=sum(score.found_at_k for score in scores),
        items=scores,
    )


@dataclass(frozen=True, slots=True)
class OverlapStrata:
    """§8.2.2's stratification by question-to-clause literal overlap.

    The boundary is the split's own median, computed from the overlap values
    alone and reported with the result. Picking it after seeing which cut made
    the metrics look better would be choosing the finding; the median is fixed
    by the questions before any route runs.
    """

    boundary: float
    low: RouteMetrics
    high: RouteMetrics

    def payload(self) -> dict[str, Any]:
        return {
            "boundary": round(self.boundary, 4),
            "boundary_rule": "median question_gold_jaccard of the scored items",
            "low_overlap": self.low.payload(include_items=False),
            "high_overlap": self.high.payload(include_items=False),
        }


def stratify_by_overlap(
    route: str, items: Sequence[RetrievedItem], *, k: int
) -> OverlapStrata | None:
    """Split at the median overlap and score each side, or ``None`` if it cannot.

    Returns ``None`` when either side would be empty — a stratum of zero items
    reports rates over nothing, and two strata where one is the whole set is not
    a stratification.
    """
    if len(items) < 2:
        return None
    boundary = statistics.median(item.question_gold_jaccard for item in items)
    low = [item for item in items if item.question_gold_jaccard < boundary]
    high = [item for item in items if item.question_gold_jaccard >= boundary]
    if not low or not high:
        return None
    return OverlapStrata(
        boundary=boundary,
        low=score_route(route, low, k=k),
        high=score_route(route, high, k=k),
    )


__all__ = [
    "ItemScore",
    "OverlapStrata",
    "RetrievedItem",
    "RouteMetrics",
    "score_item",
    "score_route",
    "stratify_by_overlap",
]
