from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, build_normative_index
from tests.helpers import rfc_factory


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "normative.xml", rfc_factory.NORMATIVE_RFC_XML)


def index(document: Path) -> dict[str, object]:
    return {
        entry.section_number: entry
        for entry in build_normative_index(document, RfcLimits(), ClauseLimits())
    }


def test_keywords_are_counted_from_the_documents_own_markup(document: Path) -> None:
    """The source tags every BCP 14 keyword, so nothing has to be guessed."""
    one = index(document)["1"]

    assert one.keyword_counts == {"MUST": 1, "SHOULD NOT": 1}  # type: ignore[attr-defined]
    assert one.normative_total == 2  # type: ignore[attr-defined]


def test_an_unmarked_uppercase_word_is_prose_and_is_not_counted(
    document: Path,
) -> None:
    """Section 1 says MUST twice; only the tagged one is normative.

    Scanning for uppercase words would count ABNF, field names, and quoted
    examples as requirements, and the sections that look most normative would
    be the ones with the most syntax in them.
    """
    assert index(document)["1"].keyword_counts["MUST"] == 1  # type: ignore[attr-defined]


def test_a_two_word_keyword_is_one_keyword_not_two(document: Path) -> None:
    one = index(document)["1"]

    assert "SHOULD NOT" in one.keyword_counts  # type: ignore[attr-defined]
    assert "SHOULD" not in one.keyword_counts  # type: ignore[attr-defined]
    assert "NOT" not in one.keyword_counts  # type: ignore[attr-defined]


def test_a_nested_section_keeps_its_own_keywords(document: Path) -> None:
    """Otherwise a parent absorbs its children and every top section looks rich."""
    entries = index(document)

    assert entries["1.1"].keyword_counts == {"MAY": 1}  # type: ignore[attr-defined]
    assert "MAY" not in entries["1"].keyword_counts  # type: ignore[attr-defined]


def test_a_section_with_no_requirements_still_appears_with_zero(
    document: Path,
) -> None:
    """A candidate list that hides the empty sections cannot be checked."""
    two = index(document)["2"]

    assert two.keyword_counts == {}  # type: ignore[attr-defined]
    assert two.normative_total == 0  # type: ignore[attr-defined]
    assert two.clause_count == 1  # type: ignore[attr-defined]


def test_every_section_reports_its_clause_and_word_counts(document: Path) -> None:
    one = index(document)["1"]

    assert one.clause_count == 2  # type: ignore[attr-defined]
    assert one.word_count > 0  # type: ignore[attr-defined]


def unwrapped(tmp_path: Path) -> Path:
    directory = tmp_path / "unwrapped"
    directory.mkdir(mode=0o700, exist_ok=True)
    return rfc_factory.write(
        directory, "unwrapped.xml", rfc_factory.UNWRAPPED_PROSE_XML
    )


def test_prose_the_source_did_not_wrap_in_a_tag_is_still_a_clause(
    tmp_path: Path,
) -> None:
    """RFC 9110 leaves 3689 words in bare <li> and 454 in bare <dd>.

    Seventeen BCP 14 keywords live in them. Text with no clause is text no
    citation can name and no retriever can reach, so the requirement would be
    unusable as gold — it would simply not be in the corpus.
    """
    from specpilot.corpus.clauses import build_clauses

    clauses = build_clauses(unwrapped(tmp_path), RfcLimits(), ClauseLimits())

    # The wrapped paragraph, the bare <li>, the wrapped <li>, and the bare <dd>.
    assert len(clauses) == 4


def test_a_definition_term_and_a_table_cell_are_not_clauses(
    tmp_path: Path,
) -> None:
    """482 <dt> in RFC 9110 average under two words and state no requirement.

    Admitting labels would add eight hundred near-empty citation targets for no
    requirement gained, and every one of them would be a retrieval candidate.
    """
    from specpilot.corpus.clauses import build_clauses

    clauses = build_clauses(unwrapped(tmp_path), RfcLimits(), ClauseLimits())
    entry = index(unwrapped(tmp_path))["1"]

    assert len(clauses) == 4
    assert entry.clause_count == 4  # type: ignore[attr-defined]


def test_an_unwrapped_items_keywords_reach_the_normative_index(
    tmp_path: Path,
) -> None:
    assert index(unwrapped(tmp_path))["1"].keyword_counts == {"MUST": 1}  # type: ignore[attr-defined]


def test_the_index_carries_no_clause_text(document: Path) -> None:
    entries = build_normative_index(document, RfcLimits(), ClauseLimits())

    for entry in entries:
        assert "prose" not in repr(entry)
        assert "recipient" not in repr(entry)
