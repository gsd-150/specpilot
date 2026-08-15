"""Adversarial L2 groups, shaped so a half-built pair cannot be written down.

Section 8.1.1 builds the one situation the deterministic checks in §4.3 cannot
settle: the citation exists, the version is right, and the conclusion still does
not follow. The unanswerable items already in the store do not reach it — those
find no evidence, while this subset finds evidence that looks highly relevant
and does not support the claim.

A group is one negative scenario together with one minimally rewritten positive
claim, and the pair is the measurement rather than two records that happen to
sit near each other. The negative catches a Verifier that waves a topically
close distractor through; the positive catches one that has learned to refuse
everything. Either alone is unfalsifiable, so they are one record and a group
cannot be stored half built.

Section 8.1 forbids clause prose in a committable record. A claim is authored
text, not source text, and is bounded here at the same 1,024 characters as a
question — room to state a claim, far short of reproducing a clause.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from specpilot.contracts.annotation import (
    AnnotationOrigin,
    GoldOrigin,
    GoldOriginEvent,
    QuestionText,
    Split,
    Verdict,
)
from specpilot.contracts.manifests import Identifier, Sha256


class AdversarialDimension(StrEnum):
    """The axis on which the distractor differs from the claim it fails to support.

    These replace the 3GPP axes the plan first named. A distractor is only
    adversarial if it is topically close and differs on exactly one recorded
    axis, so §8.1.1 requires every group to name the axis it was built on —
    which is what lets the report give a dimension distribution rather than one
    undifferentiated count of sixteen.
    """

    REQUEST_VS_RESPONSE = "request_vs_response"
    ROLE_ATTRIBUTION = "role_attribution"
    DOCUMENT_ATTRIBUTION = "document_attribution"
    NORMATIVE_STRENGTH = "normative_strength"
    RECEIVED_VS_GENERATED = "received_vs_generated"


class AdversarialGroup(BaseModel):
    """One negative scenario and its matched positive claim.

    ``negative_expected_verdict`` is fixed rather than chosen. The end-to-end
    negative exists to observe whether the system cites the distractor and
    concludes anyway, so a determinate gold verdict here would score the run for
    reaching the conclusion the case was built to refuse.

    ``distractor_clause_ids`` and ``supporting_clause_ids`` may overlap. The
    positive claim is the negative one minimally rewritten to match the
    distractor's applicability, so the clause that was the distractor is often
    exactly what supports the rewrite — that is the construction, not a mistake.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["l2-adv-group/v1"] = "l2-adv-group/v1"
    group_id: Identifier
    family: Identifier
    split: Split
    dimension: AdversarialDimension
    negative_claim_id: Identifier
    negative_claim: QuestionText
    distractor_clause_ids: tuple[Sha256, ...] = Field(min_length=1)
    negative_expected_verdict: Literal[Verdict.INSUFFICIENT_EVIDENCE] = (
        Verdict.INSUFFICIENT_EVIDENCE
    )
    positive_claim_id: Identifier
    positive_claim: QuestionText
    supporting_clause_ids: tuple[Sha256, ...] = Field(min_length=1)
    proposed_verdict: Verdict
    content_origin: AnnotationOrigin
    label_origin: AnnotationOrigin
    construction_origins: tuple[GoldOriginEvent, ...] = Field(min_length=1)
    group_record_id: Sha256 | None = None

    @model_validator(mode="after")
    def _require_a_human_source_check(self) -> Self:
        """§8.2 keeps the subset from measuring whoever proposed it.

        A clause is only a distractor because a reader confirmed against the
        source that it is topically close and still fails to support the claim.
        Nothing downstream can recover that: the negative and the positive are
        indistinguishable from a wrong pair until someone has read the clause.
        The origin chain may start with a model proposal or a retrieval hit, but
        it has to end at a person.
        """
        if not any(
            event.origin is GoldOrigin.HUMAN_SOURCE_REVIEW
            for event in self.construction_origins
        ):
            raise ValueError(
                "an adversarial group requires a human_source_review origin"
            )
        return self

    @model_validator(mode="after")
    def _refuse_a_pair_built_from_one_claim(self) -> Self:
        """§8.1.1 pairs two different claims, and never one claim twice.

        Labelling a single unsupported claim as also supported by some other
        evidence measures nothing: whichever way the Verifier answers, one of
        the two labels calls it correct. The pair only isolates the semantic
        step when the positive is a genuinely different claim that genuinely
        holds.
        """
        if self.negative_claim_id == self.positive_claim_id:
            raise ValueError("a matched pair needs two different claim identifiers")
        if self.negative_claim.strip() == self.positive_claim.strip():
            raise ValueError("a matched pair needs two different claims")
        return self

    @model_validator(mode="after")
    def _verify_group_record_id(self) -> Self:
        if self.group_record_id is not None:
            from specpilot.manifests.canonical import canonical_sha256

            if self.group_record_id != canonical_sha256(self):
                raise ValueError("group_record_id does not match canonical content")
        return self
