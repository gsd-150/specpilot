"""The deep read has to produce a finding, not set a flag.

`ReviewDecision.deep_reviewed` records that the reviewer was told an item was
sampled. It records nothing about whether they then read anything, so a reviewer
who ignores the banner leaves a byte-identical record — and a pass with no deep
reading in it reported 100% coverage on exactly that basis.

These tests pin the two properties that make a finding evidence: it cannot exist
without naming what was examined and what came of it, and coverage is computed
from findings rather than from the flag that can only ever agree with itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.annotation.review import (
    DeepReviewStore,
    deep_review_required,
    review_statistics,
)
from specpilot.contracts.annotation import (
    DeepReviewFinding,
    DeepReviewOutcome,
    ReviewDecision,
)

ANNOTATION_ID = "a" * 64
SALT = "r1-2026-08"
FIELDS: dict[str, object] = {
    "reviewed_annotation_id": ANNOTATION_ID,
    "item_id": "l1-dev-007",
    "outcome": "gold_complete",
    "scope": "section",
    "clauses_examined": 13,
    "elapsed_seconds": 480,
    "reviewer_id": "chunxue",
}


def finding(**overrides: object) -> DeepReviewFinding:
    return DeepReviewFinding(**{**FIELDS, **overrides})


def test_a_finding_records_what_was_examined_not_only_that_it_was() -> None:
    made = finding()

    assert made.clauses_examined == 13
    assert made.elapsed_seconds == 480
    assert made.outcome is DeepReviewOutcome.GOLD_COMPLETE


def test_finding_nothing_is_a_result_and_records_as_one() -> None:
    """Most sections have nothing to add. That is a claim with a scope and a
    duration behind it, not an absence."""
    confirmed = finding(outcome="gold_complete")

    assert confirmed.additional_gold_clause_ids == ()
    assert confirmed.clauses_examined >= 1


def test_extending_the_gold_and_the_outcome_cannot_disagree() -> None:
    with pytest.raises(ValidationError):
        finding(outcome="gold_extended")
    with pytest.raises(ValidationError):
        finding(outcome="gold_complete", additional_gold_clause_ids=("b" * 64,))


def test_a_clause_cannot_be_added_twice() -> None:
    with pytest.raises(ValidationError):
        finding(
            outcome="gold_extended",
            additional_gold_clause_ids=("b" * 64, "b" * 64),
        )


def test_a_finding_over_nothing_is_refused() -> None:
    """Zero clauses examined is not a deep review of anything."""
    with pytest.raises(ValidationError):
        finding(clauses_examined=0)


def test_the_finding_holds_no_prose() -> None:
    for forbidden in ("note", "comment", "quote", "clause_text", "summary"):
        with pytest.raises(ValidationError):
            finding(**{forbidden: "read the section, looked fine"})


def test_a_finding_is_content_addressed_and_create_only(tmp_path: Path) -> None:
    store = DeepReviewStore(tmp_path)

    stored = store.create(finding())

    assert len(stored.finding_id) == 64
    assert store.read(stored.finding_id) == stored
    assert store.for_annotation(ANNOTATION_ID) == (stored,)


def test_stored_findings_are_private(tmp_path: Path) -> None:
    stored = DeepReviewStore(tmp_path).create(finding())

    path = tmp_path / f"{stored.finding_id}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_a_review_store_will_not_read_a_deep_review_record(tmp_path: Path) -> None:
    """Two kinds of judgement, two schemas, neither readable as the other."""
    from specpilot.annotation.review import ReviewStore

    stored = DeepReviewStore(tmp_path).create(finding())

    with pytest.raises(ValueError, match="schema"):
        ReviewStore(tmp_path).read(stored.finding_id)


def decision(item_id: str, **overrides: object) -> ReviewDecision:
    fields: dict[str, object] = {
        "reviewed_annotation_id": ANNOTATION_ID,
        "item_id": item_id,
        "outcome": "accepted_as_proposed",
        "candidates_shown": 4,
        "chose_proposal": True,
        "reviewer_id": "chunxue",
        "proposal_producer": "claude-opus-5",
        "deep_reviewed": deep_review_required(item_id, rate=0.25, salt=SALT),
    }
    return ReviewDecision(**{**fields, **overrides})


ITEMS = tuple(f"l1-dev-{n:03d}" for n in range(20))
SAMPLED = tuple(i for i in ITEMS if deep_review_required(i, rate=0.25, salt=SALT))


def test_coverage_comes_from_findings_not_from_the_flag() -> None:
    """The exact failure this replaces: every item flagged, none read.

    Coverage computed from `deep_reviewed` reports 1.0 here, because the same
    function sets the flag and picks the sample. Computed from findings it
    reports what happened.
    """
    stats = review_statistics(
        [decision(item) for item in ITEMS], [], rate=0.25, salt=SALT
    )

    assert stats.deep_review_expected == len(SAMPLED)
    assert stats.deep_review_flagged == len(SAMPLED)
    assert stats.deep_review_recorded == 0
    assert stats.deep_review_coverage == 0.0


def test_coverage_rises_only_as_findings_are_recorded() -> None:
    done = SAMPLED[:2]
    stats = review_statistics(
        [decision(item) for item in ITEMS],
        [finding(item_id=item) for item in done],
        rate=0.25,
        salt=SALT,
    )

    assert stats.deep_review_recorded == 2
    assert stats.deep_review_coverage == pytest.approx(2 / len(SAMPLED))


def test_the_statistics_report_how_long_the_deep_reads_took() -> None:
    """A thirteen-paragraph section closed in twelve seconds was not read.

    The minimum is reported beside the median because one such finding is the
    thing worth seeing, and a median hides it.
    """
    stats = review_statistics(
        [decision(item) for item in ITEMS],
        [
            finding(item_id=SAMPLED[0], elapsed_seconds=600),
            finding(item_id=SAMPLED[1], elapsed_seconds=420),
            finding(item_id=SAMPLED[2], elapsed_seconds=11),
        ],
        rate=0.25,
        salt=SALT,
    )

    assert stats.deep_review_seconds_median == 420
    assert stats.deep_review_seconds_min == 11


def test_the_statistics_count_the_gold_the_deep_reads_added() -> None:
    stats = review_statistics(
        [decision(item) for item in ITEMS],
        [
            finding(item_id=SAMPLED[0]),
            finding(
                item_id=SAMPLED[1],
                outcome="gold_extended",
                additional_gold_clause_ids=("b" * 64, "c" * 64),
            ),
            finding(item_id=SAMPLED[2], outcome="gold_wrong"),
        ],
        rate=0.25,
        salt=SALT,
    )

    assert stats.deep_review_additional_gold == 2
    assert stats.deep_review_outcomes == {
        "gold_complete": 1,
        "gold_extended": 1,
        "gold_wrong": 1,
    }


def test_durations_are_absent_rather_than_zero_when_nothing_was_read() -> None:
    """Zero seconds would read as an instant deep review rather than none."""
    stats = review_statistics(
        [decision(item) for item in ITEMS], [], rate=0.25, salt=SALT
    )

    assert stats.deep_review_seconds_median is None
    assert stats.deep_review_seconds_min is None
    assert stats.payload()["deep_review_outcomes"] == {}
