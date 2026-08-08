"""Tables as a citable unit.

A table is cited whole — "the table in §7.3" — so the table is the unit. Its
cells are not: `<td>` in isolation names nothing, and admitting 332 of them to
the clause index would have added that many near-empty retrieval candidates for
no requirement gained. Excluding them from clauses was right; leaving tables out
of the corpus altogether was not, which is what this module fixes.

They are small and they state nothing normative — 12 tables and 142 body rows in
RFC 9110, 3 and 11 in RFC 9112, and zero BCP 14 keywords among them. They are
reference lookups: status codes, method names, registry pointers. A question
about one is a real question, and until now it had nothing to cite.

Like a clause, a table holds no text. `iter_table_rows` is the call you have to
mean.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree.ElementTree import Element  # noqa: S405 - parsed via defusedxml

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, OversizedClauseError
from specpilot.corpus.walk import (
    document_identity,
    element_text,
    owned_tables,
    parse_verified,
    sections,
    unit_identity,
)
from specpilot.ingestion.rfc import RfcInput

TABLE_KIND = "table"

_CELL_TAGS = ("th", "td")


@dataclass(frozen=True, slots=True)
class Table:
    table_id: str
    document_id: str
    document_version: str
    section_anchor: str
    section_number: str | None
    section_path: str
    ordinal: int
    anchor: str | None
    row_count: int
    column_count: int
    has_header: bool
    byte_count: int
    word_count: int


def _row_cells(row: Element) -> tuple[str, ...]:
    return tuple(element_text(cell) for cell in row if cell.tag in _CELL_TAGS)


def _row_width(row: Element) -> int:
    """Count columns, honouring colspan rather than counting cell elements."""
    width = 0
    for cell in row:
        if cell.tag not in _CELL_TAGS:
            continue
        try:
            width += max(int(cell.get("colspan", "1")), 1)
        except ValueError:
            width += 1
    return width


def _table_rows(table: Element) -> tuple[tuple[tuple[str, ...], ...], bool]:
    """Return the rows with any header first, and whether a header existed.

    The header leads rather than being dropped, because without it a column
    cannot be named — a cell reading "200" means nothing until something says
    the column is a status code. It is not counted as a body row either, which
    would inflate the row count against the source.
    """
    head = table.find("thead")
    header_rows: tuple[tuple[str, ...], ...] = ()
    if head is not None:
        header_rows = tuple(_row_cells(row) for row in head.iter("tr"))
    body_rows: list[tuple[str, ...]] = []
    for container in (table.find("tbody"), table.find("tfoot")):
        if container is None:
            continue
        body_rows.extend(_row_cells(row) for row in container.iter("tr"))
    if head is None and not body_rows:
        # A table with neither wrapper still has rows directly beneath it.
        body_rows.extend(_row_cells(row) for row in table.findall("tr"))
    return (*header_rows, *tuple(body_rows)), bool(header_rows)


def _tables_with_rows(
    source: RfcInput,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> Iterator[tuple[Table, tuple[tuple[str, ...], ...]]]:
    root = parse_verified(source, rfc_limits)
    document_id, document_version = document_identity(root)

    for section in sections(root):
        ordinal = 0
        for element in owned_tables(section.element):
            rows, has_header = _table_rows(element)
            if not rows:
                continue
            ordinal += 1
            text = element_text(element)
            byte_count = len(text.encode("utf-8"))
            word_count = len(text.split())
            if (
                byte_count > clause_limits.max_bytes
                or word_count > clause_limits.max_words
            ):
                raise OversizedClauseError(
                    f"table {ordinal} of section {section.anchor} "
                    "exceeds the excerpt caps"
                )
            body_count = len(rows) - (1 if has_header else 0)
            widths = [_row_width(row) for row in element.iter("tr")]
            yield (
                Table(
                    table_id=unit_identity(
                        TABLE_KIND,
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
                    anchor=element.get("pn") or element.get("anchor"),
                    row_count=body_count,
                    column_count=max(widths) if widths else 0,
                    has_header=has_header,
                    byte_count=byte_count,
                    word_count=word_count,
                ),
                rows,
            )


def build_tables(
    source: RfcInput,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> tuple[Table, ...]:
    """Return every table in document order, without their cells."""
    return tuple(
        table for table, _ in _tables_with_rows(source, rfc_limits, clause_limits)
    )


def iter_table_rows(
    source: RfcInput,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
) -> Iterator[tuple[Table, tuple[tuple[str, ...], ...]]]:
    """Yield each table with its rows, for callers that genuinely need them."""
    yield from _tables_with_rows(source, rfc_limits, clause_limits)
