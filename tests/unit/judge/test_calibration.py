"""Tests for the §8.3.2 calibration mathematics.

Written RED-first per the judge scoring plan. The fixtures are hand-computed:
every expected number below was worked out on paper so a passing run means the
implementation reproduces the paper arithmetic, not that it agrees with itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from specpilot.contracts.scoring import (
    AnswerClaimJudgement,
    ClaimVerdict,
    HumanAnswerClaimLabel,
    HumanDevLabels,
    HumanKeyPointLabel,
    JudgeOutput,
    JudgeRecord,
    KeyPointHit,
)
from specpilot.judge.calibration import (
    build_calibration_report,
    cohens_kappa,
)

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


def _record(
    case_id: str,
    point_hits: tuple[tuple[str, bool], ...],
    claims: tuple[tuple[str, str, str, bool], ...] = (),
) -> JudgeRecord:
    return JudgeRecord(
        case_id=case_id,
        question_hash="1" * 64,
        final_answer_hash="2" * 64,
        prompt_hash="3" * 64,
        prompt_version="judge-v1",
        model_id="glm-5.2",
        output=JudgeOutput(
            key_point_hits=tuple(
                KeyPointHit(
                    point_id=point_id,
                    hit=hit,
                    miss_reason=None if hit else "missing",
                )
                for point_id, hit in point_hits
            ),
            answer_claims=tuple(
                AnswerClaimJudgement(
                    claim_id=claim_id,
                    claim="Extracted statement.",
                    verdict=ClaimVerdict(verdict),
                    severe=severe,
                )
                for claim_id, verdict, _, severe in claims
            ),
        ),
        scored_at=NOW,
    )


def _labels(
    case_id: str,
    point_hits: tuple[tuple[str, bool], ...],
    claims: tuple[tuple[str, str, bool], ...] = (),
) -> HumanDevLabels:
    return HumanDevLabels(
        case_id=case_id,
        labeler="chunxue",
        key_points=tuple(
            HumanKeyPointLabel(point_id=point_id, hit=hit)
            for point_id, hit in point_hits
        ),
        claims=tuple(
            HumanAnswerClaimLabel(
                claim_id=claim_id,
                verdict=ClaimVerdict(verdict),
                severe=severe,
            )
            for claim_id, verdict, severe in claims
        ),
        labelled_at=NOW,
    )


def test_perfect_agreement_gives_rate_one() -> None:
    report = build_calibration_report(
        (_record("c1", (("p1", True), ("p2", False))),),
        (_labels("c1", (("p1", True), ("p2", False))),),
    )
    assert report.case_count == 1
    assert report.key_points.n == 2
    assert report.key_points.agreed == 2
    assert report.key_points.agreement_rate == 1.0
    assert report.key_points.kappa == 1.0


def test_complete_disagreement_over_two_categories_is_kappa_minus_one() -> None:
    # Judge H M vs Human M H over uniform marginals: po = 0, pe = 0.5, kappa -1.
    report = build_calibration_report(
        (_record("c1", (("p1", True), ("p2", False))),),
        (_labels("c1", (("p1", False), ("p2", True))),),
    )
    assert report.key_points.agreement_rate == 0.0
    assert report.key_points.kappa == -1.0


def test_hand_computed_binary_kappa() -> None:
    # Judge: H H M M | Human: H M H M (by point id) → po = 2/4; both marginals
    # are 2/4 and 2/4, so pe = 0.5 and kappa = (0.5 - 0.5) / 0.5 = 0.0.
    report = build_calibration_report(
        (
            _record(
                "c1",
                (("p1", True), ("p2", True)),
            ),
            _record(
                "c2",
                (("p3", False), ("p4", False)),
            ),
        ),
        (
            _labels(
                "c1",
                (("p1", True), ("p2", False)),
            ),
            _labels(
                "c2",
                (("p3", True), ("p4", False)),
            ),
        ),
    )
    assert report.key_points.n == 4
    assert report.key_points.agreement_rate == pytest.approx(0.5)
    assert report.key_points.kappa == pytest.approx(0.0)


def test_three_class_claim_kappa_and_severe_flag() -> None:
    report = build_calibration_report(
        (
            _record(
                "c1",
                (("p1", True),),
                claims=(
                    ("a1", "supported", "", False),
                    ("a2", "contradicted", "", False),
                    ("a3", "insufficient", "", True),
                ),
            ),
        ),
        (
            _labels(
                "c1",
                (("p1", True),),
                claims=(
                    ("a1", "supported", False),
                    ("a2", "contradicted", False),
                    ("a3", "insufficient", False),
                ),
            ),
        ),
    )
    claims = report.claims
    assert claims.n == 3
    assert claims.agreement_rate == 1.0
    assert claims.kappa == 1.0
    assert claims.severe_both == 0
    assert claims.severe_judge_only == 1
    assert claims.severe_human_only == 0
    assert claims.severe_neither == 2
    assert claims.confusion["contradicted|contradicted"] == 1


def test_missing_labels_are_excluded_with_counts_not_guessed() -> None:
    report = build_calibration_report(
        (
            _record("c1", (("p1", True), ("p2", False), ("p3", True))),
            _record("c2", (("p1", True),)),
        ),
        (
            _labels("c1", (("p1", True), ("p2", False))),
        ),
    )
    assert report.case_count == 1
    assert report.excluded_cases_judge_only == 1
    assert report.key_points.n == 2
    assert report.key_points.excluded_judge_only == 1  # p3 unlabelled
    assert report.key_points.excluded_human_only == 0


def test_duplicate_case_ids_refuse() -> None:
    with pytest.raises(ValueError):
        build_calibration_report(
            (_record("c1", (("p1", True),)), _record("c1", (("p1", True),))),
            (_labels("c1", (("p1", True),)),),
        )


def test_degenerate_labeling_reports_none_kappa_instead_of_fabricating() -> None:
    report = build_calibration_report(
        (_record("c1", (("p1", True), ("p2", True))),),
        (_labels("c1", (("p1", True), ("p2", True))),),
    )
    # Perfect agreement, one category only: chance agreement is 1.0.
    assert report.key_points.kappa is None
    assert report.key_points.agreement_rate == 1.0


def test_cohens_kappa_refuses_unequal_raters() -> None:
    with pytest.raises(ValueError):
        cohens_kappa(["hit"], ["hit", "miss"])
