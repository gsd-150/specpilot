"""The review record: what a forced-choice review decided, kept beside the item.

`gold_origins` already records that a human reviewed a model proposal. It does
not record what the review found, so a reviewer who approves everything and one
who catches real errors leave identical records — and gold is the ruler, so a
wrong gold makes every downstream metric wrong with nothing to catch it.

The decision is a separate record rather than a field on the annotation, for two
reasons found while trying the other way. Annotations are content addressed, so
adding any field — even one defaulting to null — changes every stored record's
ID and makes the three existing records unreadable. And an annotation's identity
should not change because somebody reviewed it: the question, the gold, and the
key points are what the item *is*, while the review is a later judgement about
it by a different actor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.annotation.review import ReviewStore
from specpilot.contracts.annotation import ReviewDecision, ReviewOutcome

ANNOTATION_ID = "a" * 64
FIELDS: dict[str, object] = {
    "reviewed_annotation_id": ANNOTATION_ID,
    "item_id": "l1-dev-001",
    "outcome": "accepted_as_proposed",
    "candidates_shown": 3,
    "chose_proposal": True,
    "key_points_edited": False,
    "deep_reviewed": False,
    "reviewer_id": "chunxue",
    "proposal_producer": "claude-opus-5",
}


def decision(**overrides: object) -> ReviewDecision:
    return ReviewDecision(**{**FIELDS, **overrides})


def test_all_three_outcomes_validate() -> None:
    assert {member.value for member in ReviewOutcome} == {
        "accepted_as_proposed",
        "gold_changed",
        "item_rejected",
    }


def test_choosing_the_proposal_and_the_outcome_cannot_disagree() -> None:
    """One fact, not two that can drift apart.

    The acceptance rate is computed from one of them, so they must agree.
    """
    with pytest.raises(ValidationError):
        decision(outcome="gold_changed", chose_proposal=True)
    with pytest.raises(ValidationError):
        decision(outcome="accepted_as_proposed", chose_proposal=False)
    with pytest.raises(ValidationError):
        decision(outcome="item_rejected", chose_proposal=True)


def test_a_changed_gold_and_a_rejection_both_record_cleanly() -> None:
    changed = decision(outcome="gold_changed", chose_proposal=False)
    rejected = decision(outcome="item_rejected", chose_proposal=False)

    assert changed.outcome is ReviewOutcome.GOLD_CHANGED
    assert rejected.outcome is ReviewOutcome.ITEM_REJECTED


def test_a_review_needs_at_least_two_candidates_to_be_a_choice() -> None:
    with pytest.raises(ValidationError):
        decision(candidates_shown=1)


def test_a_review_of_an_unanswerable_item_shows_no_candidates() -> None:
    """Its review confirms nothing in the document answers the question.

    There is no clause to choose between, so zero is the honest count and the
    two-candidate floor does not apply.
    """
    confirmed = decision(candidates_shown=0, unanswerable=True)

    assert confirmed.candidates_shown == 0
    assert confirmed.unanswerable is True


def test_an_answerable_review_cannot_claim_to_be_unanswerable_with_candidates() -> None:
    with pytest.raises(ValidationError):
        decision(candidates_shown=3, unanswerable=True)


def test_the_record_holds_no_prose() -> None:
    """A free-text justification is unfalsifiable and invites clause text.

    What the report needs from a review is how often the reviewer disagreed,
    which is a number.
    """
    for forbidden in ("note", "comment", "rationale", "quote", "clause_text"):
        with pytest.raises(ValidationError):
            decision(**{forbidden: "looked fine to me"})


def test_the_record_names_the_reviewer_and_the_drafter() -> None:
    """Both halves of a mixed-origin record, so the report can say who did what."""
    made = decision()

    assert made.reviewer_id == "chunxue"
    assert made.proposal_producer == "claude-opus-5"


def test_a_review_is_content_addressed_and_create_only(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path)

    stored = store.create(decision())

    assert len(stored.review_id) == 64
    assert store.read(stored.review_id) == stored


def test_replaying_an_identical_review_is_a_no_op(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path)

    first = store.create(decision())
    again = store.create(decision())

    assert again.review_id == first.review_id


def test_a_second_different_review_of_one_item_is_kept_not_overwritten(
    tmp_path: Path,
) -> None:
    """A re-review is a real event, and losing the first would hide a change of mind."""
    store = ReviewStore(tmp_path)

    first = store.create(decision())
    second = store.create(decision(outcome="gold_changed", chose_proposal=False))

    assert first.review_id != second.review_id
    assert {item.review_id for item in store.read_all()} == {
        first.review_id,
        second.review_id,
    }


def test_reviews_are_findable_by_the_annotation_they_judge(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path)
    store.create(decision())
    store.create(decision(reviewed_annotation_id="b" * 64, item_id="l1-dev-002"))

    for_first = store.for_annotation(ANNOTATION_ID)

    assert [item.item_id for item in for_first] == ["l1-dev-001"]


def test_stored_reviews_are_private(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path)

    stored = store.create(decision())

    path = tmp_path / f"{stored.review_id}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700
