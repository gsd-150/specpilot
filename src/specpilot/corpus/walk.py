"""Shared reading of the frozen RFC tree.

Clauses, the normative index, and tables all need the same three things: a tree
that has passed the boundary, the section list in document order with numbers
and paths resolved, and the prose a section owns. Three copies of that walk
would be three chances for them to disagree about what section 5.6.2 contains.

Nothing here returns text unless asked. `element_text` is the one call that
does, and callers that only need locators never reach it.
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

# List items and definition bodies whose prose the source did not wrap in <t>.
# Measured on RFC 9110: 182 <li> holding 3689 words and 37 <dd> holding 454,
# among them 17 BCP 14 keywords — requirements that would otherwise sit in no
# clause at all, citable by nothing and reachable by no retriever.
UNWRAPPED_PROSE_TAGS = frozenset({"li", "dd"})

# Blocks the source numbers in the same paragraph sequence as <t>. RFC 9110's
# §3.9 runs section-3.9-1 (t), -2 (t), -3 (sourcecode), -4 (t), -5 (sourcecode):
# the document itself counts an ABNF block as a numbered paragraph, so it is a
# clause and not a unit of its own. 366 sourcecode and 81 artwork blocks in RFC
# 9110 carry a `pn`, holding 2913 words that were in no clause before —
# including every ABNF rule name, which §4.1.6 calls this corpus's
# highest-signal sparse retrieval term.
BLOCK_TAGS = frozenset({"t", "sourcecode", "artwork"})

# Deliberately excluded. <dt> is a definition's term, 482 of them in RFC 9110
# averaging under two words and stating no requirement; <td> and <th> are
# reference-table cells, meaningless as a citation on their own. Tables are a
# unit of their own — see `corpus/tables.py` — rather than a heap of cells.
LABEL_TAGS = frozenset({"dt", "td", "th"})

# Separates the parts of a hashed identity. A unit separator cannot appear in
# any of them, so section "1" ordinal 12 and section "11" ordinal 2 stay
# distinct — concatenating them directly makes both "1112".
SEPARATOR = "\x1f"


@dataclass(frozen=True, slots=True)
class SectionRef:
    element: Element
    number: str | None
    path: str
    anchor: str


def unit_identity(
    kind: str,
    document_id: str,
    document_version: str,
    section_anchor: str,
    ordinal: int,
) -> str:
    """Hash one unit's citable identity, discriminated by kind.

    `kind` is not decoration. Tables and clauses are both numbered from one
    within their section, so without it table 1 of §5.6.2 and paragraph 1 of
    §5.6.2 hash to the same value, and a gold field naming one would silently
    resolve to the other.
    """
    joined = SEPARATOR.join(
        (kind, document_id, document_version, section_anchor, str(ordinal))
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def parse_verified(path: Path, rfc_limits: RfcLimits) -> Element:
    """Verify before parsing. The tree is never read from an unchecked file."""
    inspect_rfc_xml(path, rfc_limits)
    root: Element = defused_fromstring(
        path.read_text(encoding="utf-8"),
        forbid_dtd=True,
        forbid_entities=True,
        forbid_external=True,
    )
    return root


def document_identity(root: Element) -> tuple[str, str]:
    number = root.get("number") or "unknown"
    return f"ietf-rfc-{number}", root.get("version") or "3"


def _parts(element: Element) -> Iterator[str]:
    """Yield this element's text and its descendants', excluding its own tail.

    A plain `itertext()` walk loses cross-reference text. RFC v3 renders most
    references as an empty `<xref derivedContent="Section 3.1"/>`, putting the
    display text in the attribute — 1523 of RFC 9110's 2519 references and 304
    of RFC 9112's 458 are shaped that way. Flattened naively, a clause reads
    "separates semantics (this document) and caching () from the syntax ()",
    with the empty parentheses where the references were.

    That matters beyond readability. Section numbers are the highest-signal
    terms this corpus has for sparse retrieval, and every one cited in prose
    would be absent from the index.
    """
    if element.text:
        yield element.text
    for child in element:
        inner = "".join(_parts(child))
        if child.tag == "xref" and not inner.strip():
            inner = child.get("derivedContent") or inner
        yield inner
        if child.tail:
            yield child.tail


def element_text(element: Element) -> str:
    """Flatten an element to whitespace-normalized text."""
    return " ".join("".join(_parts(element)).split())


def owned_paragraphs(section: Element) -> Iterator[Element]:
    """Yield the paragraphs this section owns, in document order.

    Descends through lists, definition lists, and asides, because normative
    text lives there. Stops at a nested <section>, which owns its own.
    """
    for child in section:
        if child.tag == "section":
            continue
        if child.tag in BLOCK_TAGS:
            yield child
        elif child.tag in LABEL_TAGS:
            continue
        elif child.tag in UNWRAPPED_PROSE_TAGS and child.find("t") is None:
            # Prose the source chose not to wrap. It is still the unit a
            # citation would name, and neither RFC nests a list inside such an
            # item, so yielding it here cannot double-count a child.
            yield child
        else:
            yield from owned_paragraphs(child)


def owned_tables(section: Element) -> Iterator[Element]:
    """Yield the tables this section owns, in document order."""
    for child in section:
        if child.tag == "section":
            continue
        if child.tag == "table":
            yield child
        else:
            yield from owned_tables(child)


def _walk(
    parent: Element,
    *,
    prefix: str,
    titles: tuple[str, ...],
    found: list[SectionRef],
    lettered: bool = False,
    skip_unnumbered: bool = False,
) -> None:
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
        found.append(SectionRef(section, number, path or title, anchor))
        _walk(
            section,
            prefix=f"{number}." if number else prefix,
            titles=(*titles, title) if title else titles,
            found=found,
        )


def sections(root: Element) -> tuple[SectionRef, ...]:
    """Return every corpus section in document order, numbers resolved."""
    found: list[SectionRef] = []
    middle = root.find("middle")
    if middle is not None:
        _walk(middle, prefix="", titles=(), found=found)
    back = root.find("back")
    if back is not None:
        # Appendices are lettered, because "Appendix A" is how one is cited.
        _walk(
            back,
            prefix="",
            titles=(),
            found=found,
            lettered=True,
            skip_unnumbered=True,
        )
    return tuple(found)
