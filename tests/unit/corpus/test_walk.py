from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, iter_clause_texts
from specpilot.corpus.walk import (
    InvalidDocumentIdentityError,
    document_identity,
    parse_verified,
    unit_identity,
)
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


@pytest.mark.parametrize(
    "xml",
    [
        rfc_factory.SAFE_RFC_XML.replace('number="9999"', 'number=""'),
        rfc_factory.SAFE_RFC_XML.replace('number="9999"', 'number="９９９９"'),
        rfc_factory.SAFE_RFC_XML.replace('number="9999"', 'number="RFC9999"'),
        rfc_factory.SAFE_RFC_XML.replace('<date month="08" year="2026"/>', ""),
        rfc_factory.SAFE_RFC_XML.replace(' month="08"', ""),
        rfc_factory.SAFE_RFC_XML.replace(' year="2026"', ""),
        rfc_factory.SAFE_RFC_XML.replace(
            '<date month="08" year="2026"/>',
            '<date month="08" year="2026"/><date month="09" year="2026"/>',
        ),
        rfc_factory.SAFE_RFC_XML.replace('month="08"', 'month="13"'),
        rfc_factory.SAFE_RFC_XML.replace(
            'month="08"',
            'month="' + ("1" * 4301) + '"',
        ),
    ],
    ids=(
        "missing-number",
        "non-ascii-number",
        "non-numeric-number",
        "missing-date",
        "missing-month",
        "missing-year",
        "duplicate",
        "invalid-month",
        "oversized-month",
    ),
)
def test_document_identity_requires_a_valid_rfc_number_and_publication_date(
    tmp_path: Path,
    xml: str,
) -> None:
    document = rfc_factory.write(tmp_path, "identity.xml", xml)
    root = parse_verified(document, RfcLimits())

    with pytest.raises(InvalidDocumentIdentityError):
        document_identity(root)
