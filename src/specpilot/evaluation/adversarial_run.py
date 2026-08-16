"""Run a matched adversarial pair as one unit, and score it as one sample.

Nothing executed an `AdversarialGroup` before this. `l2 run` takes a question
and a case id; a group holds a negative claim and a minimally-rewritten positive
claim, each with its own clause set and its own expected verdict. The ten locked
groups had no execution path at all.

The unit of independence is the group, not the claim (§8.5.4). Ten groups are
twenty invocations and `n=10`. Everything here is shaped to make that hard to
get wrong: cases are planned in group order, results are joined back by
`group_id`, and the matrix refuses a repeated group rather than counting it.

A confirmed negative is the measurement, not a fault. It is the end-to-end false
confirmation §8.5.3 reports, so it is recorded and returned; raising on it would
turn the number this subset exists to produce into a lost run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from specpilot.annotation.adversarial import AdversarialGroupStore
from specpilot.contracts.annotation import Split, Verdict
from specpilot.contracts.l2_adv import AdversarialGroup


class AdversarialRunError(ValueError):
    """A pair or a selection that must not become a reported number."""


class AdversarialRole(StrEnum):
    NEGATIVE = "negative"
    POSITIVE = "positive"


class PairOutcome(StrEnum):
    """Which halves of the pair the system got right."""

    BOTH = "both"
    NEGATIVE_ONLY = "negative_only"
    POSITIVE_ONLY = "positive_only"
    NEITHER = "neither"


@dataclass(frozen=True)
class AdversarialCase:
    """One claim, one provider invocation, one evaluation root."""

    group_id: str
    role: AdversarialRole
    case_id: str
    claim: str
    expected_verdict: Verdict


@dataclass(frozen=True)
class PairResult:
    group_id: str
    dimension: str
    negative_verdicts: tuple[Verdict, ...]
    positive_verdicts: tuple[Verdict, ...]
    outcome: PairOutcome
    false_confirmation: bool
    false_rejection: bool


def select_groups(
    store: AdversarialGroupStore, *, split: Split, expected: int
) -> tuple[AdversarialGroup, ...]:
    """The groups of one split, ordered by group id, or a refusal.

    Mirrors `evaluation.sweep.select_cases`: the count is supplied by the
    caller and checked, because a filter bug that silently shortens a one-shot
    locked run is the failure a first-run boundary cannot absorb.
    """
    selected = tuple(
        sorted(
            (item for item in store.read_all() if item.split is split),
            key=lambda item: item.group_id,
        )
    )
    if not selected:
        raise AdversarialRunError(
            f"adversarial_empty_selection: no group in split {split.value}"
        )
    if len(selected) != expected:
        raise AdversarialRunError(
            f"adversarial_count_mismatch: selected {len(selected)} group(s) "
            f"in split {split.value}, expected {expected}"
        )
    return selected


def plan_cases(groups: Sequence[AdversarialGroup]) -> tuple[AdversarialCase, ...]:
    """Two cases per group, negative first, groups kept adjacent.

    Adjacency is deliberate: a sweep interrupted midway then leaves whole pairs
    behind rather than orphaned halves, and `join_pair` refuses an orphan.
    """
    planned: list[AdversarialCase] = []
    for item in groups:
        planned.append(
            AdversarialCase(
                group_id=item.group_id,
                role=AdversarialRole.NEGATIVE,
                case_id=item.negative_claim_id,
                claim=item.negative_claim,
                expected_verdict=item.negative_expected_verdict,
            )
        )
        planned.append(
            AdversarialCase(
                group_id=item.group_id,
                role=AdversarialRole.POSITIVE,
                case_id=item.positive_claim_id,
                claim=item.positive_claim,
                expected_verdict=item.proposed_verdict,
            )
        )
    return tuple(planned)


def join_pair(
    group: AdversarialGroup,
    *,
    negative_verdicts: Sequence[Verdict],
    positive_verdicts: Sequence[Verdict],
) -> PairResult:
    """Score one group from the verdicts its two cases produced.

    The chain decomposes a claim into atomic claims, so each case can return
    several verdicts. The negative counts as correctly refused when **any** of
    them is `insufficient_evidence`: the adversarial claim as posed was not
    confirmed, which is what the subset asks. Requiring all of them would score
    a partial confirmation as a pass.

    The positive counts as correct only on the annotated verdict. A determinate
    verdict that disagrees with gold is a wrong answer, not a rejection, and is
    reported as neither.
    """
    if not negative_verdicts or not positive_verdicts:
        raise AdversarialRunError(
            f"adversarial_pair_incomplete: group {group.group_id!r} has "
            f"{len(negative_verdicts)} negative and {len(positive_verdicts)} "
            "positive verdict(s); a matched pair is the unit of measurement"
        )

    negative_refused = Verdict.INSUFFICIENT_EVIDENCE in negative_verdicts
    positive_correct = all(
        verdict is group.proposed_verdict for verdict in positive_verdicts
    )
    positive_withheld = Verdict.INSUFFICIENT_EVIDENCE in positive_verdicts

    if negative_refused and positive_correct:
        outcome = PairOutcome.BOTH
    elif negative_refused:
        outcome = PairOutcome.NEGATIVE_ONLY
    elif positive_correct:
        outcome = PairOutcome.POSITIVE_ONLY
    else:
        outcome = PairOutcome.NEITHER

    return PairResult(
        group_id=group.group_id,
        dimension=group.dimension.value,
        negative_verdicts=tuple(negative_verdicts),
        positive_verdicts=tuple(positive_verdicts),
        outcome=outcome,
        false_confirmation=not negative_refused,
        false_rejection=positive_withheld,
    )


def build_pair_matrix(results: Sequence[PairResult]) -> dict[str, object]:
    """The matched-pair confusion matrix, counted in groups.

    `group_count` is `n`. A repeated group refuses rather than inflating it:
    the three-run repeats of §8.5 are within-case, and folding them into this
    matrix is exactly the arithmetic the plan forbids.
    """
    seen = Counter(item.group_id for item in results)
    repeated = sorted(name for name, count in seen.items() if count > 1)
    if repeated:
        raise AdversarialRunError(
            f"adversarial_group_repeated: {', '.join(repeated)}; repeats are "
            "within-case and never widen n"
        )

    outcomes = {member.value: 0 for member in PairOutcome}
    dimensions: Counter[str] = Counter()
    for item in results:
        outcomes[item.outcome.value] += 1
        dimensions[item.dimension] += 1
    return {
        "group_count": len(results),
        "outcomes": outcomes,
        "false_confirmations": sum(1 for item in results if item.false_confirmation),
        "false_rejections": sum(1 for item in results if item.false_rejection),
        "dimension_counts": dict(sorted(dimensions.items())),
    }
