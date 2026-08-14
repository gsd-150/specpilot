"""Create-only, content-addressed storage for forced-choice review decisions.

Kept beside the annotation store rather than inside it. An annotation is what
the item is — its question, its gold, its key points — and its content ID is a
hash over exactly that. A review is a later judgement about the item by a
different actor, so binding it into the annotation's identity would mean the
same item has two IDs depending on whether anyone has looked at it yet.

Two consequences follow, and both are wanted. Records written before review
existed stay byte-identical and readable. And a re-review is an additional
record rather than an edit, so a change of mind leaves both decisions behind
instead of overwriting the first.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from specpilot.contracts.annotation import (
    DeepReviewFinding,
    ReviewDecision,
    ReviewOutcome,
)
from specpilot.manifests.canonical import canonical_json, canonical_sha256

_MAX_RECORD_BYTES = 16 * 1024

# Separates the salt from the item id before hashing, so salt "r1" with item
# "0-001" and salt "r1-0" with item "001" cannot draw the same number. A unit
# separator appears in neither.
_SEPARATOR = "\x1f"

# The draw is uniform over 64 bits. Comparing it against the rate is the whole
# sampler: no generator state, nothing to seed at start-up, nothing that gives a
# different answer on a second run.
_DRAW_WIDTH = 8
_DRAW_SPACE = float(1 << (_DRAW_WIDTH * 8))


def _validate_sample(rate: float, salt: str) -> None:
    if not 0.0 <= rate <= 1.0:
        raise ValueError("the deep-review rate must be between zero and one")
    if not salt:
        # Indistinguishable from having forgotten to configure one, and a
        # forgotten salt makes the sample a constant nobody chose.
        raise ValueError("the deep-review salt may not be empty")


def deep_review_required(item_id: str, *, rate: float, salt: str) -> bool:
    """Whether this item is in the sample that gets read against full source.

    Deterministic in the item id and the salt, so the sample is fixed before
    the first item is opened. A reviewer choosing which items to check deeply
    picks the ones that look easy, and the error rate that comes back then
    describes the easy items rather than the set.

    Being a pure function of the id is also what makes it checkable afterwards:
    anyone with the salt can recompute which items should have been read deeply
    and compare that against which ones were. That is why the salt belongs in
    the evaluation set's own record — an unrecorded salt is one that can be
    chosen after the results are in.
    """
    _validate_sample(rate, salt)
    digest = hashlib.sha256(f"{salt}{_SEPARATOR}{item_id}".encode()).digest()
    draw = int.from_bytes(digest[:_DRAW_WIDTH], "big") / _DRAW_SPACE
    return draw < rate


class _JudgementStore[Record: (ReviewDecision, DeepReviewFinding)]:
    """Create-only, content-addressed storage for one kind of judgement record.

    Shared by the forced-choice decisions and the deep-review findings because
    they need identical discipline, and a second hand-written copy of it is a
    second chance to get the permissions or the replay check subtly wrong.
    """

    _model: type[Record]
    _schema: str
    _id_field: str
    _noun: str

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create(self, record: Record) -> Record:
        record_id = canonical_sha256(record)
        self._write(record_id, record)
        return self.read(record_id)

    def read(self, record_id: str) -> Record:
        path = self._directory / f"{record_id}.json"
        data = path.read_bytes()
        if len(data) > _MAX_RECORD_BYTES:
            raise ValueError(f"stored {self._noun} exceeds the maximum record size")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError(f"stored {self._noun} is invalid")
        if parsed.get("schema_version") != self._schema:
            raise ValueError(f"unsupported {self._noun} schema")
        try:
            record = self._model.model_validate_json(data)
        except ValidationError as error:
            raise ValueError(f"stored {self._noun} is invalid") from error
        if canonical_sha256(record) != record_id:
            raise ValueError(f"stored {self._noun} ID does not match its content")
        updated = record.model_copy(update={self._id_field: record_id})
        assert isinstance(updated, self._model)
        return updated

    def read_all(self) -> tuple[Record, ...]:
        return tuple(self._iter_records())

    def for_annotation(self, annotation_id: str) -> tuple[Record, ...]:
        """Every record about one annotation, in stored order.

        More than one is not an error. A second review of the same item is a
        real event — the reviewer went back — and both belong in the audit
        trail, so the caller decides which is current rather than the store
        silently picking.
        """
        return tuple(
            record
            for record in self._iter_records()
            if record.reviewed_annotation_id == annotation_id
        )

    def _iter_records(self) -> Iterator[Record]:
        if not self._directory.exists():
            return
        for path in sorted(self._directory.glob("*.json")):
            yield self.read(path.stem)

    def _write(self, record_id: str, record: Record) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        path = self._directory / f"{record_id}.json"
        data = canonical_json(record)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError(
                    f"stored {self._noun} differs from the replayed record"
                )
            return
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ReviewStore(_JudgementStore[ReviewDecision]):
    _model = ReviewDecision
    _schema = "annotation-review/v1"
    _id_field = "review_id"
    _noun = "review"


class DeepReviewStore(_JudgementStore[DeepReviewFinding]):
    _model = DeepReviewFinding
    _schema = "annotation-deep-review/v1"
    _id_field = "finding_id"
    _noun = "deep review"


@dataclass(frozen=True, slots=True)
class ReviewStatistics:
    """What the reviews found, as counts that describe the gold.

    Not the system. An acceptance rate printed beside a Recall@k reads as a
    quality result for SpecPilot, which it is not — §8.1 requires the two kept
    apart, so `payload` says what it measures and the report keeps this in its
    own block.

    A high acceptance rate is ambiguous on its own: "the proposals were good"
    and "the review was shallow" fit it equally well. Only the deep-review
    sample separates them, which is why coverage sits in the same object rather
    than somewhere it can be omitted.
    """

    reviewed_items: int
    decisions: int
    accepted: int
    gold_changed: int
    rejected: int
    key_points_edited: int
    unanswerable_reviews: int
    deep_review_rate: float
    deep_review_salt: str
    deep_review_expected: int
    deep_review_flagged: int
    deep_review_recorded: int
    deep_review_outcomes: dict[str, int]
    deep_review_conflicting_items: int
    deep_review_additional_gold: int
    deep_review_seconds_median: int | None
    deep_review_seconds_min: int | None
    reviewers: dict[str, int]
    proposal_producers: dict[str, int]

    @property
    def re_reviews(self) -> int:
        return self.decisions - self.reviewed_items

    @property
    def proposal_acceptance_rate(self) -> float | None:
        """``None`` rather than zero when nothing has been reviewed.

        Zero reads as "every proposal was rejected", which is a different
        statement from "no proposals".
        """
        if not self.decisions:
            return None
        return self.accepted / self.decisions

    @property
    def deep_review_coverage(self) -> float | None:
        """Computed from findings, never from the flag on the decision.

        `deep_reviewed` says the reviewer was told an item was sampled. Coverage
        computed from it can only ever report 100%, because the same function
        sets the flag and picks the sample — which is how a pass with no deep
        reading in it reported complete coverage once already.
        """
        if not self.deep_review_expected:
            return None
        return self.deep_review_recorded / self.deep_review_expected

    def payload(self) -> dict[str, Any]:
        return {
            "measures": "gold_quality",
            "reviewed_items": self.reviewed_items,
            "decisions": self.decisions,
            "re_reviews": self.re_reviews,
            "accepted": self.accepted,
            "gold_changed": self.gold_changed,
            "rejected": self.rejected,
            "proposal_acceptance_rate": self.proposal_acceptance_rate,
            "key_points_edited": self.key_points_edited,
            "unanswerable_reviews": self.unanswerable_reviews,
            "deep_review_rate": self.deep_review_rate,
            "deep_review_salt": self.deep_review_salt,
            "deep_review_expected": self.deep_review_expected,
            # Flagged is how many were *told*; recorded is how many produced a
            # finding. They are reported side by side because the gap between
            # them is the whole question, and one number cannot show a gap.
            "deep_review_flagged": self.deep_review_flagged,
            "deep_review_recorded": self.deep_review_recorded,
            "deep_review_coverage": self.deep_review_coverage,
            "deep_review_outcomes": self.deep_review_outcomes,
            "deep_review_conflicting_items": self.deep_review_conflicting_items,
            "deep_review_additional_gold": self.deep_review_additional_gold,
            "deep_review_seconds_median": self.deep_review_seconds_median,
            "deep_review_seconds_min": self.deep_review_seconds_min,
            "reviewers": self.reviewers,
            "proposal_producers": self.proposal_producers,
        }


def review_statistics(
    decisions: Iterable[ReviewDecision],
    findings: Iterable[DeepReviewFinding] = (),
    *,
    rate: float,
    salt: str,
) -> ReviewStatistics:
    """Count what the reviews decided, against the sample they should have used.

    ``rate`` and ``salt`` are the evaluation set's declared ones, not whatever
    a run happened to pass. The sample is recomputed here rather than believed,
    because a pass run at rate zero records no deep reviews and looks complete
    on its own terms; recomputing is what makes that visible as coverage 0 of N.

    Every decision counts, including a second one about an item already
    reviewed. The store has no clock, so "the latest decision" is not knowable
    from it, and counting them all errs downward — a rejection later overturned
    still shows as a rejection, understating the acceptance rate rather than
    overstating it. ``re_reviews`` makes the gap visible.
    """
    # Up front, not inside the per-item loop: an empty store would otherwise
    # report happily under a rate and salt nobody could have used.
    _validate_sample(rate, salt)
    outcomes: Counter[ReviewOutcome] = Counter()
    reviewers: Counter[str] = Counter()
    producers: Counter[str] = Counter()
    items: set[str] = set()
    deeply_reviewed: set[str] = set()
    total = 0
    edited = 0
    unanswerable = 0

    for decision in decisions:
        total += 1
        outcomes[decision.outcome] += 1
        reviewers[decision.reviewer_id] += 1
        producers[decision.proposal_producer] += 1
        items.add(decision.item_id)
        if decision.deep_reviewed:
            deeply_reviewed.add(decision.item_id)
        if decision.key_points_edited:
            edited += 1
        if decision.unanswerable:
            unanswerable += 1

    # One item can own more than one finding: an item that joins the set late
    # is drawn into the sample and can be read again. `deep_review_recorded`
    # was already per item, but the outcome, gold and duration figures counted
    # findings, so twelve items reported thirteen outcomes. The sample size is
    # what the confidence bound is computed from, and a bound over thirteen is
    # tighter than twelve reads earn — the error was in the direction that
    # overstates the evidence.
    by_item: dict[str, list[DeepReviewFinding]] = {}
    for finding in findings:
        by_item.setdefault(finding.item_id, []).append(finding)

    finding_outcomes: Counter[str] = Counter()
    conflicting = 0
    added_gold = 0
    durations: list[int] = []
    for item_findings in by_item.values():
        seen = {finding.outcome.value for finding in item_findings}
        if len(seen) == 1:
            finding_outcomes[seen.pop()] += 1
        else:
            # Two reads of one item that disagree are not one observation to be
            # resolved by whichever the filesystem yielded last. The findings
            # carry no timestamp, so there is no "latest" to prefer; the
            # disagreement is reported instead of being averaged away.
            conflicting += 1
        added_gold += len(
            {
                clause_id
                for finding in item_findings
                for clause_id in finding.additional_gold_clause_ids
            }
        )
        # The shortest read, because §8.1's reason for timing at all is telling
        # a read from a keystroke. A second, quicker pass over an item already
        # read must not raise the floor this figure reports.
        durations.append(min(finding.elapsed_seconds for finding in item_findings))

    return ReviewStatistics(
        reviewed_items=len(items),
        decisions=total,
        accepted=outcomes[ReviewOutcome.ACCEPTED_AS_PROPOSED],
        gold_changed=outcomes[ReviewOutcome.GOLD_CHANGED],
        rejected=outcomes[ReviewOutcome.ITEM_REJECTED],
        key_points_edited=edited,
        unanswerable_reviews=unanswerable,
        deep_review_rate=rate,
        deep_review_salt=salt,
        deep_review_expected=sum(
            deep_review_required(item, rate=rate, salt=salt) for item in items
        ),
        deep_review_flagged=len(deeply_reviewed),
        deep_review_recorded=len(by_item),
        deep_review_outcomes=dict(sorted(finding_outcomes.items())),
        deep_review_conflicting_items=conflicting,
        deep_review_additional_gold=added_gold,
        deep_review_seconds_median=(
            int(statistics.median(durations)) if durations else None
        ),
        deep_review_seconds_min=min(durations) if durations else None,
        reviewers=dict(sorted(reviewers.items())),
        proposal_producers=dict(sorted(producers.items())),
    )


__all__ = [
    "DeepReviewStore",
    "ReviewStatistics",
    "ReviewStore",
    "deep_review_required",
    "review_statistics",
]
