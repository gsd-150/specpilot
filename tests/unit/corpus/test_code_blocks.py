from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import (
    ClauseLimits,
    OversizedClauseError,
    build_clauses,
    iter_clause_texts,
)
from tests.helpers import rfc_factory


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "grammar.xml", rfc_factory.CODE_BLOCK_XML)


def test_a_grammar_block_is_a_clause_because_the_source_numbers_it_as_one(
    document: Path,
) -> None:
    """RFC 9110's §3.9 runs -1 (t), -2 (t), -3 (sourcecode), -4 (t), -5.

    The document counts an ABNF block as a numbered paragraph of the section,
    so it is a clause rather than a unit of its own. 366 sourcecode and 81
    artwork blocks in RFC 9110 hold 2913 words that were in no clause before,
    including every ABNF rule name.
    """
    texts = [
        text for _, text in iter_clause_texts(document, RfcLimits(), ClauseLimits())
    ]

    assert "token = 1*tchar" in texts


def test_artwork_inside_a_figure_is_reached_too(document: Path) -> None:
    texts = [
        text for _, text in iter_clause_texts(document, RfcLimits(), ClauseLimits())
    ]

    assert "a diagram" in texts


def test_a_grammar_block_keeps_its_place_in_the_paragraph_order(
    document: Path,
) -> None:
    """Appending them instead would break the source's own numbering."""
    clauses = build_clauses(document, RfcLimits(), ClauseLimits())
    ordinals = {c.anchor: c.ordinal for c in clauses}

    assert ordinals["section-1-1"] == 1
    assert ordinals["section-1-2"] == 2
    assert ordinals["section-1-3"] == 3


def test_an_excluded_section_contributes_no_clauses(document: Path) -> None:
    """RFC 9110's Collected ABNF restates 143 rules and introduces none.

    Indexing it would add that many near-duplicate candidates competing with
    the clauses that actually state the rules.
    """
    limits = ClauseLimits(excluded_sections=frozenset({"collected.abnf"}))

    clauses = build_clauses(document, RfcLimits(), limits)

    assert clauses
    assert all(c.section_anchor != "collected.abnf" for c in clauses)


def test_without_the_exclusion_the_section_is_still_read(document: Path) -> None:
    """The exclusion is a recorded decision, not a property of the parser."""
    clauses = build_clauses(document, RfcLimits(), ClauseLimits())

    assert any(c.section_anchor == "collected.abnf" for c in clauses)


def test_an_unexpected_oversized_block_still_refuses(tmp_path: Path) -> None:
    """Only the one evidenced section is excluded; a second means something
    changed that nobody has looked at."""
    directory = tmp_path / "big"
    directory.mkdir(mode=0o700)
    document = rfc_factory.write(
        directory,
        "big.xml",
        rfc_factory.CODE_BLOCK_XML.replace(
            '<t pn="section-1-1">Prose before the grammar.</t>',
            f'<t pn="section-1-1">{"word " * 600}</t>',
        ),
    )
    limits = ClauseLimits(excluded_sections=frozenset({"collected.abnf"}))

    with pytest.raises(OversizedClauseError):
        build_clauses(document, RfcLimits(), limits)
