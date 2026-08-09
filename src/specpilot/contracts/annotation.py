"""Annotation records shaped so the forbidden states cannot be written down.

Two rules from the product plan govern gold annotation, and both are the kind
that a tired annotator at 1 a.m. will not remember from a document.

Gold origins are recorded as auditable events. They describe how a candidate
entered the annotation process; they do not gate whether source-checked gold
may be stored.

Section 8.1 forbids clause prose and quotations from entering anything
committable. No field here can hold them. `KeyPoint` is a criterion with room
for necessary factual values — a timer default, a state name, a range — and is
bounded far below a clause, so a pasted quotation does not fit.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from specpilot.contracts.manifests import Identifier, Sha256

QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_024),
]
CriterionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
FactualValue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
SectionPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
AdjudicationNote = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_024),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnnotationOrigin(StrEnum):
    HUMAN = "human"
    MODEL = "model"
    MIXED = "mixed"


class GoldOrigin(StrEnum):
    SOURCE_TEXT_NAVIGATION = "source_text_navigation"
    LITERAL_SEARCH = "literal_search"
    CROSS_REFERENCE_TRACE = "cross_reference_trace"
    TERMINOLOGY_INDEX = "terminology_index"
    HUMAN_SOURCE_REVIEW = "human_source_review"
    MODEL_PROPOSAL = "model_proposal"
    SEARCH_CLAUSES = "search_clauses"
    DENSE_RETRIEVAL = "dense_retrieval"
    BM25_RETRIEVAL = "bm25_retrieval"
    HYBRID_RETRIEVAL = "hybrid_retrieval"


class GoldOriginEvent(_FrozenModel):
    origin: GoldOrigin
    producer: Identifier | None = None

    @model_validator(mode="after")
    def _require_the_right_producer(self) -> Self:
        automated_origins = {
            GoldOrigin.MODEL_PROPOSAL,
            GoldOrigin.SEARCH_CLAUSES,
            GoldOrigin.DENSE_RETRIEVAL,
            GoldOrigin.BM25_RETRIEVAL,
            GoldOrigin.HYBRID_RETRIEVAL,
        }
        if self.origin in automated_origins and self.producer is None:
            raise ValueError("model and retrieval gold origins require a producer")
        if self.origin not in automated_origins and self.producer is not None:
            raise ValueError("human and source gold origins may not name a producer")
        return self


class QuestionDirection(StrEnum):
    CLAUSE_FIRST = "clause_first"
    SCENARIO_FIRST = "scenario_first"


class Split(StrEnum):
    DEV = "dev"
    LOCKED = "locked"


class Verdict(StrEnum):
    COMPLIANT = "compliant"
    VIOLATING = "violating"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class KeyPoint(_FrozenModel):
    """A judgement criterion, not a restatement of the clause.

    ``criterion`` is bounded at 256 characters — enough to say what must be
    present, far short of reproducing a clause, whose median in the frozen
    corpus is over 900 bytes.
    """

    point_id: Identifier
    criterion: CriterionText
    factual_values: tuple[FactualValue, ...] = ()

    @model_validator(mode="after")
    def _refuse_a_verdict_as_a_factual_value(self) -> Self:
        """§8.1 keeps task-level gold and scoring criteria in separate fields.

        A verdict label sitting in a key point makes an answer scoreable for
        producing the word rather than the reasoning, and duplicates a value
        that ``expected_verdict`` already owns — so the two could disagree.
        """
        labels = {verdict.value for verdict in Verdict}
        for value in self.factual_values:
            if value.strip().lower() in labels:
                raise ValueError(
                    "a verdict label belongs in expected_verdict, "
                    "not in a key point's factual values"
                )
        return self


class Adjudication(_FrozenModel):
    """One completeness-audit decision, per section 8.2.3.

    Retrieval may propose a candidate; this records that the author checked it
    against the source and what they decided.
    """

    candidate_origin: Identifier
    note: AdjudicationNote


class ReviewOutcome(StrEnum):
    ACCEPTED_AS_PROPOSED = "accepted_as_proposed"
    GOLD_CHANGED = "gold_changed"
    ITEM_REJECTED = "item_rejected"


class ReviewDecision(_FrozenModel):
    """What a forced-choice review of a drafted proposal decided.

    `gold_origins` already records that a human reviewed a model proposal. It
    does not record what the review found, so a reviewer who approves
    everything and one who catches real errors leave identical records — and
    gold is the ruler, so a wrong gold makes every downstream metric wrong with
    nothing to catch it.

    This is a record beside the annotation rather than a field on it, for two
    reasons found by trying the other way first. Annotations are content
    addressed, so adding any field — even one defaulting to null — changes the
    canonical bytes and makes every stored record unreadable. And an
    annotation's identity should not move because somebody reviewed it: the
    question, the gold, and the key points are what the item *is*, while a
    review is a later judgement about it by a different actor.

    There is no note field. A free-text justification is unfalsifiable, invites
    clause prose into a committable record, and lets a shallow review look
    thorough. What the report needs is how often the reviewer disagreed, and
    that is a number.
    """

    schema_version: Literal["annotation-review/v1"] = "annotation-review/v1"
    # Not `annotation_id`: `canonical_json` strips that name as a record's own
    # content ID, so a foreign key spelled that way is silently dropped from
    # the bytes it is hashed over and from the file written.
    #
    # Null exactly when the item was rejected, because a rejected proposal never
    # became an annotation. The decision is still stored — see the class
    # docstring — but it points at nothing, since there is nothing.
    reviewed_annotation_id: Sha256 | None = None
    item_id: Identifier
    outcome: ReviewOutcome
    candidates_shown: Annotated[int, Field(ge=0)]
    chose_proposal: StrictBool
    reviewer_id: Identifier
    proposal_producer: Identifier
    key_points_edited: StrictBool = False
    deep_reviewed: StrictBool = False
    unanswerable: StrictBool = False
    review_id: Sha256 | None = None

    @model_validator(mode="after")
    def _verify_review_id(self) -> Self:
        if self.review_id is not None:
            from specpilot.manifests.canonical import canonical_sha256

            if self.review_id != canonical_sha256(self):
                raise ValueError("review_id does not match canonical content")
        return self

    @model_validator(mode="after")
    def _one_fact_not_two(self) -> Self:
        """`chose_proposal` and the outcome say the same thing, so they agree.

        Two fields that can drift apart eventually do, and the acceptance rate
        is computed from one of them.
        """
        accepted = self.outcome is ReviewOutcome.ACCEPTED_AS_PROPOSED
        if self.chose_proposal != accepted:
            raise ValueError(
                "chose_proposal must be true exactly when the outcome is "
                "accepted_as_proposed"
            )
        return self

    @model_validator(mode="after")
    def _a_rejection_points_at_nothing_and_everything_else_points_somewhere(
        self,
    ) -> Self:
        """A rejected proposal produced no record; every other outcome did.

        Rejections have to be stored or the acceptance rate is 100% by
        construction, which is the exact shape of a number that reassures and
        measures nothing. They just have no annotation to name.
        """
        rejected = self.outcome is ReviewOutcome.ITEM_REJECTED
        if rejected and self.reviewed_annotation_id is not None:
            raise ValueError("a rejected proposal never became an annotation")
        if not rejected and self.reviewed_annotation_id is None:
            raise ValueError("a review that produced gold must name the record")
        return self

    @model_validator(mode="after")
    def _a_choice_needs_something_to_choose_between(self) -> Self:
        """Two candidates minimum, except where there is nothing to choose.

        An unanswerable item's review confirms that no clause in the document
        answers the question, which is a different act with no candidate set.
        """
        if self.unanswerable:
            if self.candidates_shown:
                raise ValueError(
                    "an unanswerable item's review has no candidates to show"
                )
            return self
        if self.candidates_shown < 2:
            raise ValueError("a choice between fewer than two candidates is not one")
        return self


class L1Annotation(_FrozenModel):
    schema_version: Literal["annotation-l1/v2"] = "annotation-l1/v2"
    item_id: Identifier
    split: Split
    question: QuestionText
    direction: QuestionDirection
    content_origin: AnnotationOrigin
    label_origin: AnnotationOrigin
    document_id: Identifier
    document_version: Identifier
    gold_clause_ids: tuple[Sha256, ...] = ()
    gold_section_paths: tuple[SectionPath, ...] = ()
    key_points: tuple[KeyPoint, ...] = ()
    expected_refusal: StrictBool = False
    question_gold_jaccard: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    gold_origins: tuple[GoldOriginEvent, ...] = ()
    predecessor_annotation_id: Sha256 | None = None
    adjudications: tuple[Adjudication, ...] = ()
    annotation_id: Sha256 | None = None

    @model_validator(mode="after")
    def _verify_annotation_id(self) -> Self:
        if self.annotation_id is not None:
            from specpilot.manifests.canonical import canonical_sha256

            if self.annotation_id != canonical_sha256(self):
                raise ValueError("annotation_id does not match canonical content")
        return self

    @model_validator(mode="after")
    def _enforce_answerability(self) -> Self:
        if self.expected_refusal:
            if self.gold_clause_ids or self.gold_section_paths or self.gold_origins:
                raise ValueError("an unanswerable item may not carry gold")
            if self.question_gold_jaccard is not None:
                raise ValueError("an unanswerable item has no overlap figure")
            return self
        if not self.gold_clause_ids:
            raise ValueError("an answerable item requires at least one gold clause")
        if not self.gold_origins:
            raise ValueError("an answerable item requires at least one gold origin")
        if self.question_gold_jaccard is None:
            # Section 8.2.2 reports Macro-Recall stratified by literal overlap,
            # so an answerable item without the figure cannot be stratified.
            raise ValueError("an answerable item requires its overlap figure")
        return self


class L2Annotation(L1Annotation):
    """Adds the conformance labels, keeping the two families apart.

    Section 8.1 is explicit that task-level gold and the Verifier's support
    relation use different fields and must not substitute for one another, so
    they are separate fields here rather than one reused verdict.
    """

    schema_version: Literal["annotation-l2/v2"] = "annotation-l2/v2"  # type: ignore[assignment]
    claim_id: Identifier
    expected_verdict: Verdict
    proposed_verdict: Verdict
    supports_verdict: StrictBool


class UnsupportedAnnotationSchemaError(ValueError):
    pass


def annotation_model_for_schema(
    schema_version: object,
) -> type[L1Annotation] | type[L2Annotation]:
    if schema_version == "annotation-l1/v2":
        return L1Annotation
    if schema_version == "annotation-l2/v2":
        return L2Annotation
    raise UnsupportedAnnotationSchemaError("unsupported annotation schema")
