"""RED-first contract tests for the judge scoring models.

These tests were written before `specpilot.contracts.scoring` existed, as the
first task of the judge scoring plan
(`docs/superpowers/plans/2026-08-15-judge-scoring-calibration.md`). Every rule
here is one that a real calibration would silently fudge if it were absent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import JudgePayload, ScoringPoint
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

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
CASE_HASH = "a" * 64
ANSWER_HASH = "b" * 64
PROMPT_HASH = "c" * 64


def _payload() -> JudgePayload:
    return JudgePayload(
        query="What must the origin server send with a 405 response?",
        final_answer="The Allow header field.",
        scoring_points=(
            ScoringPoint(point_id="p1", text="Names the Allow requirement"),
            ScoringPoint(point_id="p2", text="Names the 405 status"),
        ),
    )


def _output() -> JudgeOutput:
    return JudgeOutput(
        key_point_hits=(
            KeyPointHit(point_id="p1", hit=True),
            KeyPointHit(point_id="p2", hit=False, miss_reason="405 not named"),
        ),
        answer_claims=(
            AnswerClaimJudgement(
                claim_id="c1",
                claim="The origin server sends Allow with 405.",
                verdict=ClaimVerdict.SUPPORTED,
            ),
        ),
    )


def test_a_hit_carries_no_reason_and_a_miss_must_carry_one() -> None:
    with pytest.raises(ValidationError):
        KeyPointHit(point_id="p1", hit=True, miss_reason="why")
    with pytest.raises(ValidationError):
        KeyPointHit(point_id="p1", hit=False)
    assert KeyPointHit(point_id="p1", hit=False, miss_reason="why").miss_reason


def test_a_claim_verdict_is_the_closed_triage() -> None:
    with pytest.raises(ValidationError):
        AnswerClaimJudgement(
            claim_id="c1", claim="A claim.", verdict="violating"  # type: ignore[arg-type]
        )
    assert (
        AnswerClaimJudgement(
            claim_id="c1", claim="A claim.", verdict=ClaimVerdict.CONTRADICTED
        ).severe
        is False
    )


def test_an_output_refuses_duplicate_identifiers_and_empty_points() -> None:
    with pytest.raises(ValidationError):
        JudgeOutput(
            key_point_hits=(
                KeyPointHit(point_id="p1", hit=True),
                KeyPointHit(point_id="p1", hit=True),
            )
        )
    with pytest.raises(ValidationError):
        JudgeOutput(
            key_point_hits=(KeyPointHit(point_id="p1", hit=True),),
            answer_claims=(
                AnswerClaimJudgement(
                    claim_id="c1", claim="A.", verdict=ClaimVerdict.SUPPORTED
                ),
                AnswerClaimJudgement(
                    claim_id="c1", claim="B.", verdict=ClaimVerdict.SUPPORTED
                ),
            ),
        )
    with pytest.raises(ValidationError):
        JudgeOutput(key_point_hits=())


def test_verify_against_refuses_both_directions_of_mismatch() -> None:
    payload = _payload()
    assert _output().verify_against(payload) is None
    with pytest.raises(ValueError):
        JudgeOutput(
            key_point_hits=(KeyPointHit(point_id="p1", hit=True),)
        ).verify_against(payload)
    with pytest.raises(ValueError):
        JudgeOutput(
            key_point_hits=(
                KeyPointHit(point_id="p1", hit=True),
                KeyPointHit(point_id="p2", hit=True),
                KeyPointHit(point_id="p3", hit=True),
            )
        ).verify_against(payload)


def test_a_judge_record_carries_hashes_not_prose() -> None:
    record = JudgeRecord(
        case_id="l1-dev-001",
        question_hash=CASE_HASH,
        final_answer_hash=ANSWER_HASH,
        prompt_hash=PROMPT_HASH,
        prompt_version="judge-v1",
        model_id="glm-5.2",
        output=_output(),
        scored_at=NOW,
    )
    assert record.question_hash == CASE_HASH
    with pytest.raises(ValidationError):
        JudgeRecord(
            case_id="l1-dev-001",
            question_hash="not-a-hash",
            final_answer_hash=ANSWER_HASH,
            prompt_hash=PROMPT_HASH,
            prompt_version="judge-v1",
            model_id="glm-5.2",
            output=_output(),
            scored_at=NOW,
        )


def test_human_labels_require_a_labeler_and_unique_ids() -> None:
    labels = HumanDevLabels(
        case_id="l1-dev-001",
        labeler="chunxue",
        key_points=(HumanKeyPointLabel(point_id="p1", hit=True),),
        claims=(
            HumanAnswerClaimLabel(
                claim_id="c1", verdict=ClaimVerdict.INSUFFICIENT, severe=True
            ),
        ),
        labelled_at=NOW,
    )
    assert labels.labeler == "chunxue"
    with pytest.raises(ValidationError):
        HumanDevLabels(
            case_id="l1-dev-001",
            labeler="chunxue",
            key_points=(
                HumanKeyPointLabel(point_id="p1", hit=True),
                HumanKeyPointLabel(point_id="p1", hit=False),
            ),
            labelled_at=NOW,
        )
    with pytest.raises(ValidationError):
        HumanDevLabels.model_validate(
            {
                "schema_version": "human-dev-labels/v1",
                "case_id": "l1-dev-001",
                "key_points": [],
                "claims": [],
                "labelled_at": NOW.isoformat(),
            }
        )
