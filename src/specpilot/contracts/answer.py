"""What a verified answer is allowed to be.

The project's claim is that a determinate conclusion carries a citation a reader
can check. That claim is only worth something if the system can *fail* it, so
this contract makes the failing states unwriteable rather than unlikely: a
citation always carries the frozen identity needed to look it up, and an
answered verdict cannot exist without at least one.

Refusal is a first-class verdict, not an error. §7 and §12.1 require the system
to decline when the evidence does not support a conclusion, and a refusal that
came back as an exception would be indistinguishable from a crash.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from specpilot.contracts.manifests import Identifier, Sha256

AnswerText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
SectionNumber = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


# What the model is told to return, kept beside the contract it has to satisfy.
# It lived next to the parser, which the provider adapter cannot import without
# inverting the layering — so it was written and never sent, and the first live
# call came back as prose.
REPLY_INSTRUCTIONS = """\
Answer only from the numbered excerpts provided. Reply with one JSON object and \
nothing else:

  {"sufficient": true, "answer": "...", "citations": ["<clause_id>", ...]}

Set "sufficient" to false, with an empty citation list and no answer, when the \
excerpts do not settle the question. Cite a clause_id only if that excerpt was \
given to you above. Do not cite anything you were not shown, and do not answer \
from memory of the specification."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Citation(_FrozenModel):
    """One clause, named the way a reader can find it again.

    Every field is needed to check it and none is decoration. Without the
    manifest pair the citation names a clause in no particular version of no
    particular corpus; without `content_hash` it names a location whose text may
    since have changed, which is the failure a frozen corpus exists to prevent.
    """

    clause_id: Sha256
    corpus_manifest_id: Sha256
    document_id: Identifier
    document_version: Identifier
    section_number: SectionNumber | None = None
    content_hash: Sha256


class AnswerVerdict(StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"


class RefusalReason(StrEnum):
    """Why the system declined, in terms that name a checkable condition.

    `no_evidence_retrieved` and `evidence_insufficient` are different failures
    and must not collapse: the first says retrieval found nothing to send, the
    second says the model read what was sent and declined. Reported as one
    number they would hide whether the retriever or the reasoning is at fault.
    """

    NO_EVIDENCE_RETRIEVED = "no_evidence_retrieved"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    UNVERIFIABLE_CITATION = "unverifiable_citation"


class VerifiedAnswer(_FrozenModel):
    schema_version: Literal["answer/v1"] = "answer/v1"
    verdict: AnswerVerdict
    answer: AnswerText | None = None
    citations: tuple[Citation, ...] = ()
    refusal_reason: RefusalReason | None = None
    citation_faults: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _an_answer_is_cited_and_a_refusal_is_not(self) -> Self:
        """The one invariant the whole project rests on.

        An answered verdict with no citation is precisely the output this system
        exists to make impossible, and a refusal carrying an answer would let a
        declined question be read as a conclusion.
        """
        if self.verdict is AnswerVerdict.ANSWERED:
            if not self.answer:
                raise ValueError("an answered verdict requires an answer")
            if not self.citations:
                raise ValueError("an answered verdict requires at least one citation")
            if self.refusal_reason is not None:
                raise ValueError("an answered verdict has no refusal reason")
            return self
        if self.answer is not None:
            raise ValueError("a refusal may not carry an answer")
        if self.citations:
            # A refusal that cites is claiming support for a conclusion it
            # declined to draw.
            raise ValueError("a refusal may not carry citations")
        if self.refusal_reason is None:
            raise ValueError("a refusal must say why")
        return self

    @model_validator(mode="after")
    def _citations_stay_within_one_frozen_corpus(self) -> Self:
        """§6.4: evidence and run carry one corpus_manifest_id, or fail closed."""
        if len({citation.corpus_manifest_id for citation in self.citations}) > 1:
            raise ValueError("citations cross corpus manifests")
        return self

    @model_validator(mode="after")
    def _one_clause_is_cited_once(self) -> Self:
        seen = [citation.clause_id for citation in self.citations]
        if len(set(seen)) != len(seen):
            raise ValueError("a clause is cited twice")
        return self


__all__ = [
    "REPLY_INSTRUCTIONS",
    "AnswerText",
    "AnswerVerdict",
    "Citation",
    "RefusalReason",
    "VerifiedAnswer",
]
