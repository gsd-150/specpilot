"""Countable annotation progress, reported without any of the prose counted.

Three things here are easy to get wrong in a way that overstates progress, and
each is decided once, in code, rather than remembered.

**An item is not a file.** Section 8.2.3's completeness audit writes a
successor rather than mutating a record, so one annotated item can own a chain
of files. Counting files would report the corpus as further along than it is,
and the overcount would grow with every audit pass. Progress counts chain
heads, keyed by ``item_id``.

**L1 and L2 express "unanswerable" differently.** Section 8.1 sets the floors
as L1 dev 3 / test 5 and L2 dev 2 / test 4, but L2's comes from its fixed
verdict distribution — 3/3/2 and 4/4/4. An L2 item whose gold verdict is
``insufficient_evidence`` is answerable in L1's sense: the system should return
that verdict, not refuse. So L1 counts refusals and L2 counts verdicts, and
neither is substituted for the other.

**The 60/40 direction ratio is L1's alone.** Section 8.2.2 opens by scoping it
that way, because L2's input is a design description rather than a retrieval
question. L2's direction counts are still reported as facts; its target is
``None``, which says the ratio does not apply rather than silently omitting it.

Nothing here reads a question, a key point, or a section title. The report is
counts and enum labels, so publishing it can never publish the corpus.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from specpilot.annotation.review import ReviewStatistics
from specpilot.annotation.store import Annotation, AnnotationStore
from specpilot.contracts.annotation import (
    GoldOrigin,
    L2Annotation,
    QuestionDirection,
    Split,
    Verdict,
)


@dataclass(frozen=True, slots=True)
class SetTarget:
    """One evaluation set's section 8.1 volumes and section 8.2.2 ratio."""

    total: int
    dev: int
    locked: int
    unanswerable_dev: int
    unanswerable_locked: int
    clause_first_share: float | None


@dataclass(frozen=True, slots=True)
class ProvenanceProgress:
    content_origins: dict[str, int]
    label_origins: dict[str, int]
    gold_origins: dict[str, int]
    gold_origin_chains: dict[str, int]
    retrieval_originated_gold_items: int


L1_TARGET = SetTarget(
    total=40,
    dev=15,
    locked=25,
    unanswerable_dev=3,
    unanswerable_locked=5,
    clause_first_share=0.6,
)
L2_TARGET = SetTarget(
    total=20,
    dev=8,
    locked=12,
    unanswerable_dev=2,
    unanswerable_locked=4,
    clause_first_share=None,
)


@dataclass(frozen=True, slots=True)
class SetProgress:
    set_name: str
    target_total: int
    target_dev: int
    target_locked: int
    completed_total: int
    completed_dev: int
    completed_locked: int
    unanswerable_dev: int
    unanswerable_locked: int
    unanswerable_floor_dev: int
    unanswerable_floor_locked: int
    clause_first: int
    scenario_first: int
    clause_first_target: float | None
    provenance: ProvenanceProgress
    verdict_counts: dict[str, int]
    awaiting_adjudication: int
    gold_clauses: int
    pooled_gold_clauses: int

    @property
    def unanswerable_dev_met(self) -> bool:
        return self.unanswerable_dev >= self.unanswerable_floor_dev

    @property
    def unanswerable_locked_met(self) -> bool:
        return self.unanswerable_locked >= self.unanswerable_floor_locked

    @property
    def clause_first_share(self) -> float | None:
        """The observed share, or ``None`` when nothing has been annotated yet.

        Zero would read as "no clause-first questions", which is a different
        statement from "no questions".
        """
        directed = self.clause_first + self.scenario_first
        return self.clause_first / directed if directed else None

    def payload(self) -> dict[str, Any]:
        return {
            "target_total": self.target_total,
            "target_dev": self.target_dev,
            "target_locked": self.target_locked,
            "completed_total": self.completed_total,
            "completed_dev": self.completed_dev,
            "completed_locked": self.completed_locked,
            "unanswerable_dev": self.unanswerable_dev,
            "unanswerable_locked": self.unanswerable_locked,
            "unanswerable_floor_dev": self.unanswerable_floor_dev,
            "unanswerable_floor_locked": self.unanswerable_floor_locked,
            "unanswerable_dev_met": self.unanswerable_dev_met,
            "unanswerable_locked_met": self.unanswerable_locked_met,
            "clause_first": self.clause_first,
            "scenario_first": self.scenario_first,
            "clause_first_share": self.clause_first_share,
            "clause_first_target": self.clause_first_target,
            "provenance": {
                "content_origins": self.provenance.content_origins,
                "label_origins": self.provenance.label_origins,
                "gold_origins": self.provenance.gold_origins,
                "gold_origin_chains": self.provenance.gold_origin_chains,
                "retrieval_originated_gold_items": (
                    self.provenance.retrieval_originated_gold_items
                ),
            },
            "verdict_counts": self.verdict_counts,
            "awaiting_adjudication": self.awaiting_adjudication,
            "gold_clauses": self.gold_clauses,
            "pooled_gold_clauses": self.pooled_gold_clauses,
        }


@dataclass(frozen=True, slots=True)
class PoolingRunProgress:
    """One audit run's own counts."""

    run_id: str
    registered_items: int
    adjudicated_items: int
    gold_complete: int
    gold_extended: int
    blocked: int
    added_gold_clauses: int
    sealed: bool

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "registered_items": self.registered_items,
            "adjudicated_items": self.adjudicated_items,
            "gold_complete": self.gold_complete,
            "gold_extended": self.gold_extended,
            "blocked": self.blocked,
            "added_gold_clauses": self.added_gold_clauses,
            "sealed": self.sealed,
        }


@dataclass(frozen=True, slots=True)
class PoolingAuditProgress:
    """The audit across every run, counted per item rather than per decision.

    There used to be a scalar `run_id` and a scalar `sealed`, and this command
    refused outright on finding more than one run. That made the audit a
    one-shot: growing the gold set needs a second run to cover the new items,
    and registering one broke progress. The names went with the assumption —
    with several runs there is no "the run" for a `run_id` field to hold.

    **Counted per item, because runs overlap.** A second run over a grown gold
    set re-registers everything the first one covered, so summing decisions
    would count those items twice. An item is audited when some sealed run
    holds an applied head decision for it.
    """

    registered_items: int
    adjudicated_items: int
    gold_complete: int
    gold_extended: int
    blocked: int
    added_gold_clauses: int
    fully_sealed: bool
    runs: tuple[PoolingRunProgress, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "registered_items": self.registered_items,
            "adjudicated_items": self.adjudicated_items,
            "gold_complete": self.gold_complete,
            "gold_extended": self.gold_extended,
            "blocked": self.blocked,
            "added_gold_clauses": self.added_gold_clauses,
            "fully_sealed": self.fully_sealed,
            "runs": [run.payload() for run in self.runs],
        }


@dataclass(frozen=True, slots=True)
class ProgressReport:
    l1: SetProgress
    l2: SetProgress
    annotated_items: int
    superseded_count: int
    retired_item_ids: tuple[str, ...] = ()
    gold_review: ReviewStatistics | None = None
    pooling_audit: PoolingAuditProgress | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "reported",
            "annotated_items": self.annotated_items,
            "superseded_count": self.superseded_count,
            "retired_item_ids": list(self.retired_item_ids),
            "l1": self.l1.payload(),
            "l2": self.l2.payload(),
        }
        if self.gold_review is not None:
            # Its own key, deliberately outside `l1` and `l2`. These count how
            # good the gold is, not how well the system answers against it, and
            # a reader who finds an acceptance rate inside a set's progress
            # block will read it as the second thing. Absent rather than zeroed
            # when reviews were not asked for: an empty block reads as "no
            # reviews", which is a different claim.
            payload["gold_review"] = self.gold_review.payload()
        if self.pooling_audit is not None:
            payload["pooling_audit"] = self.pooling_audit.payload()
        return payload


def _unanswerable(record: Annotation) -> bool:
    if isinstance(record, L2Annotation):
        return record.expected_verdict is Verdict.INSUFFICIENT_EVIDENCE
    return record.expected_refusal


def _chain_root(head: Annotation, by_id: dict[str, Annotation]) -> Annotation:
    """Walk back to the record the author wrote before any audit touched it."""
    current = head
    seen: set[str] = set()
    while current.predecessor_annotation_id is not None:
        predecessor_id = current.predecessor_annotation_id
        if predecessor_id in seen:
            raise ValueError("annotation chain is cyclic")
        seen.add(predecessor_id)
        previous = by_id.get(predecessor_id)
        if previous is None:
            # A chain missing its root cannot be told apart from an item whose
            # gold was found independently, which is exactly the distinction
            # section 8.2.3 requires the report to disclose.
            raise ValueError("annotation chain references a missing predecessor")
        current = previous
    return current


def _set_progress(
    set_name: str,
    target: SetTarget,
    heads: list[tuple[Annotation, Annotation]],
) -> SetProgress:
    content_origins: Counter[str] = Counter()
    label_origins: Counter[str] = Counter()
    gold_origins: Counter[str] = Counter()
    gold_origin_chains: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    directions: Counter[QuestionDirection] = Counter()
    unanswerable = Counter[Split]()
    completed = Counter[Split]()
    gold = 0
    pooled = 0
    awaiting = 0
    retrieval_originated_gold_items = 0
    retrieval_origins = {
        GoldOrigin.SEARCH_CLAUSES,
        GoldOrigin.DENSE_RETRIEVAL,
        GoldOrigin.BM25_RETRIEVAL,
        GoldOrigin.HYBRID_RETRIEVAL,
    }

    for head, root in heads:
        completed[head.split] += 1
        content_origins[head.content_origin.value] += 1
        label_origins[head.label_origin.value] += 1
        for event in head.gold_origins:
            gold_origins[event.origin.value] += 1
        if head.gold_origins:
            gold_origin_chains[
                " > ".join(
                    f"{event.origin.value}@{quote(event.producer, safe='-._~')}"
                    if event.producer is not None
                    else event.origin.value
                    for event in head.gold_origins
                )
            ] += 1
        if any(event.origin in retrieval_origins for event in head.gold_origins):
            retrieval_originated_gold_items += 1
        if isinstance(head, L2Annotation):
            verdict_counts[head.expected_verdict.value] += 1
        directions[head.direction] += 1
        if _unanswerable(head):
            unanswerable[head.split] += 1
        if not head.adjudications:
            awaiting += 1
        head_gold = set(head.gold_clause_ids)
        gold += len(head_gold)
        pooled += len(head_gold - set(root.gold_clause_ids))

    if set_name == "l2":
        for verdict in Verdict:
            verdict_counts.setdefault(verdict.value, 0)

    return SetProgress(
        set_name=set_name,
        target_total=target.total,
        target_dev=target.dev,
        target_locked=target.locked,
        completed_total=len(heads),
        completed_dev=completed[Split.DEV],
        completed_locked=completed[Split.LOCKED],
        unanswerable_dev=unanswerable[Split.DEV],
        unanswerable_locked=unanswerable[Split.LOCKED],
        unanswerable_floor_dev=target.unanswerable_dev,
        unanswerable_floor_locked=target.unanswerable_locked,
        clause_first=directions[QuestionDirection.CLAUSE_FIRST],
        scenario_first=directions[QuestionDirection.SCENARIO_FIRST],
        clause_first_target=target.clause_first_share,
        provenance=ProvenanceProgress(
            content_origins=dict(sorted(content_origins.items())),
            label_origins=dict(sorted(label_origins.items())),
            gold_origins=dict(sorted(gold_origins.items())),
            gold_origin_chains=dict(sorted(gold_origin_chains.items())),
            retrieval_originated_gold_items=retrieval_originated_gold_items,
        ),
        verdict_counts=dict(sorted(verdict_counts.items())),
        awaiting_adjudication=awaiting,
        gold_clauses=gold,
        pooled_gold_clauses=pooled,
    )


def build_progress(
    records: Iterable[Annotation],
    gold_review: ReviewStatistics | None = None,
    pooling_audit: PoolingAuditProgress | None = None,
    retired_item_ids: frozenset[str] = frozenset(),
) -> ProgressReport:
    """Report progress over annotation records, counting items not files.

    ``gold_review`` is built by the caller, because the statistics need the
    evaluation set's declared sample rate and salt and this module has no
    business knowing either.
    """
    by_id: dict[str, Annotation] = {}
    for record in records:
        if record.annotation_id is None:
            raise ValueError("annotation is not content-addressed")
        by_id[record.annotation_id] = record

    superseded = {
        record.predecessor_annotation_id
        for record in by_id.values()
        if record.predecessor_annotation_id is not None
    }

    sets: dict[str, list[tuple[Annotation, Annotation]]] = {"l1": [], "l2": []}
    items: set[str] = set()
    for annotation_id, head in by_id.items():
        if annotation_id in superseded:
            continue
        if head.item_id in items:
            raise ValueError(f"item_id {head.item_id!r} owns more than one chain")
        items.add(head.item_id)
        # The record stays; it just stops counting. A defective question cannot
        # be answered correctly, so leaving it in the denominator would measure
        # the defect rather than the system.
        if head.item_id in retired_item_ids:
            continue
        key = "l2" if isinstance(head, L2Annotation) else "l1"
        sets[key].append((head, _chain_root(head, by_id)))

    return ProgressReport(
        l1=_set_progress("l1", L1_TARGET, sets["l1"]),
        l2=_set_progress("l2", L2_TARGET, sets["l2"]),
        annotated_items=len(items),
        superseded_count=len(superseded),
        retired_item_ids=tuple(sorted(retired_item_ids & items)),
        gold_review=gold_review,
        pooling_audit=pooling_audit,
    )


def read_progress(
    directory: Path,
    gold_review: ReviewStatistics | None = None,
    pooling_audit: PoolingAuditProgress | None = None,
) -> ProgressReport:
    """Report progress over a stored annotation set, verifying every record.

    Retired items keep their records and stop counting toward the target: an
    item whose question is defective cannot be answered correctly, so leaving
    it in the denominator would measure the defect rather than the system.
    """
    store = AnnotationStore(directory)
    return build_progress(
        store.iter_records(),
        gold_review,
        pooling_audit,
        frozenset(entry.item_id for entry in store.read_retirements()),
    )
