"""§8.3.2 calibration mathematics: judge-human agreement, kappa, confusion.

Pure functions only. The two label sets — gold key points and extracted answer
claims — are computed and reported separately and are never merged, because
§8.3.2 requires two independently reported agreements and mixing them would
produce one number that describes neither.

Every agreement carries its `n`; where Cohen's kappa is mathematically
undefined (no pairs, or a labelling so degenerate that chance agreement is
1.0) the field is `None` rather than a fabricated number. A label present on
only one side is excluded with a count, never guessed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from specpilot.contracts.scoring import (
    ClaimVerdict,
    HumanDevLabels,
    JudgeRecord,
)

_BINARY_CATEGORIES = ("hit", "miss")
_CLAIM_CATEGORIES = tuple(verdict.value for verdict in ClaimVerdict)


def cohens_kappa(rater_a: list[str], rater_b: list[str]) -> float | None:
    """Standard Cohen's kappa over two raters' category labels.

    `None` when the statistic is undefined: no observations, or a degenerate
    labelling where every observation shares one category and chance agreement
    is 1.0 — reporting 0 or 1 there would both be fabrication.
    """
    if len(rater_a) != len(rater_b):
        raise ValueError("raters must agree in length")
    if not rater_a:
        return None
    n = len(rater_a)
    observed = sum(1 for a, b in zip(rater_a, rater_b, strict=True) if a == b) / n
    counter_a = Counter(rater_a)
    counter_b = Counter(rater_b)
    categories = set(counter_a) | set(counter_b)
    if len(categories) <= 1:
        return None
    expected = sum(
        (counter_a[category] / n) * (counter_b[category] / n)
        for category in categories
    )
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def _confusion(
    pairs: list[tuple[str, str]], categories: tuple[str, ...]
) -> dict[str, int]:
    table = Counter(pairs)
    return {
        f"{judge}|{human}": table[(judge, human)]
        for judge in categories
        for human in categories
    }


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True, slots=True)
class KeyPointAgreement:
    """The §8.3.2 key-point label set, on its own."""

    n: int
    agreed: int
    agreement_rate: float | None
    kappa: float | None
    confusion: dict[str, int] = field(default_factory=dict)
    excluded_judge_only: int = 0
    excluded_human_only: int = 0


@dataclass(frozen=True, slots=True)
class ClaimAgreement:
    """The §8.3.2 answer-claim label set, on its own.

    The verdict triage and the severe flag are two judgements inside one label
    set; the flag agreement is reported beside the triage, not merged into it.
    """

    n: int
    agreed: int
    agreement_rate: float | None
    kappa: float | None
    confusion: dict[str, int] = field(default_factory=dict)
    severe_both: int = 0
    severe_human_only: int = 0
    severe_judge_only: int = 0
    severe_neither: int = 0
    excluded_judge_only: int = 0
    excluded_human_only: int = 0


class CalibrationReport(_FrozenModel):
    """The §8.3.2 report over one matched set of records and human labels.

    Frozen and timestamp-free so its canonical bytes are stable: the freeze
    evidence file derives from this report, and a report that changed on every
    serialization could never be hashed twice.
    """

    schema_version: Literal["judge-calibration/v1"] = "judge-calibration/v1"
    case_count: int = Field(ge=0)
    excluded_cases_judge_only: int = Field(ge=0)
    excluded_cases_human_only: int = Field(ge=0)
    key_points: KeyPointAgreement
    claims: ClaimAgreement


def _rate(agreed: int, n: int) -> float | None:
    if n == 0:
        return None
    return agreed / n


def build_calibration_report(
    records: tuple[JudgeRecord, ...], labels: tuple[HumanDevLabels, ...]
) -> CalibrationReport:
    """Join judge records and human labels by case id and compare them.

    Duplicate case ids on either side refuse: a duplicated record would let one
    case count twice and inflate `n` without being visible in the report.
    """
    record_ids = [record.case_id for record in records]
    label_ids = [label.case_id for label in labels]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("duplicate judge record case ids")
    if len(set(label_ids)) != len(label_ids):
        raise ValueError("duplicate human label case ids")
    by_record = {record.case_id: record for record in records}
    by_label = {label.case_id: label for label in labels}
    matched = sorted(set(by_record) & set(by_label))
    excluded_cases_judge_only = len(set(by_record) - set(by_label))
    excluded_cases_human_only = len(set(by_label) - set(by_record))

    point_pairs: list[tuple[str, str]] = []
    point_excluded_judge = 0
    point_excluded_human = 0
    claim_pairs: list[tuple[str, str]] = []
    claim_excluded_judge = 0
    claim_excluded_human = 0
    severe_flags: list[tuple[bool, bool]] = []

    for case_id in matched:
        record = by_record[case_id]
        label = by_label[case_id]

        judge_points = {hit.point_id: hit for hit in record.output.key_point_hits}
        human_points = {entry.point_id: entry for entry in label.key_points}
        for point_id in sorted(set(judge_points) & set(human_points)):
            point_pairs.append(
                (
                    "hit" if judge_points[point_id].hit else "miss",
                    "hit" if human_points[point_id].hit else "miss",
                )
            )
        point_excluded_judge += len(set(judge_points) - set(human_points))
        point_excluded_human += len(set(human_points) - set(judge_points))

        judge_claims = {
            claim.claim_id: claim for claim in record.output.answer_claims
        }
        human_claims = {entry.claim_id: entry for entry in label.claims}
        for claim_id in sorted(set(judge_claims) & set(human_claims)):
            judge_claim = judge_claims[claim_id]
            human_claim = human_claims[claim_id]
            claim_pairs.append((judge_claim.verdict.value, human_claim.verdict.value))
            severe_flags.append((judge_claim.severe, human_claim.severe))
        claim_excluded_judge += len(set(judge_claims) - set(human_claims))
        claim_excluded_human += len(set(human_claims) - set(judge_claims))

    point_agreed = sum(1 for a, b in point_pairs if a == b)
    claim_agreed = sum(1 for a, b in claim_pairs if a == b)
    severe_counts = Counter(severe_flags)

    return CalibrationReport(
        case_count=len(matched),
        excluded_cases_judge_only=excluded_cases_judge_only,
        excluded_cases_human_only=excluded_cases_human_only,
        key_points=KeyPointAgreement(
            n=len(point_pairs),
            agreed=point_agreed,
            agreement_rate=_rate(point_agreed, len(point_pairs)),
            kappa=cohens_kappa(
                [a for a, _ in point_pairs], [b for _, b in point_pairs]
            ),
            confusion=_confusion(point_pairs, _BINARY_CATEGORIES),
            excluded_judge_only=point_excluded_judge,
            excluded_human_only=point_excluded_human,
        ),
        claims=ClaimAgreement(
            n=len(claim_pairs),
            agreed=claim_agreed,
            agreement_rate=_rate(claim_agreed, len(claim_pairs)),
            kappa=cohens_kappa(
                [a for a, _ in claim_pairs], [b for _, b in claim_pairs]
            ),
            confusion=_confusion(claim_pairs, _CLAIM_CATEGORIES),
            severe_both=severe_counts[(True, True)],
            severe_human_only=severe_counts[(False, True)],
            severe_judge_only=severe_counts[(True, False)],
            severe_neither=severe_counts[(False, False)],
            excluded_judge_only=claim_excluded_judge,
            excluded_human_only=claim_excluded_human,
        ),
    )
