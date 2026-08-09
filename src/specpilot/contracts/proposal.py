"""A drafted item, before anyone has decided whether it is gold.

A proposal is a file, not a record. It lives outside the annotation store, is
never counted by `progress`, and cannot enter the store except through a
recorded review decision. That separation is the point: a model may draft
questions and name clauses all day, and none of it is gold until a human has
chosen it over alternatives they could have chosen instead.

What a proposal deliberately does **not** carry:

- **its distractors.** The reviewer's command selects them structurally from a
  seed given at review time. A drafter who supplied the wrong answers could
  supply obviously wrong ones and the forced choice would measure nothing.
- **the overlap figure.** Computed from the question and the chosen clause, at
  the same place `annotation add` computes it.
- **the section path.** Read off the chosen clause, so it cannot disagree with
  the clause it is supposed to describe.

`key_points` and `drafted_key_points` start out identical, and the reviewer
edits only the first. Whether they still match is then a fact about two lists
rather than an answer to "did you change anything?" — which is the kind of
self-report this whole workflow exists to remove.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from specpilot.contracts.annotation import (
    KeyPoint,
    QuestionDirection,
    QuestionText,
    Split,
)
from specpilot.contracts.manifests import Identifier, Sha256


class Proposal(BaseModel):
    """One drafted L1 item awaiting a forced-choice review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["annotation-proposal/v1"] = "annotation-proposal/v1"
    item_id: Identifier
    split: Split
    question: QuestionText
    direction: QuestionDirection
    document_id: Identifier
    document_version: Identifier
    proposal_producer: Identifier
    proposed_gold_clause_id: Sha256 | None = None
    expected_refusal: StrictBool = False
    drafted_key_points: tuple[KeyPoint, ...] = ()
    key_points: tuple[KeyPoint, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _the_editable_copy_starts_as_the_drafted_one(cls, data: Any) -> Any:
        """Written once by the drafter, editable in one of the two places.

        Asking the drafter to write both by hand would let them drift apart at
        the start, which would report an edit nobody made.
        """
        if isinstance(data, dict) and "key_points" not in data:
            return {**data, "key_points": data.get("drafted_key_points", ())}
        return data

    @model_validator(mode="after")
    def _a_proposal_proposes_exactly_one_thing(self) -> Self:
        """Either a clause to judge, or the claim that no clause answers it.

        An answerable draft with nothing to propose would present a choice with
        no right answer in it, and the reviewer would have no way to tell.
        """
        if self.expected_refusal:
            if self.proposed_gold_clause_id is not None:
                raise ValueError("an unanswerable proposal may not name a clause")
            return self
        if self.proposed_gold_clause_id is None:
            raise ValueError("an answerable proposal must name one clause")
        return self

    @property
    def key_points_edited(self) -> bool:
        return self.key_points != self.drafted_key_points


class UnsupportedProposalSchemaError(ValueError):
    pass


def proposal_for_schema(schema_version: object) -> type[Proposal]:
    if schema_version == "annotation-proposal/v1":
        return Proposal
    raise UnsupportedProposalSchemaError("unsupported proposal schema")


__all__ = [
    "Proposal",
    "UnsupportedProposalSchemaError",
    "proposal_for_schema",
]
