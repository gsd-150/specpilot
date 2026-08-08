from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, build_clauses
from specpilot.corpus.tables import build_tables, iter_table_rows
from tests.helpers import rfc_factory


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "tables.xml", rfc_factory.TABLE_RFC_XML)


def tables(document: Path) -> tuple[object, ...]:
    return build_tables(document, RfcLimits(), ClauseLimits())


def test_every_table_in_the_document_gets_a_locator(document: Path) -> None:
    assert len(tables(document)) == 3


def test_a_table_is_identified_by_its_section_and_ordinal(document: Path) -> None:
    first, second, third = build_tables(document, RfcLimits(), ClauseLimits())

    assert (first.section_number, first.ordinal) == ("1", 1)  # type: ignore[attr-defined]
    assert (second.section_number, second.ordinal) == ("1", 2)  # type: ignore[attr-defined]
    assert (third.section_number, third.ordinal) == ("2", 1)  # type: ignore[attr-defined]
    assert len({t.table_id for t in (first, second, third)}) == 3  # type: ignore[attr-defined]


def test_the_identity_is_stable_across_two_builds(document: Path) -> None:
    first = [t.table_id for t in tables(document)]  # type: ignore[attr-defined]
    second = [t.table_id for t in tables(document)]  # type: ignore[attr-defined]

    assert first == second


def test_a_table_and_a_clause_in_the_same_position_are_not_the_same_thing(
    document: Path,
) -> None:
    """Both are numbered from one within their section.

    Without a discriminator in the hashed identity, table 1 of section 1 and
    paragraph 1 of section 1 would be the same ID, and a gold field naming one
    would silently resolve to the other.
    """
    clauses = build_clauses(document, RfcLimits(), ClauseLimits())
    clause_ids = {c.clause_id for c in clauses}
    table_ids = {t.table_id for t in tables(document)}  # type: ignore[attr-defined]

    assert clause_ids
    assert table_ids
    assert not (clause_ids & table_ids)


def test_rows_and_columns_are_counted_from_the_source(document: Path) -> None:
    first, second, third = build_tables(document, RfcLimits(), ClauseLimits())

    assert (first.row_count, first.column_count) == (2, 2)  # type: ignore[attr-defined]
    assert (second.row_count, second.column_count) == (1, 3)  # type: ignore[attr-defined]
    assert (third.row_count, third.column_count) == (1, 1)  # type: ignore[attr-defined]


def test_a_header_row_is_marked_rather_than_counted_as_a_body_row(
    document: Path,
) -> None:
    """Dropping it loses the column names; counting it inflates the row count."""
    first, second, _ = build_tables(document, RfcLimits(), ClauseLimits())

    assert first.has_header is True  # type: ignore[attr-defined]
    assert first.row_count == 2  # type: ignore[attr-defined]
    assert second.has_header is False  # type: ignore[attr-defined]


def test_a_table_locator_holds_no_cell_text(document: Path) -> None:
    for table in build_tables(document, RfcLimits(), ClauseLimits()):
        rendered = str(asdict(table))
        assert "Not Found" not in rendered
        assert "gamma" not in rendered


def test_the_rows_are_reachable_by_a_call_you_have_to_mean(document: Path) -> None:
    rows = dict(
        (table.table_id, body)
        for table, body in iter_table_rows(document, RfcLimits(), ClauseLimits())
    )
    first = build_tables(document, RfcLimits(), ClauseLimits())[0]

    assert rows[first.table_id] == (  # type: ignore[attr-defined]
        ("Code", "Meaning"),
        ("200", "OK"),
        ("404", "Not Found"),
    )


def test_the_header_leads_the_rows_so_a_column_can_be_named(document: Path) -> None:
    _, body = next(iter(iter_table_rows(document, RfcLimits(), ClauseLimits())))

    assert body[0] == ("Code", "Meaning")


def test_cells_keep_the_text_of_a_cross_reference_they_contain(
    tmp_path: Path,
) -> None:
    """RFC 9110's tables are mostly <xref> cells; dropping them empties them."""
    directory = tmp_path / "xref"
    directory.mkdir(mode=0o700)
    document = rfc_factory.write(
        directory,
        "x.xml",
        rfc_factory.TABLE_RFC_XML.replace(
            "<td>OK</td>", '<td><xref target="one" derivedContent="Section 1"/></td>'
        ),
    )

    _, body = next(iter(iter_table_rows(document, RfcLimits(), ClauseLimits())))

    assert body[1][1] != ""


def test_building_tables_requires_passing_the_rfc_boundary(tmp_path: Path) -> None:
    from specpilot.contracts.rfc import UnsafeRfcError

    directory = tmp_path / "hostile"
    directory.mkdir(mode=0o700)
    document = rfc_factory.write(
        directory, "hostile.xml", rfc_factory.EXTERNAL_ENTITY_XML
    )

    with pytest.raises(UnsafeRfcError):
        build_tables(document, RfcLimits(), ClauseLimits())
