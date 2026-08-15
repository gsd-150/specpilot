"""Tests for the §8.4 answer-metric aggregation.

Written RED-first per the judge scoring plan; numbers are hand-computed from
the fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from specpilot.contracts.scoring import (
    AnswerClaimJudgement,
    ClaimVerdict,
    JudgeOutput,
    JudgeRecord,
    KeyPointHit,
)
from specpilot.judge.aggregate import build_answer_metrics

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


def _record(
    case_id: str,
    hits: tuple[tuple[str, bool], ...],
    claims: tuple[tuple[str, str, bool], ...] = (),
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
                for point_id, hit in hits
            ),
            answer_claims=tuple(
                AnswerClaimJudgement(
                    claim_id=claim_id,
                    claim="Extracted statement.",
                    verdict=ClaimVerdict(verdict),
                    severe=severe,
                )
                for claim_id, verdict, severe in claims
            ),
        ),
        scored_at=NOW,
    )


def test_kp_recall_matches_the_hand_computed_fixture() -> None:
    metrics = build_answer_metrics(
        (
            _record("q1", (("p1", True), ("p2", False), ("p3", True))),
            _record("q2", (("p1", True), ("p2", True))),
        )
    )
    assert metrics.question_count == 2
    assert metrics.per_question_kp_recall == {"q1": 2 / 3, "q2": 1.0}
    assert metrics.macro_kp_recall == pytest.approx((2 / 3 + 1.0) / 2)


def test_claim_rates_and_severe_error_count() -> None:
    metrics = build_answer_metrics(
        (
            _record(
                "q1",
                (("p1", True),),
                claims=(
                    ("a1", "supported", False),
                    ("a2", "insufficient", False),
                    ("a3", "contradicted", True),
                ),
            ),
            _record(
                "q2",
                (("p1", True),),
                claims=(("a1", "insufficient", True),),
            ),
        )
    )
    assert metrics.unsupported_answer_claim_rate.numerator == 2
    assert metrics.unsupported_answer_claim_rate.denominator == 4
    assert metrics.unsupported_answer_claim_rate.rate == pytest.approx(0.5)
    assert metrics.gold_contradiction_rate.rate == pytest.approx(0.25)
    assert metrics.severe_error_question_count == 2


def test_empty_claim_rates_are_none_not_zero() -> None:
    metrics = build_answer_metrics((_record("q1", (("p1", True),)),))
    assert metrics.unsupported_answer_claim_rate.rate is None
    assert metrics.unsupported_answer_claim_rate.denominator == 0
    assert metrics.gold_contradiction_rate.rate is None


def test_duplicate_case_ids_refuse() -> None:
    with pytest.raises(ValueError):
        build_answer_metrics(
            (_record("q1", (("p1", True),)), _record("q1", (("p1", True),)))
        )
