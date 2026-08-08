"""Sections and cross-references from RFC v3 XML.

This is the payoff of the corpus change. In the DOCX path a cross-reference had
to be recovered from text patterns; here `<xref target="..."/>` is an element,
so a reference is read rather than guessed.

Nothing in this module fetches anything. A cross-reference is recorded with its
target and whether that target resolves inside the document; resolving it across
documents is a corpus-level concern, and following it over the network is never
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from xml.etree.ElementTree import Element  # noqa: S405 - parsed via defusedxml

from specpilot.contracts.rfc import RfcLimits
from specpilot.ingestion.rfc import RfcInput, ensure_verified_rfc


class CrossReferenceKind(StrEnum):
    INTERNAL = "internal"
    INTER_DOCUMENT = "inter_document"
    DANGLING = "dangling"


class DuplicateAnchorError(ValueError):
    """Two definitions of one anchor make every reference to it ambiguous."""


@dataclass(frozen=True, slots=True)
class RfcSection:
    anchor: str
    number: str | None
    title: str
    depth: int


@dataclass(frozen=True, slots=True)
class RfcCrossReference:
    source_anchor: str | None
    target: str
    kind: CrossReferenceKind

    @property
    def resolved(self) -> bool:
        return self.kind is not CrossReferenceKind.DANGLING


@dataclass(frozen=True, slots=True)
class RfcStructure:
    document_sha256: str
    sections: tuple[RfcSection, ...] = field(default_factory=tuple)
    cross_references: tuple[RfcCrossReference, ...] = field(default_factory=tuple)

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def dangling_count(self) -> int:
        return sum(1 for x in self.cross_references if not x.resolved)


def _text_of(element: Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


# An xref target can name three different things in prepped v3 XML, and only
# collecting the first of them makes a clean document look broken. Measured on
# RFC 9110: `anchor` alone leaves 970 of 2519 targets unresolved, adding `pn`
# leaves 305, and adding `slugifiedName` leaves none.
_IDENTIFIER_ATTRIBUTES = ("anchor", "pn", "slugifiedName")

# Anchors on these elements name another document rather than a place in this
# one, which is what separates an inter-document reference from an internal one.
_REFERENCE_TAGS = frozenset({"reference", "referencegroup"})


def _register(anchors: dict[str, str], anchor: str, kind: str) -> None:
    if anchor in anchors:
        raise DuplicateAnchorError("duplicate anchor in document")
    anchors[anchor] = kind


def _collect_identifiers(root: Element) -> dict[str, str]:
    """Map every identifier in the document to whether it names another document."""
    anchors: dict[str, str] = {}
    for element in root.iter():
        kind = "reference" if element.tag in _REFERENCE_TAGS else "internal"
        for attribute in _IDENTIFIER_ATTRIBUTES:
            value = element.get(attribute)
            if value:
                _register(anchors, value, kind)
    return anchors


def _walk_sections(
    parent: Element,
    *,
    depth: int,
    prefix: str,
    sections: list[RfcSection],
) -> None:
    counter = 0
    for element in parent.findall("section"):
        anchor = element.get("anchor")
        numbered = element.get("numbered", "true") != "false"
        number: str | None = None
        if numbered:
            counter += 1
            number = f"{prefix}{counter}"
        if anchor:
            sections.append(
                RfcSection(
                    anchor=anchor,
                    number=number,
                    title=_text_of(element.find("name")),
                    depth=depth,
                )
            )
        _walk_sections(
            element,
            depth=depth + 1,
            prefix=f"{number}." if number else prefix,
            sections=sections,
        )


def _collect_cross_references(
    element: Element,
    anchors: dict[str, str],
    *,
    section_anchor: str | None,
    found: list[RfcCrossReference],
) -> None:
    """Walk once, carrying the nearest enclosing section.

    Iterating xrefs per section instead double-counts every reference inside a
    nested section — on RFC 9110 that turned 2,519 references into 4,657.
    """
    if element.tag == "xref":
        target = element.get("target")
        if target:
            defined = anchors.get(target)
            if defined == "reference":
                kind = CrossReferenceKind.INTER_DOCUMENT
            elif defined is not None:
                kind = CrossReferenceKind.INTERNAL
            else:
                # Reported, never dropped. A broken reference in a corpus chosen
                # for its cross-referencing is a finding about the corpus.
                kind = CrossReferenceKind.DANGLING
            found.append(
                RfcCrossReference(
                    source_anchor=section_anchor, target=target, kind=kind
                )
            )
        return

    current = element.get("anchor") if element.tag == "section" else None
    for child in element:
        _collect_cross_references(
            child,
            anchors,
            section_anchor=current or section_anchor,
            found=found,
        )


def extract_structure(source: RfcInput, limits: RfcLimits) -> RfcStructure:
    """Verify a document, then read its sections and cross-references.

    Accepts a path or a verified snapshot. Routing extraction through the
    boundary makes it impossible to read structure out of a document that was
    never verified.
    """
    verified = ensure_verified_rfc(source, limits)
    root = verified.root

    anchors = _collect_identifiers(root)

    sections: list[RfcSection] = []
    middle = root.find("middle")
    if middle is not None:
        _walk_sections(middle, depth=1, prefix="", sections=sections)

    cross_references: list[RfcCrossReference] = []
    _collect_cross_references(
        root, anchors, section_anchor=None, found=cross_references
    )

    return RfcStructure(
        document_sha256=verified.inspection.document_sha256,
        sections=tuple(sections),
        cross_references=tuple(cross_references),
    )
