from __future__ import annotations

import pytest

from specpilot.annotation.adversarial import (
    AdversarialRegistrationError,
    build_overlap_report,
    build_registration_status,
)
from specpilot.contracts.annotation import Split, Verdict
from specpilot.contracts.l2_adv import AdversarialDimension, AdversarialGroup

_DIMENSIONS = tuple(AdversarialDimension)


def _group(index: int, split: Split) -> AdversarialGroup:
    tag = f"{split.value}-{index:03d}"
    # Offset the clause digests by split: dev and locked must not share a
    # distractor, and an index-only digest would collide across the two.
    base = index + (0 if split is Split.DEV else 1_000)
    return AdversarialGroup(
        group_id=f"adv-{tag}",
        family=f"family-{tag}",
        split=split,
        dimension=_DIMENSIONS[index % len(_DIMENSIONS)],
        negative_claim_id=f"adv-{tag}-neg",
        negative_claim=f"the proxy must reject request {tag}",
        distractor_clause_ids=(f"{base:064x}",),
        positive_claim_id=f"adv-{tag}-pos",
        positive_claim=f"the origin server must reject request {tag}",
        supporting_clause_ids=(f"{base + 100:064x}",),
        proposed_verdict=Verdict.VIOLATING,
    )


def _registration(
    *, dev: int = 6, locked: int = 10
) -> tuple[AdversarialGroup, ...]:
    return tuple(
        [_group(index, Split.DEV) for index in range(dev)]
        + [_group(index, Split.LOCKED) for index in range(locked)]
    )


def test_a_clean_registration_produces_the_schema_the_freeze_reads() -> None:
    groups = _registration()
    report = build_overlap_report(groups)
    status = build_registration_status(groups, report)

    assert status["schema_version"] == "l2-adv-registration/v1"
    assert len(status["dev"]["item_ids"]) == 6
    assert len(status["locked"]["item_ids"]) == 10
    assert len(status["dev"]["families"]) == 6
    assert len(status["locked"]["families"]) == 10
    assert len(status["overlap_report_sha256"]) == 64


def test_the_status_carries_no_key_the_freeze_forbids() -> None:
    """§8.1.1 registration is identifiers, never the claims themselves.

    The freeze rejects `question`, `claim`, `excerpt`, `answer`, and `rationale`
    recursively, so a status that carried claim text would be refused at the
    gate rather than here — a late failure for something knowable now.
    """
    groups = _registration()
    status = build_registration_status(groups, build_overlap_report(groups))

    def _keys(value: object) -> set[str]:
        if isinstance(value, dict):
            found = set(value)
            for nested in value.values():
                found |= _keys(nested)
            return found
        if isinstance(value, (list, tuple)):
            found: set[str] = set()
            for nested in value:
                found |= _keys(nested)
            return found
        return set()

    assert not _keys(status) & {
        "question",
        "claim",
        "excerpt",
        "answer",
        "rationale",
    }


@pytest.mark.parametrize(("dev", "locked"), [(5, 10), (6, 9), (3, 10), (6, 16)])
def test_a_registration_of_the_wrong_size_is_refused_here_not_at_the_gate(
    dev: int, locked: int
) -> None:
    groups = _registration(dev=dev, locked=locked)

    with pytest.raises(AdversarialRegistrationError, match="cardinality"):
        build_registration_status(groups, build_overlap_report(groups))


def test_the_report_names_every_axis_the_plan_requires_to_be_disjoint() -> None:
    report = build_overlap_report(_registration())

    assert set(report.checks) == {
        "group_id",
        "family",
        "claim",
        "distractor_clause",
        "positive_pair",
    }
    assert all(check.disjoint for check in report.checks.values())
    assert report.clean


def test_a_family_shared_across_the_split_is_reported_and_then_refused() -> None:
    groups = list(_registration())
    groups[6] = groups[6].model_copy(update={"family": groups[0].family})

    report = build_overlap_report(tuple(groups))

    assert not report.clean
    assert not report.checks["family"].disjoint
    assert report.checks["family"].shared == (groups[0].family,)

    with pytest.raises(AdversarialRegistrationError, match="overlap"):
        build_registration_status(tuple(groups), report)


def test_a_claim_reused_across_the_split_is_caught_though_the_gate_cannot() -> None:
    """The freeze checks ids and families only.

    A locked group that restates a dev claim under a fresh id and family passes
    every check `freeze.py` makes, and quietly destroys the isolation the locked
    split exists to provide. This is the last point where the text is present to
    compare, so it is compared here.
    """
    groups = list(_registration())
    groups[6] = groups[6].model_copy(
        update={"negative_claim": groups[0].negative_claim}
    )

    report = build_overlap_report(tuple(groups))

    assert not report.checks["claim"].disjoint
    with pytest.raises(AdversarialRegistrationError, match="overlap"):
        build_registration_status(tuple(groups), report)


def test_a_distractor_clause_reused_across_the_split_is_caught() -> None:
    groups = list(_registration())
    groups[6] = groups[6].model_copy(
        update={"distractor_clause_ids": groups[0].distractor_clause_ids}
    )

    report = build_overlap_report(tuple(groups))

    assert not report.checks["distractor_clause"].disjoint
    with pytest.raises(AdversarialRegistrationError, match="overlap"):
        build_registration_status(tuple(groups), report)


def test_a_report_from_other_groups_cannot_be_passed_off_as_this_one() -> None:
    groups = _registration()
    other = build_overlap_report(_registration(dev=6, locked=10)[:16])
    stale = other.model_copy(update={"registration_sha256": "f" * 64})

    with pytest.raises(AdversarialRegistrationError, match="report"):
        build_registration_status(groups, stale)


def test_the_report_records_the_dimension_distribution() -> None:
    report = build_overlap_report(_registration())

    assert sum(report.dimension_counts.values()) == 16
    assert set(report.dimension_counts) <= {
        dimension.value for dimension in AdversarialDimension
    }
