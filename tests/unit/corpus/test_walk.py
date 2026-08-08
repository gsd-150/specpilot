from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, iter_clause_texts
from specpilot.corpus.walk import unit_identity
from tests.helpers import rfc_factory


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "xrefs.xml", rfc_factory.DERIVED_XREF_XML)


def texts(document: Path) -> list[str]:
    return [
        text for _, text in iter_clause_texts(document, RfcLimits(), ClauseLimits())
    ]


def test_a_reference_with_no_inline_text_keeps_the_text_it_renders_as(
    document: Path,
) -> None:
    """1523 of RFC 9110's 2519 references are shaped this way.

    Flattened naively they vanish, and a clause reads "caching () from the
    syntax ()". Section numbers are also this corpus's highest-signal sparse
    retrieval terms, so every one cited in prose would be missing from the
    index.
    """
    first = texts(document)[0]

    assert "Section 2" in first
    assert "Section 3" in first
    assert "()" not in first


def test_a_reference_that_does_carry_inline_text_keeps_that_instead(
    document: Path,
) -> None:
    second = texts(document)[1]

    assert "the second section" in second


def test_surrounding_text_survives_the_substitution(document: Path) -> None:
    """The tail after a reference is easy to drop when rewriting a flatten."""
    first = texts(document)[0]

    assert first.startswith("Semantics are defined in Section 2")
    assert first.endswith("and syntax in Section 3.")


def test_a_units_identity_depends_on_its_kind() -> None:
    """Tables and clauses are both numbered from one inside their section."""
    shared = ("ietf-rfc-9110", "3", "section-5.6.2", 1)

    assert unit_identity("clause", *shared) != unit_identity("table", *shared)


def test_a_units_identity_cannot_be_forged_by_moving_a_boundary() -> None:
    """Section "1" ordinal 12 and section "11" ordinal 2 both spell 1112."""
    assert unit_identity("clause", "d", "v", "1", 12) != unit_identity(
        "clause", "d", "v", "11", 2
    )
