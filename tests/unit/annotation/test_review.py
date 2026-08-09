"""The deep-review sample is drawn before the reviewer sees anything.

A reviewer who picks which items to check against the full source will pick the
ones that look easy, and the error rate that comes back describes the easy
items. The sample is therefore a deterministic function of the item id and a
recorded salt: fixed before the first item is opened, and not recomputable to a
more convenient answer afterwards.
"""

from __future__ import annotations

import pytest

from specpilot.annotation.review import deep_review_required

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
