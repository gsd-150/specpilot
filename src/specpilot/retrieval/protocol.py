"""Frozen retrieval identities and stable ordering keys."""

from __future__ import annotations

import re
from dataclasses import dataclass

from specpilot.corpus.indexable import IndexUnit


def _appendix_number(label: str) -> int:
    value = 0
    for character in label.upper():
        if not "A" <= character <= "Z":
            raise ValueError("appendix label is not alphabetic")
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _is_decimal(part: str) -> bool:
    return part.isascii() and part.isdecimal()


def numeric_clause_path(unit: IndexUnit) -> tuple[int, ...]:
    """Return the numeric body/appendix path used for deterministic ties."""
    if unit.section_number is None:
        raise ValueError("retrieval unit has no numbered section")
    parts = unit.section_number.split(".")
    if parts and all(_is_decimal(part) for part in parts):
        section = (0, *(int(part) for part in parts))
    elif (
        parts
        and parts[0]
        and all("A" <= character <= "Z" for character in parts[0].upper())
        and all(_is_decimal(part) for part in parts[1:])
    ):
        section = (
            1,
            _appendix_number(parts[0]),
            *(int(part) for part in parts[1:]),
        )
    else:
        raise ValueError("retrieval unit has a nonnumeric clause path")
    kind_rank = {"clause": 0, "table": 1}.get(unit.kind)
    if kind_rank is None:
        raise ValueError("retrieval unit has an unsupported kind")
    return (*section, -1, unit.ordinal, kind_rank)


@dataclass(frozen=True, slots=True)
class RetrievalLocator:
    """The complete deduplication identity and deterministic local tie key."""

    corpus_manifest_id: str
    document_id: str
    clause_id: str
    child_span: tuple[int, int] | None
    numeric_clause_path: tuple[int, ...]
    child_start: int

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.corpus_manifest_id) is None:
            raise ValueError("retrieval locator has an invalid corpus manifest ID")
        if not self.document_id or self.document_id != self.document_id.strip():
            raise ValueError("retrieval locator has an invalid document ID")
        if not self.clause_id or self.clause_id != self.clause_id.strip():
            raise ValueError("retrieval locator has an invalid clause ID")
        if (
            not isinstance(self.numeric_clause_path, tuple)
            or not self.numeric_clause_path
            or any(type(part) is not int for part in self.numeric_clause_path)
        ):
            raise ValueError("retrieval locator has no numeric clause path")
        if type(self.child_start) is not int or self.child_start < 0:
            raise ValueError("retrieval locator has an invalid child start")
        if self.child_span is None:
            if self.child_start != 0:
                raise ValueError("whole-unit locator must start at zero")
            return
        if (
            not isinstance(self.child_span, tuple)
            or len(self.child_span) != 2
            or any(type(part) is not int for part in self.child_span)
        ):
            raise ValueError("retrieval locator has an invalid child span")
        start, end = self.child_span
        if start < 0 or start >= end or self.child_start != start:
            raise ValueError("retrieval locator child span and start disagree")

    @property
    def dedupe_key(self) -> tuple[object, ...]:
        return (
            self.corpus_manifest_id,
            self.document_id,
            self.clause_id,
            self.child_span,
        )

    @property
    def stable_tie_key(self) -> tuple[object, ...]:
        return self.document_id, self.numeric_clause_path, self.child_start


def locator_for_unit(
    corpus_manifest_id: str,
    unit: IndexUnit,
) -> RetrievalLocator:
    """Build a whole-unit locator for one local index unit."""
    return RetrievalLocator(
        corpus_manifest_id=corpus_manifest_id,
        document_id=unit.document_id,
        clause_id=unit.unit_id,
        child_span=None,
        numeric_clause_path=numeric_clause_path(unit),
        child_start=0,
    )
