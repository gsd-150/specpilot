"""The deep-review sample is drawn before the reviewer sees anything.

A reviewer who picks which items to check against the full source will pick the
ones that look easy, and the error rate that comes back describes the easy
items. The sample is therefore a deterministic function of the item id and a
recorded salt: fixed before the first item is opened, and not recomputable to a
more convenient answer afterwards.
"""

from __future__ import annotations

import pytest

from specpilot.annotation.review import deep_review_required, review_statistics
from specpilot.contracts.annotation import ReviewDecision

SALT = "r1-2026-08"
IDS = tuple(f"l1-dev-{n:03d}" for n in range(2000))


def test_the_same_item_and_salt_always_give_the_same_answer() -> None:
    for item_id in IDS[:50]:
        first = deep_review_required(item_id, rate=0.2, salt=SALT)
        assert deep_review_required(item_id, rate=0.2, salt=SALT) is first


def test_the_sample_does_not_depend_on_when_it_is_asked() -> None:
    """Deciding at review time and deciding beforehand must agree.

    Otherwise "pre-registered" is a claim about intent rather than a property
    of the code.
    """
    planned = {item: deep_review_required(item, rate=0.25, salt=SALT) for item in IDS}

    for item, expected in planned.items():
        assert deep_review_required(item, rate=0.25, salt=SALT) is expected


def test_a_rate_of_zero_samples_nothing_and_a_rate_of_one_samples_everything() -> None:
    assert not any(deep_review_required(item, rate=0.0, salt=SALT) for item in IDS)
    assert all(deep_review_required(item, rate=1.0, salt=SALT) for item in IDS)


@pytest.mark.parametrize("rate", [0.1, 0.25, 0.5])
def test_the_realised_rate_approaches_the_configured_one(rate: float) -> None:
    drawn = sum(deep_review_required(item, rate=rate, salt=SALT) for item in IDS)

    assert abs(drawn / len(IDS) - rate) < 0.03


def test_changing_the_salt_changes_the_sample() -> None:
    """Which is why the salt has to be recorded with the evaluation set.

    Without it the sample cannot be checked, and an unrecorded salt is one that
    can be chosen after the fact.
    """
    first = {item for item in IDS if deep_review_required(item, rate=0.2, salt=SALT)}
    second = {
        item for item in IDS if deep_review_required(item, rate=0.2, salt="different")
    }

    assert first != second
    assert first & second  # a different sample, not a disjoint universe


@pytest.mark.parametrize("rate", [-0.01, 1.01])
def test_a_rate_outside_zero_to_one_is_refused(rate: float) -> None:
    with pytest.raises(ValueError, match="rate"):
        deep_review_required("l1-dev-001", rate=rate, salt=SALT)


def test_an_empty_salt_is_refused() -> None:
    """An empty salt is indistinguishable from having forgotten to set one."""
    with pytest.raises(ValueError, match="salt"):
        deep_review_required("l1-dev-001", rate=0.2, salt="")


def decision(item_id: str, outcome: str, **overrides: object) -> ReviewDecision:
    accepted = outcome == "accepted_as_proposed"
    rejected = outcome == "item_rejected"
    fields: dict[str, object] = {
        "reviewed_annotation_id": (
            None if rejected else item_id.encode().hex().zfill(64)
        ),
        "item_id": item_id,
        "outcome": outcome,
        "candidates_shown": 4,
        "chose_proposal": accepted,
        "reviewer_id": "chunxue",
        "proposal_producer": "claude-opus-5",
        "deep_reviewed": deep_review_required(item_id, rate=0.25, salt=SALT),
    }
    return ReviewDecision(**{**fields, **overrides})


def test_a_rejection_stays_in_the_acceptance_denominator() -> None:
    """A store holding only accepted proposals reports 100% by construction.

    Which is the exact shape of a number that reassures and measures nothing.
    """
    stats = review_statistics(
        [
            decision("l1-dev-001", "accepted_as_proposed"),
            decision("l1-dev-002", "accepted_as_proposed"),
            decision("l1-dev-003", "gold_changed"),
            decision("l1-dev-004", "item_rejected"),
        ],
        rate=0.25,
        salt=SALT,
    )

    assert stats.accepted == 2
    assert stats.gold_changed == 1
    assert stats.rejected == 1
    assert stats.proposal_acceptance_rate == 0.5


def test_an_acceptance_rate_over_nothing_is_not_zero() -> None:
    """Zero would read as "every proposal was rejected"."""
    stats = review_statistics([], rate=0.25, salt=SALT)

    assert stats.proposal_acceptance_rate is None
    assert stats.deep_review_coverage is None


def test_a_re_review_is_counted_as_another_decision_not_another_item() -> None:
    """The store has no clock, so "the latest decision" is not knowable.

    Counting every decision is well defined and errs downward: a rejection later
    overturned still shows as a rejection, which understates the acceptance rate
    rather than overstating it.
    """
    stats = review_statistics(
        [
            decision("l1-dev-001", "item_rejected"),
            decision("l1-dev-001", "accepted_as_proposed"),
            decision("l1-dev-002", "accepted_as_proposed"),
        ],
        rate=0.25,
        salt=SALT,
    )

    assert stats.decisions == 3
    assert stats.reviewed_items == 2
    assert stats.re_reviews == 1
    assert stats.proposal_acceptance_rate == pytest.approx(2 / 3)


def test_the_deep_review_sample_is_recomputed_rather_than_believed() -> None:
    """The sample is checked against the declared rate, not against the run's own.

    A pass run at `--deep-review-rate 0.0` flags nothing and looks complete on
    its own terms. Recomputing from the salt the evaluation set declares is what
    makes that visible as a gap.
    """
    items = [f"l1-dev-{n:03d}" for n in range(40)]
    flagged = review_statistics(
        [decision(item, "accepted_as_proposed") for item in items],
        rate=0.25,
        salt=SALT,
    )
    unflagged = review_statistics(
        [
            decision(item, "accepted_as_proposed", deep_reviewed=False)
            for item in items
        ],
        rate=0.25,
        salt=SALT,
    )

    assert flagged.deep_review_expected > 0
    assert flagged.deep_review_flagged == flagged.deep_review_expected
    assert unflagged.deep_review_expected == flagged.deep_review_expected
    assert unflagged.deep_review_flagged == 0


def test_flagging_every_sampled_item_still_reports_no_coverage() -> None:
    """This assertion used to read `recorded == expected` and pass.

    It passed because `recorded` was counted from `deep_reviewed`, which the
    same function sets that picks the sample — so it could only ever agree with
    itself. A real pass then reported 100% coverage with no deep reading in it.
    Coverage now comes from findings, and being flagged buys nothing.
    """
    items = [f"l1-dev-{n:03d}" for n in range(40)]

    stats = review_statistics(
        [decision(item, "accepted_as_proposed") for item in items],
        [],
        rate=0.25,
        salt=SALT,
    )

    assert stats.deep_review_flagged == stats.deep_review_expected
    assert stats.deep_review_recorded == 0
    assert stats.deep_review_coverage == 0.0


def test_edited_key_points_are_counted() -> None:
    """Drafted key points are the part the forced choice cannot check.

    So whether they were accepted verbatim is a countable fact rather than an
    invisible one.
    """
    stats = review_statistics(
        [
            decision("l1-dev-001", "accepted_as_proposed", key_points_edited=True),
            decision("l1-dev-002", "accepted_as_proposed"),
        ],
        rate=0.25,
        salt=SALT,
    )

    assert stats.key_points_edited == 1


def test_the_statistics_name_who_reviewed_and_who_drafted() -> None:
    stats = review_statistics(
        [
            decision("l1-dev-001", "accepted_as_proposed"),
            decision(
                "l1-dev-002", "accepted_as_proposed", proposal_producer="openai-codex"
            ),
        ],
        rate=0.25,
        salt=SALT,
    )

    assert stats.reviewers == {"chunxue": 2}
    assert stats.proposal_producers == {"claude-opus-5": 1, "openai-codex": 1}


def test_the_payload_says_what_it_measures_and_carries_no_system_metric() -> None:
    """§8.1: these describe the gold, not the system.

    Reported beside a retrieval or answer figure without that distinction, an
    acceptance rate reads as a quality result for SpecPilot, which it is not.
    """
    payload = review_statistics(
        [decision("l1-dev-001", "accepted_as_proposed")], rate=0.25, salt=SALT
    ).payload()

    assert payload["measures"] == "gold_quality"
    assert payload["deep_review_salt"] == SALT
    assert payload["deep_review_rate"] == 0.25
    forbidden = ("recall", "ndcg", "mrr", "precision", "f1", "latency", "accuracy")
    assert not [key for key in payload if any(word in key for word in forbidden)]
