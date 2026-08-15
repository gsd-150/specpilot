"""Every clause that uses a term, so a negative claim can actually be checked.

An adversarial negative asserts something about the whole corpus: that no clause
supports the stated verdict. Inspecting the distractor you already chose says
nothing about that, and the failure is silent — a pair looks adversarial right
up until the clause you never read turns out to support the conclusion outright.
Two drafted groups were lost that way, one to a sender prohibition two sections
from the clause in hand, one to a MUST governing the same omission the chosen
distractor merely discouraged.

Neither was near the clause it invalidated, so nothing positional would have
raised them. Both used its terms.

This is literal search over the frozen bytes, which §8.2.1 permits as an
annotation aid on the same footing as `grep`, and it is emphatically not the
system's retriever. That matters twice here. Feeding ranked hits into gold
construction is the circularity §8.2.1 forbids, and separately, a list that
stops at the top k cannot answer an exhaustiveness question — the clause that
refutes you is exactly the one a relevance score is free to rank last. So there
is no query, no score, and no limit, in the signature or anywhere else.

Nothing here reads out clause text. A hit is a locator with counts, so the aid
narrows where to read without putting prose anywhere it should not be.
"""

from __future__ import annotations

from dataclasses import dataclass

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, _clauses_with_text
from specpilot.corpus.walk import (
    element_text,
    owned_paragraphs,
    parse_verified,
    sections,
)
from specpilot.ingestion.rfc import RfcInput


@dataclass(frozen=True, slots=True)
class RequirementHit:
    """A locator with counts, like `Clause`, plus what it was matched on."""

    clause_id: str
    section_number: str | None
    section_path: str
    ordinal: int
    word_count: int
    matched_terms_lower: tuple[str, ...]
    keyword_counts: dict[str, int]


def _keyword_counts_by_clause(
    source: RfcInput, rfc_limits: RfcLimits, clause_limits: ClauseLimits
) -> dict[tuple[str, int], dict[str, int]]:
    """BCP 14 keywords per paragraph, read from the document's own markup.

    Keyed the way `_clauses_with_text` walks, so the two agree on what a clause
    is. "MUST NOT" stays one keyword: splitting on whitespace would score it as
    a MUST and invert the requirement, which is the difference the
    normative-strength axis is built on.
    """
    root = parse_verified(source, rfc_limits)
    counts: dict[tuple[str, int], dict[str, int]] = {}
    for section in sections(root):
        if section.anchor in clause_limits.excluded_sections:
            continue
        ordinal = 0
        for paragraph in owned_paragraphs(section.element):
            text = element_text(paragraph)
            if not text:
                continue
            ordinal += 1
            found: dict[str, int] = {}
            for keyword in paragraph.iter("bcp14"):
                label = element_text(keyword)
                if label:
                    found[label] = found.get(label, 0) + 1
            counts[(section.anchor, ordinal)] = found
    return counts


def scan_requirements(
    source: RfcInput,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
    *,
    terms: tuple[str, ...],
    keywords: tuple[str, ...] = (),
) -> tuple[RequirementHit, ...]:
    """Every clause containing all of ``terms``, in document order.

    ``terms`` is conjunctive and compared case-insensitively against the
    clause's own text. ``keywords`` optionally narrows to clauses stating at
    least one of those BCP 14 strengths, matched exactly so that "MUST" does not
    select a "MUST NOT".

    Refuses an empty ``terms``: an unfiltered walk is not an exhaustiveness
    check, it is the document, and returning it would let a caller believe the
    question had been asked.
    """
    if not terms:
        raise ValueError("a requirement scan needs at least one term")

    wanted = tuple(term.strip().lower() for term in terms if term.strip())
    if not wanted:
        raise ValueError("a requirement scan needs at least one non-empty term")

    per_clause = _keyword_counts_by_clause(source, rfc_limits, clause_limits)
    hits: list[RequirementHit] = []
    for clause, text in _clauses_with_text(source, rfc_limits, clause_limits):
        lowered = text.lower()
        if not all(term in lowered for term in wanted):
            continue
        counts = per_clause.get((clause.section_anchor, clause.ordinal), {})
        if keywords and not any(keyword in counts for keyword in keywords):
            continue
        hits.append(
            RequirementHit(
                clause_id=clause.clause_id,
                section_number=clause.section_number,
                section_path=clause.section_path,
                ordinal=clause.ordinal,
                word_count=clause.word_count,
                matched_terms_lower=wanted,
                keyword_counts=dict(sorted(counts.items())),
            )
        )
    return tuple(hits)
