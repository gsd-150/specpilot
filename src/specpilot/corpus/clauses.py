"""The citable unit.

Two things pull in different directions. The source numbers sections, and a
citation names one — "RFC 9110 §3.7" is what a reader can check. The outbound
caps count tokens and bytes, and a section does not fit: measured on the frozen
corpus, RFC 9110's largest section is 995 words against a 512-token excerpt cap,
and RFC 9112's is 755.

The source resolves this itself. Its `pn` paragraph anchors delimit units that
always fit — the longest clause measures 208 tokens in RFC 9110 and 220 in RFC
9112, against BGE-M3's 8192. So a clause is a paragraph, carrying the section
number it belongs to. Splitting is never invented; it is read off boundaries the
document already published.

A clause holds no text. It is a locator with counts, which is what makes it safe
to record — the annotation records that reference clauses cannot leak clause
prose because a clause never had any. Text is reachable through
`iter_clause_texts`, which is a call you have to mean.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.walk import (
    document_identity,
    element_text,
    owned_paragraphs,
    parse_verified,
    sections,
    unit_identity,
)

CLAUSE_KIND = "clause"

# RFC 9110's Appendix A is a single 1221-word ABNF block — the only unit in
# either document that breaches the excerpt cap. It is excluded by anchor
# rather than by size, and on evidence rather than convenience: all 143 rule
# names it defines already appear in body grammar blocks, and none appears only
# there. Indexing it would add 143 near-duplicate candidates competing with the
# clauses that actually state the rules, which is exactly the near-duplicate
# hazard §8.1 groups against.
#
# Nothing else is excluded. Any other oversized block still raises, because a
# second one would mean something changed that nobody has looked at.
RFC_9110_EXCLUDED_SECTIONS = frozenset({"collected.abnf"})


@dataclass(frozen=True, slots=True)
class ClauseLimits:
    """Bounds a clause must respect to be excerptable at all.

    ``max_bytes`` mirrors the 8 KiB excerpt cap exactly. ``max_words`` is a
    deliberately conservative stand-in for the 512-token cap: the real count is
    model-specific and belongs to the enforcer, and clause boundaries must not
    move when the model does.
    """

    max_bytes: int = 8192
    max_words: int = 512
    excluded_sections: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Clause:
    clause_id: str
    document_id: str
    document_version: str
    section_anchor: str
    section_number: str | None
    section_path: str
    ordinal: int
    anchor: str | None
    byte_count: int
    word_count: int


class OversizedClauseError(ValueError):
    """A unit exceeds what any excerpt may carry.

    Raised rather than truncated. A silently shortened clause would be a
    citation pointing at text the reader cannot find, and the enforcer would
    reject it at egress anyway — better to fail here, where there is a document
    to look at. The cap is one cap: a table that breaches it raises this too.
    """


@dataclass(frozen=True, slots=True)
class SectionNormatives:
    """One section's size and how many requirements it states.

    A locator with counts, like `Clause`. It names where the requirements are;
    it does not say what they require, and it does not choose anything.
    """

    section_number: str | None
    section_path: str
    section_anchor: str
    clause_count: int
    word_count: int
    keyword_counts: dict[str, int]
    normative_total: int


def build_normative_index(
    path: Path,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> tuple[SectionNormatives, ...]:
    """Count each section's BCP 14 keywords, from the document's own markup.

    RFC v3 tags every normative keyword as `<bcp14>`, so this reads the
    author's own marking rather than guessing. Scanning for uppercase words
    instead would count ABNF rules, field names, and quoted examples as
    requirements, and the sections that looked most normative would be the ones
    with the most syntax in them.

    Section 8.2.1 permits this as an annotation aid because it is literal search
    over the frozen bytes — the same path as grep, not the system's retriever.
    It narrows where to read. It does not choose a clause or write a question.
    """
    root = parse_verified(path, rfc_limits)
    index: list[SectionNormatives] = []
    for section in sections(root):
        counts: dict[str, int] = {}
        clause_count = 0
        word_count = 0
        for paragraph in owned_paragraphs(section.element):
            text = element_text(paragraph)
            if not text:
                continue
            clause_count += 1
            word_count += len(text.split())
            for keyword in paragraph.iter("bcp14"):
                # "MUST NOT" is one keyword. Splitting on whitespace would
                # score it as a MUST, inverting the requirement it states.
                label = element_text(keyword)
                if label:
                    counts[label] = counts.get(label, 0) + 1
        index.append(
            SectionNormatives(
                section_number=section.number,
                section_path=section.path,
                section_anchor=section.anchor,
                clause_count=clause_count,
                word_count=word_count,
                keyword_counts=dict(sorted(counts.items())),
                normative_total=sum(counts.values()),
            )
        )
    return tuple(index)


def _clauses_with_text(
    path: Path,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> Iterator[tuple[Clause, str]]:
    root = parse_verified(path, rfc_limits)
    document_id, document_version = document_identity(root)

    for section in sections(root):
        if section.anchor in clause_limits.excluded_sections:
            continue
        ordinal = 0
        for paragraph in owned_paragraphs(section.element):
            text = element_text(paragraph)
            if not text:
                continue
            ordinal += 1
            byte_count = len(text.encode("utf-8"))
            word_count = len(text.split())
            if (
                byte_count > clause_limits.max_bytes
                or word_count > clause_limits.max_words
            ):
                raise OversizedClauseError(
                    f"paragraph {ordinal} of section {section.anchor} "
                    "exceeds the excerpt caps"
                )
            yield (
                Clause(
                    clause_id=unit_identity(
                        CLAUSE_KIND,
                        document_id,
                        document_version,
                        section.anchor,
                        ordinal,
                    ),
                    document_id=document_id,
                    document_version=document_version,
                    section_anchor=section.anchor,
                    section_number=section.number,
                    section_path=section.path,
                    ordinal=ordinal,
                    anchor=paragraph.get("pn") or paragraph.get("anchor"),
                    byte_count=byte_count,
                    word_count=word_count,
                ),
                text,
            )


def build_clauses(
    path: Path,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> tuple[Clause, ...]:
    """Return every clause in document order, without their text."""
    return tuple(
        clause for clause, _ in _clauses_with_text(path, rfc_limits, clause_limits)
    )


def iter_clause_texts(
    path: Path,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> Iterator[tuple[Clause, str]]:
    """Yield each clause with its text, for callers that genuinely need it."""
    yield from _clauses_with_text(path, rfc_limits, clause_limits)
