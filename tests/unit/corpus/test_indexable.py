from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.indexable import (
    CHUNKER_VERSION,
    IndexUnit,
    build_index_units,
)
from specpilot.corpus.walk import RFCXML_PARSER_VERSION
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.dense import point_payload
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit, numeric_clause_path
from tests.helpers import rfc_factory


def _index_unit(**changes: object) -> IndexUnit:
    values: dict[str, object] = {
        "unit_id": "ietf-rfc-9110:section-2-1",
        "kind": "clause",
        "document_id": "ietf-rfc-9110",
        "document_version": "2022-06",
        "section_number": "2",
        "section_path": "Syntax",
        "ordinal": 1,
        "text": "source",
        "indexed": "2 Syntax\nsource",
    }
    values.update(changes)
    return IndexUnit(**values)  # type: ignore[arg-type]


@pytest.fixture
def document(tmp_path: Path) -> Path:
    return rfc_factory.write(tmp_path, "units.xml", rfc_factory.QA_RFC_XML)


def test_parser_and_chunker_versions_name_the_exact_frozen_protocol() -> None:
    assert RFCXML_PARSER_VERSION == "rfcxml-v3/v1"
    assert CHUNKER_VERSION == "rfc-clause-table/v1"


def test_clause_and_table_ordinals_propagate_without_reordering_units(
    document: Path,
) -> None:
    units = build_index_units(document, RfcLimits(), ClauseLimits())

    assert [
        (unit.kind, unit.section_number, unit.ordinal, unit.text, unit.indexed)
        for unit in units
    ] == [
        (
            "clause",
            "1",
            1,
            "Prose that MUST cite Section 2.",
            "1 One\nProse that MUST cite Section 2.",
        ),
        ("clause", "1", 2, "token = 1*tchar", "1 One\ntoken = 1*tchar"),
        ("clause", "1", 3, "A definition body.", "1 One\nA definition body."),
        ("clause", "2", 1, "More prose here.", "2 Two\nMore prose here."),
        ("table", "1", 1, "Code 200", "1 One\nCode 200"),
    ]


def test_numeric_paths_sort_body_sections_and_subsections_numerically() -> None:
    section_two = replace(_index_unit(), section_number="2", ordinal=1)
    section_ten = replace(_index_unit(), section_number="10", ordinal=1)
    subsection_two = replace(_index_unit(), section_number="2.2", ordinal=1)
    subsection_ten = replace(_index_unit(), section_number="2.10", ordinal=1)

    assert numeric_clause_path(section_two) == (0, 2, -1, 1, 0)
    assert numeric_clause_path(section_ten) == (0, 10, -1, 1, 0)
    assert numeric_clause_path(section_two) < numeric_clause_path(section_ten)
    assert numeric_clause_path(subsection_two) < numeric_clause_path(subsection_ten)


@pytest.mark.parametrize(
    ("section_number", "expected"),
    [
        ("A", (1, 1, -1, 1, 0)),
        ("B", (1, 2, -1, 1, 0)),
        ("C", (1, 3, -1, 1, 0)),
        ("AA", (1, 27, -1, 1, 0)),
        ("A.2", (1, 1, 2, -1, 1, 0)),
        ("A.10", (1, 1, 10, -1, 1, 0)),
    ],
)
def test_numeric_paths_encode_appendix_labels_in_base_26(
    section_number: str,
    expected: tuple[int, ...],
) -> None:
    assert numeric_clause_path(
        replace(_index_unit(), section_number=section_number)
    ) == expected


def test_appendix_paths_sort_a_b_c_aa_and_subsections_numerically() -> None:
    paths = [
        numeric_clause_path(replace(_index_unit(), section_number=number))
        for number in ("A", "B", "C", "AA")
    ]
    subsection_two = numeric_clause_path(
        replace(_index_unit(), section_number="A.2")
    )
    subsection_ten = numeric_clause_path(
        replace(_index_unit(), section_number="A.10")
    )

    assert paths == sorted(paths)
    assert subsection_two < subsection_ten


def test_clause_and_table_at_one_ordinal_have_distinct_paths_and_payloads() -> None:
    clause = replace(_index_unit(), kind="clause", ordinal=1)
    table = replace(_index_unit(), kind="table", ordinal=1)

    assert numeric_clause_path(clause) == (0, 2, -1, 1, 0)
    assert numeric_clause_path(table) == (0, 2, -1, 1, 1)
    assert set(point_payload(clause)) == {
        "unit_id",
        "kind",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"section_number": None},
        {"section_number": ""},
        {"section_number": "2."},
        {"section_number": ".2"},
        {"section_number": "A."},
        {"section_number": "A.x"},
        {"section_number": "A..1"},
        {"section_number": "A-1"},
        {"section_number": "²"},
        {"kind": "figure"},
    ],
)
def test_numeric_paths_refuse_missing_malformed_and_unsupported_units(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        numeric_clause_path(replace(_index_unit(), **changes))  # type: ignore[arg-type]


def test_local_corpus_units_preserve_document_and_unit_construction_order(
    tmp_path: Path,
) -> None:
    first = rfc_factory.write(tmp_path, "rfc9999.xml", rfc_factory.QA_RFC_XML)
    second = rfc_factory.write(
        tmp_path,
        "rfc9998.xml",
        rfc_factory.QA_RFC_XML.replace('number="9999"', 'number="9998"'),
    )

    corpus = LocalCorpus.load(
        ((first, ClauseLimits()), (second, ClauseLimits())),
        RfcLimits(),
    )

    assert [
        (unit.document_id, unit.kind, unit.section_number, unit.ordinal)
        for unit in corpus.units()
    ] == [
        ("ietf-rfc-9999", "clause", "1", 1),
        ("ietf-rfc-9999", "clause", "1", 2),
        ("ietf-rfc-9999", "clause", "1", 3),
        ("ietf-rfc-9999", "clause", "2", 1),
        ("ietf-rfc-9999", "table", "1", 1),
        ("ietf-rfc-9998", "clause", "1", 1),
        ("ietf-rfc-9998", "clause", "1", 2),
        ("ietf-rfc-9998", "clause", "1", 3),
        ("ietf-rfc-9998", "clause", "2", 1),
        ("ietf-rfc-9998", "table", "1", 1),
    ]
    assert tuple(corpus.unit_ids()) == tuple(unit.unit_id for unit in corpus.units())


def test_local_corpus_refuses_duplicate_unit_ids_before_insertion(
    document: Path,
) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        LocalCorpus.load(
            ((document, ClauseLimits()), (document, ClauseLimits())),
            RfcLimits(),
        )


def test_restricted_rfc_corpus_keeps_unique_ties_and_bm25_fingerprint() -> None:
    repository = Path(__file__).resolve().parents[3]
    paths = (
        repository / "artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml",
        repository / "artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml",
    )
    if not all(path.is_file() for path in paths):
        pytest.skip("restricted RFC fixtures are not available")
    limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
    corpus = LocalCorpus.load(
        tuple((load_verified_rfc(path, RfcLimits()), limits) for path in paths),
        RfcLimits(),
    )

    locators = tuple(locator_for_unit("a" * 64, unit) for unit in corpus.units())

    assert corpus.unit_count() == 1922
    assert len({locator.stable_tie_key for locator in locators}) == 1922
    assert Bm25Index.build(corpus.indexable()).fingerprint == (
        "8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3"
    )
