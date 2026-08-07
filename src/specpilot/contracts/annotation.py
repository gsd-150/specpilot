"""Annotation records shaped so the forbidden states cannot be written down.

Two rules from the product plan govern gold annotation, and both are the kind
that a tired annotator at 1 a.m. will not remember from a document.

Section 8.2.1 forbids gold discovered through the system's own retriever, since
measuring whether a retriever can re-find what it found is measuring nothing.
`IndependentPath` therefore has no value for retrieval: a record whose gold came
from the retriever has no representation.

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


class IndependentPath(StrEnum):
    """The paths section 8.2.1 permits for discovering initial gold.

    There is deliberately no member for retrieval, dense, sparse, hybrid, or
    pooling. Gold that came from the system cannot be recorded as gold.
    """

    SOURCE_TEXT_NAVIGATION = "source_text_navigation"
    LITERAL_SEARCH = "literal_search"
    CROSS_REFERENCE_TRACE = "cross_reference_trace"
    TERMINOLOGY_INDEX = "terminology_index"


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


class Adjudication(_FrozenModel):
    """One completeness-audit decision, per section 8.2.3.

    Retrieval may propose a candidate; this records that the author checked it
    against the source and what they decided.
    """

    candidate_origin: Identifier
    note: AdjudicationNote


class L1Annotation(_FrozenModel):
    schema_version: Literal["annotation-l1/v1"] = "annotation-l1/v1"
    item_id: Identifier
    split: Split
    question: QuestionText
    direction: QuestionDirection
    independent_path: IndependentPath
    document_id: Identifier
    document_version: Identifier
    gold_clause_ids: tuple[Sha256, ...] = ()
    gold_section_paths: tuple[SectionPath, ...] = ()
    key_points: tuple[KeyPoint, ...] = ()
    expected_refusal: StrictBool = False
    question_gold_jaccard: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
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
            if self.gold_clause_ids or self.gold_section_paths:
                raise ValueError("an unanswerable item may not carry gold")
            if self.question_gold_jaccard is not None:
                raise ValueError("an unanswerable item has no overlap figure")
            return self
        if not self.gold_clause_ids:
            raise ValueError("an answerable item requires at least one gold clause")
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

    schema_version: Literal["annotation-l2/v1"] = "annotation-l2/v1"  # type: ignore[assignment]
    claim_id: Identifier
    expected_verdict: Verdict
    proposed_verdict: Verdict
    supports_verdict: StrictBool
