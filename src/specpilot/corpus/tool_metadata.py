"""Deterministic local-only metadata for the five RFC corpus tools.

This sidecar is separate from the frozen retrieval inventory. It does not add
fields to ``IndexUnit``, alter indexed text, or participate in the existing
corpus manifest. Its canonical hash lets a run bind the exact local metadata
algorithm and source snapshots it used without recording terms or source text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Final
from xml.etree.ElementTree import Element  # noqa: S405 - verified upstream

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import CLAUSE_KIND, ClauseLimits
from specpilot.corpus.indexable import IndexUnit
from specpilot.corpus.walk import (
    document_identity,
    element_text,
    owned_paragraphs,
    sections,
    unit_identity,
)
from specpilot.ingestion.rfc import RfcInput, ensure_verified_rfc

TOOL_METADATA_VERSION: Final = "rfc-tool-metadata/v1"
_IDENTIFIER_ATTRIBUTES: Final = ("anchor", "pn", "slugifiedName")
_REFERENCE_TAGS: Final = frozenset({"reference", "referencegroup"})


class ToolMetadataIntegrityError(ValueError):
    """The sidecar no longer matches its canonical payload or source corpus."""


class InvalidToolReferenceError(ValueError):
    """A recorded reference cannot be resolved to local clause identities."""


@dataclass(frozen=True, slots=True)
class ToolDocumentRecord:
    document_id: str
    source_sha256: str
    clause_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolClauseRecord:
    document_id: str
    clause_id: str
    normative_levels: tuple[str, ...]
    reference_clause_ids: tuple[str, ...]
    invalid_reference: bool


@dataclass(frozen=True, slots=True)
class ToolTermRecord:
    document_id: str
    normalized_term: str
    definition_clause_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RfcToolMetadata:
    schema_version: str
    corpus_manifest_id: str
    documents: tuple[ToolDocumentRecord, ...]
    clauses: tuple[ToolClauseRecord, ...]
    terms: tuple[ToolTermRecord, ...]
    metadata_hash: str

    def _canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "corpus_manifest_id": self.corpus_manifest_id,
            "documents": [asdict(record) for record in self.documents],
            "clauses": [asdict(record) for record in self.clauses],
            "terms": [asdict(record) for record in self.terms],
        }

    def verify_integrity(self) -> None:
        if self.schema_version != TOOL_METADATA_VERSION:
            raise ToolMetadataIntegrityError("unsupported tool metadata version")
        if self.metadata_hash != _canonical_hash(self._canonical_payload()):
            raise ToolMetadataIntegrityError("tool metadata digest mismatch")

    def document_ids(self) -> tuple[str, ...]:
        return tuple(record.document_id for record in self.documents)

    def source_hashes(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (record.document_id, record.source_sha256) for record in self.documents
        )

    def clause_ids(self) -> tuple[str, ...]:
        return tuple(record.clause_id for record in self.clauses)

    def normative_levels(self, clause_id: str) -> tuple[str, ...]:
        record = self._clause(clause_id)
        return record.normative_levels

    def expand(self, clause_id: str, *, limit: int = 3) -> tuple[str, ...]:
        if not 1 <= limit <= 3:
            raise ValueError("reference expansion limit must be between one and three")
        record = self._clause(clause_id)
        if record.invalid_reference:
            raise InvalidToolReferenceError("invalid reference")
        return record.reference_clause_ids[:limit]

    def lookup(self, term: str, *, document_id: str | None = None) -> tuple[str, ...]:
        normalized = normalize_tool_term(term)
        clause_ids: list[str] = []
        for record in self.terms:
            if record.normalized_term != normalized:
                continue
            if document_id is not None and record.document_id != document_id:
                continue
            clause_ids.extend(record.definition_clause_ids)
        return tuple(dict.fromkeys(clause_ids))

    def _clause(self, clause_id: str) -> ToolClauseRecord:
        for record in self.clauses:
            if record.clause_id == clause_id:
                return record
        raise KeyError(clause_id)


def normalize_tool_term(term: str) -> str:
    """Normalize only the lookup key; source labels remain local to the sidecar."""
    return " ".join(term.casefold().split())


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _descendants_without_nested_sections(
    element: Element, tag: str
) -> Iterable[Element]:
    for child in element:
        if child.tag == "section":
            continue
        if child.tag == tag:
            yield child
        yield from _descendants_without_nested_sections(child, tag)


def _target_registry(root: Element) -> dict[str, list[tuple[str, Element]]]:
    targets: dict[str, list[tuple[str, Element]]] = {}
    for element in root.iter():
        kind = "bibliography" if element.tag in _REFERENCE_TAGS else "internal"
        for attribute in _IDENTIFIER_ATTRIBUTES:
            value = element.get(attribute)
            if value:
                registrations = targets.setdefault(value, [])
                if all(registered is not element for _, registered in registrations):
                    registrations.append((kind, element))
    return targets


def _definition_records(
    section: Element,
    *,
    document_id: str,
    paragraph_ids: dict[int, str],
) -> list[ToolTermRecord]:
    by_term: dict[str, list[str]] = {}
    for definition_list in _descendants_without_nested_sections(section, "dl"):
        current_term: str | None = None
        for child in definition_list:
            if child.tag == "dt":
                normalized = normalize_tool_term(element_text(child))
                current_term = normalized or None
                continue
            if child.tag != "dd" or current_term is None:
                continue
            definitions = [
                paragraph_ids[id(element)]
                for element in child.iter()
                if id(element) in paragraph_ids
            ]
            by_term.setdefault(current_term, []).extend(definitions)
    return [
        ToolTermRecord(
            document_id=document_id,
            normalized_term=term,
            definition_clause_ids=tuple(dict.fromkeys(clause_ids)),
        )
        for term, clause_ids in by_term.items()
        if clause_ids
    ]


def build_rfc_tool_metadata(
    *,
    corpus_manifest_id: str,
    documents: Sequence[tuple[RfcInput, ClauseLimits]],
    units: Sequence[IndexUnit],
    rfc_limits: RfcLimits,
) -> RfcToolMetadata:
    """Build the runtime sidecar from verified XML and frozen clause order."""
    unit_ids = {unit.unit_id for unit in units}
    expected_clause_order = tuple(
        unit.unit_id for unit in units if unit.kind == CLAUSE_KIND
    )
    document_records: list[ToolDocumentRecord] = []
    clause_records: list[ToolClauseRecord] = []
    term_records: list[ToolTermRecord] = []

    for source, clause_limits in documents:
        verified = ensure_verified_rfc(source, rfc_limits)
        root = verified.root
        document_id, document_version = document_identity(root)
        targets = _target_registry(root)
        paragraph_ids: dict[int, str] = {}
        section_clause_ids: dict[int, tuple[str, ...]] = {}
        ordered_clause_ids: list[str] = []

        for section in sections(root):
            if section.anchor in clause_limits.excluded_sections:
                continue
            owned_ids: list[str] = []
            for ordinal, paragraph in enumerate(
                owned_paragraphs(section.element), start=1
            ):
                clause_id = unit_identity(
                    CLAUSE_KIND,
                    document_id,
                    document_version,
                    section.anchor,
                    ordinal,
                )
                if clause_id not in unit_ids:
                    raise ToolMetadataIntegrityError(
                        "derived clause identity is absent from retrieval inventory"
                    )
                paragraph_ids[id(paragraph)] = clause_id
                owned_ids.append(clause_id)
                ordered_clause_ids.append(clause_id)
            section_clause_ids[id(section.element)] = tuple(owned_ids)

        document_expected = tuple(
            unit.unit_id
            for unit in units
            if unit.kind == CLAUSE_KIND and unit.document_id == document_id
        )
        if tuple(ordered_clause_ids) != document_expected:
            raise ToolMetadataIntegrityError(
                "tool metadata clause order differs from retrieval inventory"
            )

        for section in sections(root):
            if section.anchor in clause_limits.excluded_sections:
                continue
            paragraphs = tuple(owned_paragraphs(section.element))
            for paragraph in paragraphs:
                clause_id = paragraph_ids[id(paragraph)]
                levels = tuple(
                    sorted(
                        {
                            element_text(keyword)
                            for keyword in paragraph.iter("bcp14")
                            if element_text(keyword)
                        }
                    )
                )
                reference_ids: list[str] = []
                invalid_reference = False
                for reference in paragraph.iter("xref"):
                    target = reference.get("target")
                    matches = targets.get(target or "", [])
                    if len(matches) != 1:
                        invalid_reference = True
                        continue
                    kind, target_element = matches[0]
                    if kind == "bibliography":
                        invalid_reference = True
                    elif id(target_element) in paragraph_ids:
                        reference_ids.append(paragraph_ids[id(target_element)])
                    elif id(target_element) in section_clause_ids:
                        reference_ids.extend(section_clause_ids[id(target_element)])
                    else:
                        invalid_reference = True
                clause_records.append(
                    ToolClauseRecord(
                        document_id=document_id,
                        clause_id=clause_id,
                        normative_levels=levels,
                        reference_clause_ids=tuple(
                            candidate
                            for candidate in dict.fromkeys(reference_ids)
                            if candidate != clause_id
                        ),
                        invalid_reference=invalid_reference,
                    )
                )
            term_records.extend(
                _definition_records(
                    section.element,
                    document_id=document_id,
                    paragraph_ids=paragraph_ids,
                )
            )

        document_records.append(
            ToolDocumentRecord(
                document_id=document_id,
                source_sha256=verified.inspection.document_sha256,
                clause_ids=tuple(ordered_clause_ids),
            )
        )

    if tuple(record.clause_id for record in clause_records) != expected_clause_order:
        raise ToolMetadataIntegrityError(
            "tool metadata corpus order differs from retrieval inventory"
        )
    draft = RfcToolMetadata(
        schema_version=TOOL_METADATA_VERSION,
        corpus_manifest_id=corpus_manifest_id,
        documents=tuple(document_records),
        clauses=tuple(clause_records),
        terms=tuple(term_records),
        metadata_hash="",
    )
    result = RfcToolMetadata(
        schema_version=draft.schema_version,
        corpus_manifest_id=draft.corpus_manifest_id,
        documents=draft.documents,
        clauses=draft.clauses,
        terms=draft.terms,
        metadata_hash=_canonical_hash(draft._canonical_payload()),
    )
    result.verify_integrity()
    return result
