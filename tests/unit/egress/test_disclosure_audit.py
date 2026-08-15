"""Resolving ledger disclosures back to the clauses they disclosed.

Without this, `citation_count: 0` is unreadable. It can mean the evidence path
never surfaced the governing clause, or that the model was shown it and declined
to cite it — two failures in different components with different fixes, and the
ledger is the only record that separates them. Guessing between them cost two
rounds of work on the wrong component.

A `disclosure_id` is the SHA-256 of the canonical
`(corpus_manifest_id, content_hash, quote_hash, normalized_excerpt_span)` tuple
rather than of the excerpt text, because §3.2 counts a disclosure unit by that
composite — a long clause resliced into several spans must not buy several
slots. So the resolver rebuilds the whole tuple per unit; hashing the text alone
produces a table that matches nothing and looks merely empty.
"""

from __future__ import annotations

import hashlib

import pytest

from specpilot.contracts.egress import NormalizedExcerptSpan
from specpilot.egress.disclosure_audit import (
    CorpusMismatchError,
    build_disclosure_index,
    resolve_disclosures,
)
from specpilot.egress.policy import disclosure_id

_CORPUS = "a" * 64


class _Unit:
    def __init__(self, unit_id: str, text: str, ordinal: int, section: str) -> None:
        self.unit_id = unit_id
        self.text = text
        self.ordinal = ordinal
        self.section_number = section


def _units() -> tuple[_Unit, ...]:
    return (
        _Unit("u1" + "0" * 62, "a proxy MUST remove the received field", 4, "6.3"),
        _Unit("u2" + "0" * 62, "a client MUST ignore the extension", 2, "7.1.1"),
    )


def _expected_id(unit: _Unit) -> str:
    content_hash = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
    return disclosure_id(
        _CORPUS,
        content_hash,
        content_hash,
        NormalizedExcerptSpan(
            paragraph_start=unit.ordinal,
            paragraph_end=unit.ordinal,
            token_start=0,
            token_end=max(len(unit.text.split()), 1),
        ),
    )


def test_the_index_reproduces_the_ledgers_own_identifier() -> None:
    index = build_disclosure_index(_units(), corpus_manifest_id=_CORPUS)

    for unit in _units():
        assert index[_expected_id(unit)].clause_id == unit.unit_id


def test_hashing_the_text_alone_does_not_reproduce_it() -> None:
    """The mistake this module exists to stop repeating.

    A text-only digest yields a table that matches no ledger row, and an empty
    intersection reads as "nothing was disclosed" rather than "the key is
    wrong".
    """
    index = build_disclosure_index(_units(), corpus_manifest_id=_CORPUS)
    unit = _units()[0]
    text_only = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()

    assert text_only not in index


def test_resolution_reports_what_it_could_not_map() -> None:
    """An unresolved disclosure is a finding, never a silent omission.

    Dropping it would understate what left the boundary, which is the one
    number the ledger exists to keep honest.
    """
    index = build_disclosure_index(_units(), corpus_manifest_id=_CORPUS)
    known = _expected_id(_units()[0])

    resolved = resolve_disclosures((known, "f" * 64), index)

    assert [entry.clause_id for entry in resolved.clauses] == [_units()[0].unit_id]
    assert resolved.unresolved == ("f" * 64,)


def test_a_corpus_that_is_not_the_bound_one_refuses() -> None:
    """Silent mismatch is the failure mode, so the digest is checked.

    Building the table from a different rendition yields identifiers that
    resolve nothing, and the report would read as a total retrieval failure
    when it is a wrong corpus.
    """
    with pytest.raises(CorpusMismatchError):
        build_disclosure_index(
            _units(),
            corpus_manifest_id=_CORPUS,
            expected_derived_sha256="b" * 64,
            derived_sha256="c" * 64,
        )


def test_a_matching_corpus_digest_passes() -> None:
    index = build_disclosure_index(
        _units(),
        corpus_manifest_id=_CORPUS,
        expected_derived_sha256="b" * 64,
        derived_sha256="b" * 64,
    )

    assert len(index) == 2


def test_resolution_carries_locators_not_prose() -> None:
    index = build_disclosure_index(_units(), corpus_manifest_id=_CORPUS)
    resolved = resolve_disclosures((_expected_id(_units()[0]),), index)

    assert "MUST remove the received field" not in repr(resolved)
