"""§8.4 answer-metric aggregation over judge records.

Deterministic only: every rate carries its numerator, denominator, and
question count, and a rate whose denominator is zero is `None`, never zero —
zero would claim a measurement happened. Refusal metrics stay with the answer
records (the refusal flag is in `VerifiedAnswer`, not in a judge output), so
they are deliberately absent here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from specpilot.contracts.manifests import Identifier
from specpilot.contracts.scoring import ClaimVerdict, JudgeRecord


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Rate(_FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = None


def _rate(numerator: int, denominator: int) -> Rate:
    return Rate(
        numerator=numerator,
        denominator=denominator,
        rate=None if denominator == 0 else numerator / denominator,
    )


class AnswerMetrics(_FrozenModel):
    """§8.4's judge-derived answer metrics, one object per question set."""

    schema_version: Literal["answer-metrics/v1"] = "answer-metrics/v1"
    question_count: int = Field(ge=0)
    per_question_kp_recall: dict[Identifier, float]
    macro_kp_recall: float | None = None
    unsupported_answer_claim_rate: Rate
    gold_contradiction_rate: Rate
    severe_error_question_count: int = Field(ge=0)


def build_answer_metrics(records: tuple[JudgeRecord, ...]) -> AnswerMetrics:
    """Aggregate judge records into the §8.4 answer metrics.

    Duplicate case ids refuse: the per-question recall map would otherwise
    silently overwrite one question with another.
    """
    case_ids = [record.case_id for record in records]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate judge record case ids")

    per_question: dict[str, float] = {}
    unsupported = 0
    contradicted = 0
    claim_total = 0
    severe_questions = 0

    for record in records:
        points = record.output.key_point_hits
        if points:
            hits = sum(1 for hit in points if hit.hit)
            per_question[record.case_id] = hits / len(points)
        claims = record.output.answer_claims
        claim_total += len(claims)
        unsupported += sum(
            1 for claim in claims if claim.verdict is ClaimVerdict.INSUFFICIENT
        )
        contradicted += sum(
            1 for claim in claims if claim.verdict is ClaimVerdict.CONTRADICTED
        )
        if any(claim.severe for claim in claims):
            severe_questions += 1

    recall_values = list(per_question.values())
    return AnswerMetrics(
        question_count=len(records),
        per_question_kp_recall=per_question,
        macro_kp_recall=(
            None if not recall_values else sum(recall_values) / len(recall_values)
        ),
        unsupported_answer_claim_rate=_rate(unsupported, claim_total),
        gold_contradiction_rate=_rate(contradicted, claim_total),
        severe_error_question_count=severe_questions,
    )
