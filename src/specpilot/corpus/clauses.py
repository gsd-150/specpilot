"""The citable unit.

Two things pull in different directions. The source numbers sections, and a
citation names one — "RFC 9110 §3.7" is what a reader can check. The outbound
caps count tokens and bytes, and a section does not fit: measured on the frozen
corpus, RFC 9110's largest section is 995 words against a 512-token excerpt cap,
and RFC 9112's is 755.

The source resolves this itself. Its `pn` paragraph anchors delimit units that
always fit — the largest paragraph is 982 bytes and 154 words in RFC 9110, 800
and 118 in RFC 9112. So a clause is a paragraph, carrying the section number it
belongs to. Splitting is never invented; it is read off boundaries the document
already published.

A clause holds no text. It is a locator with counts, which is what makes it safe
to record — the annotation records that reference clauses cannot leak clause
prose because a clause never had any. Text is reachable through
`iter_clause_texts`, which is a call you have to mean.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element  # noqa: S405 - parsed via defusedxml

from defusedxml.ElementTree import (  # type: ignore[import-untyped]
    fromstring as defused_fromstring,
)

from specpilot.contracts.rfc import RfcLimits
from specpilot.ingestion.rfc import inspect_rfc_xml


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
    """A paragraph exceeds what any excerpt may carry.

    Raised rather than truncated. A silently shortened clause would be a
    citation pointing at text the reader cannot find, and the enforcer would
    reject it at egress anyway — better to fail here, where there is a document
    to look at.
    """


def _document_identity(root: Element) -> tuple[str, str]:
    number = root.get("number") or "unknown"
    return f"ietf-rfc-{number}", root.get("version") or "3"


def _paragraph_text(element: Element) -> str:
    return " ".join("".join(element.itertext()).split())


# List items and definition bodies whose prose the source did not wrap in <t>.
# Measured on RFC 9110: 182 <li> holding 3689 words and 37 <dd> holding 454,
# among them 17 BCP 14 keywords — requirements that would otherwise sit in no
# clause at all, citable by nothing and reachable by no retriever.
_UNWRAPPED_PROSE_TAGS = frozenset({"li", "dd"})

# Deliberately not included. <dt> is a definition's term, 482 of them in RFC
# 9110 averaging under two words and stating no requirement; <td> and <th> are
# reference-table cells, meaningless as a citation on their own. Admitting them
# would add 800-odd near-empty targets to the index for no requirement gained.
_LABEL_TAGS = frozenset({"dt", "td", "th"})


def _owned_paragraphs(section: Element) -> Iterator[Element]:
    """Yield the paragraphs this section owns, in document order.

    Descends through lists, definition lists, and asides, because normative
    text lives there — measured on RFC 9110, 365 paragraphs sit inside <li>,
    33 inside <aside>, and 11 inside <dd>. Stops at a nested <section>, which
    owns its own paragraphs.
    """
    for child in section:
        if child.tag == "section":
            continue
        if child.tag == "t":
            yield child
        elif child.tag in _LABEL_TAGS:
            continue
        elif child.tag in _UNWRAPPED_PROSE_TAGS and child.find("t") is None:
            # Prose the source chose not to wrap. It is still the unit a
            # citation would name, and neither RFC nests a list inside such an
            # item, so yielding it here cannot double-count a child.
            yield child
        else:
            yield from _owned_paragraphs(child)


def _walk(
    parent: Element,
    *,
    prefix: str,
    titles: tuple[str, ...],
    found: list[tuple[Element, str | None, str, str]],
    lettered: bool = False,
    skip_unnumbered: bool = False,
) -> None:
    """Collect (section, number, path, anchor) in document order."""
    counter = 0
    for section in parent.findall("section"):
        numbered = section.get("numbered", "true") != "false"
        if skip_unnumbered and not numbered:
            # Back matter the document declined to number is apparatus, not
            # corpus: acknowledgements, the index, and Authors' Addresses. The
            # last carries contact details that must never reach an embedding
            # index or an outbound excerpt.
            continue
        number: str | None = None
        if numbered:
            counter += 1
            label = chr(ord("A") + counter - 1) if lettered else str(counter)
            number = f"{prefix}{label}"
        name = section.find("name")
        title = "".join(name.itertext()).strip() if name is not None else ""
        path = " > ".join((*titles, title)) if title else " > ".join(titles)
        anchor = section.get("anchor") or section.get("pn") or ""
        found.append((section, number, path or title, anchor))
        _walk(
            section,
            prefix=f"{number}." if number else prefix,
            titles=(*titles, title) if title else titles,
            found=found,
        )


def _parse(path: Path, rfc_limits: RfcLimits) -> Element:
    """Verify before parsing. Structure is never read from an unchecked file."""
    inspect_rfc_xml(path, rfc_limits)
    root: Element = defused_fromstring(
        path.read_text(encoding="utf-8"),
        forbid_dtd=True,
        forbid_entities=True,
        forbid_external=True,
    )
    return root


def _clauses_with_text(
    path: Path,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> Iterator[tuple[Clause, str]]:
    root = _parse(path, rfc_limits)
    document_id, document_version = _document_identity(root)

    sections: list[tuple[Element, str | None, str, str]] = []
    middle = root.find("middle")
    if middle is not None:
        _walk(middle, prefix="", titles=(), found=sections)
    back = root.find("back")
    if back is not None:
        # Appendices are lettered, because "Appendix A" is how one is cited.
        _walk(
            back,
            prefix="",
            titles=(),
            found=sections,
            lettered=True,
            skip_unnumbered=True,
        )

    for section, number, path_text, section_anchor in sections:
        ordinal = 0
        for paragraph in _owned_paragraphs(section):
            text = _paragraph_text(paragraph)
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
                    f"paragraph {ordinal} of section {section_anchor} "
                    "exceeds the excerpt caps"
                )
            # Separated by a unit separator, which cannot appear in any of
            # these components. Concatenating them directly makes section "1"
            # paragraph 12 and section "11" paragraph 2 the same string.
            identity = "\x1f".join(
                (document_id, document_version, section_anchor, str(ordinal))
            )
            yield (
                Clause(
                    clause_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    document_id=document_id,
                    document_version=document_version,
                    section_anchor=section_anchor,
                    section_number=number,
                    section_path=path_text,
                    ordinal=ordinal,
                    anchor=paragraph.get("pn") or paragraph.get("anchor"),
                    byte_count=byte_count,
                    word_count=word_count,
                ),
                text,
            )


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
    root = _parse(path, rfc_limits)
    sections: list[tuple[Element, str | None, str, str]] = []
    middle = root.find("middle")
    if middle is not None:
        _walk(middle, prefix="", titles=(), found=sections)
    back = root.find("back")
    if back is not None:
        _walk(
            back,
            prefix="",
            titles=(),
            found=sections,
            lettered=True,
            skip_unnumbered=True,
        )

    index: list[SectionNormatives] = []
    for section, number, path_text, anchor in sections:
        counts: dict[str, int] = {}
        clause_count = 0
        word_count = 0
        for paragraph in _owned_paragraphs(section):
            text = _paragraph_text(paragraph)
            if not text:
                continue
            clause_count += 1
            word_count += len(text.split())
            for keyword in paragraph.iter("bcp14"):
                # "MUST NOT" is one keyword. Splitting on whitespace would
                # score it as a MUST, inverting the requirement it states.
                label = " ".join("".join(keyword.itertext()).split())
                if label:
                    counts[label] = counts.get(label, 0) + 1
        index.append(
            SectionNormatives(
                section_number=number,
                section_path=path_text,
                section_anchor=anchor,
                clause_count=clause_count,
                word_count=word_count,
                keyword_counts=dict(sorted(counts.items())),
                normative_total=sum(counts.values()),
            )
        )
    return tuple(index)


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
