"""Check a model's citations against the frozen corpus, and refuse if they fail.

This is the load-bearing claim. Everything else in the project — the frozen
manifests, the content-addressed clauses, the disclosure ledger, the gold
protocol — exists so that this function can be written, and it is worth nothing
unless it can turn a fluent answer into a refusal.

Four faults, each a different way a citation can be untrue, kept apart because
they say different things about where the system went wrong:

- **unknown_clause** — the model named a clause the corpus does not contain. It
  invented a locator.
- **not_disclosed** — the clause exists, but was not among the excerpts sent.
  The model cited something it never saw, so whatever it said about that clause
  came from training rather than from the source in front of it. This is the
  one a naive implementation misses, because the citation checks out against
  the corpus and looks perfectly valid.
- **content_drift** — the clause was sent, but its text no longer hashes to what
  the citation claims. The corpus moved under the answer.
- **cross_manifest** — citations spanning two frozen corpora, which §6.4 makes a
  fail-closed condition because no single version statement covers them.

A determinate answer carrying any fault becomes a refusal. Not a warning beside
the answer: an answer whose support is unverifiable is exactly the confident
wrong output the project is built to prevent, and shipping it with a footnote
would be shipping it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from specpilot.contracts.answer import (
    AnswerVerdict,
    Citation,
    RefusalReason,
    VerifiedAnswer,
)


class CitationFault(StrEnum):
    UNKNOWN_CLAUSE = "unknown_clause"
    NOT_DISCLOSED = "not_disclosed"
    CONTENT_DRIFT = "content_drift"
    CROSS_MANIFEST = "cross_manifest"


@dataclass(frozen=True, slots=True)
class DisclosedClause:
    """One clause as it was actually sent, which is what a citation is checked
    against — not as it exists in the corpus, which is a weaker test."""

    clause_id: str
    corpus_manifest_id: str
    document_id: str
    document_version: str
    content_hash: str
    section_number: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimedCitation:
    """A clause id the model named, before anything is known about it."""

    clause_id: str
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class CitationCheck:
    clause_id: str
    fault: CitationFault | None
    citation: Citation | None

    @property
    def verified(self) -> bool:
        return self.fault is None


def check_citation(
    claimed: ClaimedCitation,
    disclosed: Mapping[str, DisclosedClause],
    *,
    corpus_manifest_id: str,
) -> CitationCheck:
    """Resolve one claimed clause against what was disclosed for this request."""
    found = disclosed.get(claimed.clause_id)
    if found is None:
        # Deliberately not "look it up in the corpus, and if it is there accept
        # it". A clause the model never saw cannot be its evidence, however real
        # the clause is.
        return CitationCheck(claimed.clause_id, CitationFault.UNKNOWN_CLAUSE, None)
    if found.corpus_manifest_id != corpus_manifest_id:
        return CitationCheck(claimed.clause_id, CitationFault.CROSS_MANIFEST, None)
    if claimed.content_hash is not None and claimed.content_hash != found.content_hash:
        return CitationCheck(claimed.clause_id, CitationFault.CONTENT_DRIFT, None)
    return CitationCheck(
        claimed.clause_id,
        None,
        Citation(
            clause_id=found.clause_id,
            corpus_manifest_id=found.corpus_manifest_id,
            document_id=found.document_id,
            document_version=found.document_version,
            section_number=found.section_number,
            content_hash=found.content_hash,
        ),
    )


def verify_answer(
    *,
    answer_text: str | None,
    claimed_citations: Sequence[ClaimedCitation],
    disclosed: Sequence[DisclosedClause],
    corpus_manifest_id: str,
    model_refused: bool = False,
) -> VerifiedAnswer:
    """Turn a raw model reply into a verdict the report can stand behind.

    ``model_refused`` is the model's own decision to decline, kept separate from
    every other path: "the model read the evidence and said it was not enough"
    and "the model answered but its citations did not check out" are different
    findings, and §8.4 counts them separately.
    """
    if not disclosed:
        # Nothing was sent, so nothing could have supported an answer. Reported
        # as its own reason: this is a retrieval failure wearing the costume of
        # a reasoning failure if it is folded into the next one.
        return VerifiedAnswer(
            verdict=AnswerVerdict.REFUSED,
            refusal_reason=RefusalReason.NO_EVIDENCE_RETRIEVED,
        )
    if model_refused or not answer_text:
        return VerifiedAnswer(
            verdict=AnswerVerdict.REFUSED,
            refusal_reason=RefusalReason.EVIDENCE_INSUFFICIENT,
        )

    by_id = {clause.clause_id: clause for clause in disclosed}
    checks = [
        check_citation(claimed, by_id, corpus_manifest_id=corpus_manifest_id)
        for claimed in _deduplicate(claimed_citations)
    ]
    faults = tuple(
        f"{check.clause_id[:16]}:{check.fault.value}"
        for check in checks
        if check.fault is not None
    )
    verified = tuple(
        check.citation for check in checks if check.citation is not None
    )

    if faults or not verified:
        # Any fault sinks the whole answer. Keeping the verified citations and
        # dropping the bad ones would publish a conclusion that was reasoned
        # from evidence partly outside the corpus, with nothing in the output
        # saying so.
        return VerifiedAnswer(
            verdict=AnswerVerdict.REFUSED,
            refusal_reason=RefusalReason.UNVERIFIABLE_CITATION,
            citation_faults=faults or ("no_citation",),
        )

    return VerifiedAnswer(
        verdict=AnswerVerdict.ANSWERED,
        answer=answer_text,
        citations=verified,
    )


def _deduplicate(claimed: Sequence[ClaimedCitation]) -> list[ClaimedCitation]:
    """Keep the first mention of each clause, in order.

    A model repeating a clause id is not a fault — it is how prose reads — but
    counting it twice would let one verified clause satisfy a requirement for
    two.
    """
    seen: set[str] = set()
    unique: list[ClaimedCitation] = []
    for citation in claimed:
        if citation.clause_id in seen:
            continue
        seen.add(citation.clause_id)
        unique.append(citation)
    return unique


__all__ = [
    "CitationCheck",
    "CitationFault",
    "ClaimedCitation",
    "DisclosedClause",
    "check_citation",
    "verify_answer",
]
