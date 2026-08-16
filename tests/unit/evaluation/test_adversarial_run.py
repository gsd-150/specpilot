from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.annotation.adversarial import AdversarialGroupStore
from specpilot.contracts.annotation import (
    AnnotationOrigin,
    GoldOrigin,
    GoldOriginEvent,
    Split,
    Verdict,
)
from specpilot.contracts.l2_adv import AdversarialDimension, AdversarialGroup
from specpilot.evaluation.adversarial_run import (
    AdversarialRole,
    AdversarialRunError,
    PairOutcome,
    build_pair_matrix,
    join_pair,
    plan_cases,
    select_groups,
)

_DIMENSIONS = tuple(AdversarialDimension)
_REVIEWED = (GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),)


def group(
    index: int, split: Split = Split.DEV, **overrides: object
) -> AdversarialGroup:
    tag = f"{split.value}-{index:03d}"
    base = index + (0 if split is Split.DEV else 1_000)
    fields: dict[str, object] = {
        "group_id": f"adv-{tag}",
        "family": f"family-{tag}",
        "split": split,
        "dimension": _DIMENSIONS[index % len(_DIMENSIONS)],
        "negative_claim_id": f"adv-{tag}-neg",
        "negative_claim": f"the proxy must reject request {tag}",
        "distractor_clause_ids": (f"{base:064x}",),
        "positive_claim_id": f"adv-{tag}-pos",
        "positive_claim": f"the origin server must reject request {tag}",
        "supporting_clause_ids": (f"{base + 100:064x}",),
        "proposed_verdict": Verdict.VIOLATING,
        "content_origin": AnnotationOrigin.HUMAN,
        "label_origin": AnnotationOrigin.HUMAN,
        "construction_origins": _REVIEWED,
    }
    return AdversarialGroup(**{**fields, **overrides})


@pytest.fixture
def store(tmp_path: Path) -> AdversarialGroupStore:
    return AdversarialGroupStore(tmp_path / "l2-adv")


def stored(store: AdversarialGroupStore, *groups: AdversarialGroup) -> None:
    for item in groups:
        store.create(item)


def test_selection_returns_one_split_ordered_by_group_id(
    store: AdversarialGroupStore,
) -> None:
    stored(
        store,
        group(1),
        group(0),
        group(0, split=Split.LOCKED),
    )

    groups = select_groups(store, split=Split.DEV, expected=2)

    assert [item.group_id for item in groups] == ["adv-dev-000", "adv-dev-001"]


def test_a_count_that_differs_from_the_expected_one_refuses(
    store: AdversarialGroupStore,
) -> None:
    stored(store, group(0), group(1))

    with pytest.raises(AdversarialRunError, match="adversarial_count_mismatch"):
        select_groups(store, split=Split.DEV, expected=6)


def test_an_empty_selection_refuses(store: AdversarialGroupStore) -> None:
    stored(store, group(0))

    with pytest.raises(AdversarialRunError, match="adversarial_empty_selection"):
        select_groups(store, split=Split.LOCKED, expected=0)


def test_one_group_plans_two_cases_with_distinct_ids(
    store: AdversarialGroupStore,
) -> None:
    """A group is one independent unit and two invocations.

    A root is one question (§3.2) and the ledger refuses a second question
    under a reused root, so the two claims cannot share one.
    """
    cases = plan_cases((group(0),))

    assert len(cases) == 2
    negative, positive = cases
    assert negative.role is AdversarialRole.NEGATIVE
    assert positive.role is AdversarialRole.POSITIVE
    assert negative.group_id == positive.group_id == "adv-dev-000"
    assert negative.case_id != positive.case_id
    assert negative.claim != positive.claim
    assert negative.expected_verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert positive.expected_verdict is Verdict.VIOLATING


def test_the_planned_cases_stay_grouped(store: AdversarialGroupStore) -> None:
    """Both claims of a group run adjacently so a sweep interrupted midway
    leaves whole pairs behind rather than orphaned halves."""
    cases = plan_cases((group(0), group(1)))

    assert [case.group_id for case in cases] == [
        "adv-dev-000",
        "adv-dev-000",
        "adv-dev-001",
        "adv-dev-001",
    ]


def test_a_pair_the_system_gets_right_is_recorded_as_both() -> None:
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
        positive_verdicts=(Verdict.VIOLATING,),
    )

    assert result.outcome is PairOutcome.BOTH
    assert result.false_confirmation is False


def test_a_confirmed_negative_is_a_finding_not_an_error() -> None:
    """The number this subset exists to produce.

    A negative that reaches a determinate verdict is the end-to-end false
    confirmation §8.5.3 measures. Raising here would turn the measurement into
    a crash and lose the run.
    """
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.VIOLATING,),
        positive_verdicts=(Verdict.VIOLATING,),
    )

    assert result.false_confirmation is True
    assert result.outcome is PairOutcome.POSITIVE_ONLY


def test_a_negative_is_refused_when_any_claim_lacks_evidence() -> None:
    """The chain decomposes a claim into atomic claims, so a case can return
    several verdicts. The negative counts as correctly refused when any one of
    them is insufficient: the adversarial claim as posed was not confirmed.
    """
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.VIOLATING, Verdict.INSUFFICIENT_EVIDENCE),
        positive_verdicts=(Verdict.VIOLATING,),
    )

    assert result.false_confirmation is False
    assert result.outcome is PairOutcome.BOTH


def test_a_positive_downgraded_to_insufficient_is_a_false_rejection() -> None:
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
        positive_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
    )

    assert result.outcome is PairOutcome.NEGATIVE_ONLY
    assert result.false_rejection is True


def test_a_positive_with_the_wrong_determinate_verdict_is_not_a_pass() -> None:
    """`compliant` where `violating` was annotated is a wrong answer, not a
    rejection: it is determinate and it disagrees with the gold label."""
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
        positive_verdicts=(Verdict.COMPLIANT,),
    )

    assert result.outcome is PairOutcome.NEGATIVE_ONLY
    assert result.false_rejection is False


def test_both_halves_wrong_is_neither() -> None:
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.VIOLATING,),
        positive_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
    )

    assert result.outcome is PairOutcome.NEITHER


def test_a_half_run_pair_refuses_rather_than_scoring_the_half_that_ran() -> None:
    """Scoring one side would silently change what the subset measures.

    The matched pair is the unit: a negative alone says nothing about whether
    the system can still answer the near-identical answerable claim, which is
    the entire point of building them together.
    """
    with pytest.raises(AdversarialRunError, match="adversarial_pair_incomplete"):
        join_pair(
            group(0),
            negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
            positive_verdicts=(),
        )


def test_the_matrix_counts_groups_not_claims() -> None:
    """§8.5.4: `n=10 groups`. Twenty invocations are not twenty samples."""
    results = (
        join_pair(
            group(0),
            negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
            positive_verdicts=(Verdict.VIOLATING,),
        ),
        join_pair(
            group(1),
            negative_verdicts=(Verdict.VIOLATING,),
            positive_verdicts=(Verdict.VIOLATING,),
        ),
        join_pair(
            group(2),
            negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
            positive_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
        ),
    )

    matrix = build_pair_matrix(results)

    assert matrix["group_count"] == 3
    assert matrix["outcomes"] == {
        "both": 1,
        "negative_only": 1,
        "positive_only": 1,
        "neither": 0,
    }
    assert matrix["false_confirmations"] == 1
    assert matrix["false_rejections"] == 1
    assert matrix["dimension_counts"][_DIMENSIONS[0].value] == 1


def test_the_matrix_refuses_a_duplicated_group() -> None:
    """A group counted twice inflates `n` on a subset whose whole discipline is
    that it does not."""
    result = join_pair(
        group(0),
        negative_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
        positive_verdicts=(Verdict.VIOLATING,),
    )

    with pytest.raises(AdversarialRunError, match="adversarial_group_repeated"):
        build_pair_matrix((result, result))
