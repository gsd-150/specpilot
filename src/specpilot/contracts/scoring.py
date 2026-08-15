"""Closed contracts for the §8.3 judge and its calibration records.

The scorer is only trustworthy because it can be compared against human labels,
and these models exist to make that comparison refuse to be fudged: a judge
output cannot carry duplicate identifiers, a missed key point must say why, and
a stored record carries hashes instead of prose so the freeze-facing evidence
can be derived from it without redaction.

§8.1 keeps source text out of committable records. These records go further:
they never carry the question, the answer, or the gold excerpts — only their
hashes — because `evaluation freeze-candidate` recursively rejects exactly
those keys, and the evidence file is built from these records.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from specpilot.contracts.egress import JudgePayload
from specpilot.contracts.manifests import Identifier, Sha256

ClaimText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_024),
]
MissReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimVerdict(StrEnum):
    """§8.3.1's per-answer-claim triage, named like the L2 verdict space.

    A judge claim verdict and a task-level `Verdict` are different objects and
    must not share a type: the task verdict is what the system concluded, the
    claim verdict is whether one extracted statement inside the answer is
    supported by, contradicted by, or absent from the gold evidence.
    """

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"


class KeyPointHit(_FrozenModel):
    """One gold scoring point, hit or missed, with the miss explained.

    The reason is required exactly when the point is missed: a miss with no
    reason cannot be calibrated against human labels, and a hit with a reason
    is a hidden caveat on a binary label.
    """

    point_id: Identifier
    hit: bool
    miss_reason: MissReason | None = None

    @model_validator(mode="after")
    def _reason_matches_hit(self) -> Self:
        if self.hit and self.miss_reason is not None:
            raise ValueError("a hit key point must not carry a miss reason")
        if not self.hit and self.miss_reason is None:
            raise ValueError("a missed key point must carry a miss reason")
        return self


class AnswerClaimJudgement(_FrozenModel):
    """One extracted answer statement and the judge's classification of it."""

    claim_id: Identifier
    claim: ClaimText
    verdict: ClaimVerdict
    severe: bool = False


class JudgeOutput(_FrozenModel):
    """Everything the judge is asked to return, and nothing else.

    Duplicate identifiers are refused here rather than at aggregation time so a
    malformed reply can never be stored or joined. The payload-matching check
    is a method, not a validator: the output cannot see the payload it was
    produced for, and the caller owns that join.
    """

    schema_version: Literal["judge-output/v1"] = "judge-output/v1"
    key_point_hits: tuple[KeyPointHit, ...] = Field(min_length=1)
    answer_claims: tuple[AnswerClaimJudgement, ...] = ()

    @model_validator(mode="after")
    def _unique_identifiers(self) -> Self:
        point_ids = [hit.point_id for hit in self.key_point_hits]
        if len(set(point_ids)) != len(point_ids):
            raise ValueError("key point ids must be unique")
        claim_ids = [claim.claim_id for claim in self.answer_claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("answer claim ids must be unique")
        return self

    def verify_against(self, payload: JudgePayload) -> None:
        """Refuse an output that scored points the payload never named.

        The reverse direction is also refused: a silently dropped scoring point
        is a judgement the record never contains, and §8.4's KPRecall needs
        exactly the gold set to be scored.
        """
        expected = {point.point_id for point in payload.scoring_points}
        actual = {hit.point_id for hit in self.key_point_hits}
        if actual != expected:
            raise ValueError("judge output key points do not match the payload")


class JudgeRecord(_FrozenModel):
    """One stored judge call, prose-free by construction.

    The question, answer, and gold excerpts are hashes, not text, so the
    calibration evidence derived from a set of records satisfies the freeze
    reader's recursive prose-key rejection without a redaction step.
    """

    schema_version: Literal["judge-record/v1"] = "judge-record/v1"
    case_id: Identifier
    question_hash: Sha256
    final_answer_hash: Sha256
    prompt_hash: Sha256
    prompt_version: Identifier
    model_id: Identifier
    output: JudgeOutput
    scored_at: datetime


class HumanKeyPointLabel(_FrozenModel):
    """The author's binary label for one gold scoring point.

    Deliberately lighter than `KeyPointHit`: the §8.3.2 audit compares hit/miss
    only, and the human label set must not inherit the judge's reason field or
    its presence rules.
    """

    point_id: Identifier
    hit: bool


class HumanAnswerClaimLabel(_FrozenModel):
    """The author's classification of one judge-extracted answer claim."""

    claim_id: Identifier
    verdict: ClaimVerdict
    severe: bool = False


class HumanDevLabels(_FrozenModel):
    """The author's two independent label sets for one scored dev case.

    Kept separate from the judge record until the calibration join, so a human
    label set cannot be silently overwritten by a re-run of the judge.
    """

    schema_version: Literal["human-dev-labels/v1"] = "human-dev-labels/v1"
    case_id: Identifier
    labeler: Identifier
    key_points: tuple[HumanKeyPointLabel, ...] = ()
    claims: tuple[HumanAnswerClaimLabel, ...] = ()
    labelled_at: datetime

    @model_validator(mode="after")
    def _unique_identifiers(self) -> Self:
        point_ids = [label.point_id for label in self.key_points]
        if len(set(point_ids)) != len(point_ids):
            raise ValueError("human key point ids must be unique")
        claim_ids = [label.claim_id for label in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("human answer claim ids must be unique")
        return self
