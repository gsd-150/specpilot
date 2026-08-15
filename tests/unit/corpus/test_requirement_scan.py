"""The exhaustiveness aid for adversarial construction.

An L2-adv negative rests on a negative claim about the whole corpus: that *no*
clause supports the asserted verdict. Checking the one clause you picked proves
nothing about that, and two drafted groups were lost exactly there — one to an
unconditional sender prohibition two sections away, one to a MUST that governed
the same omission the chosen distractor only discouraged.

So this returns every clause that carries the given literal terms, in document
order, with no score and no cut-off. It is the same path as `grep` over the
frozen bytes, which §8.2.1 permits as an annotation aid, and deliberately not
the system's retriever: a ranked shortlist would put retrieval back inside the
gold path, and — worse for this particular job — a list that ends at the top k
cannot answer the question that was asked of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.requirements import scan_requirements
from specpilot.ingestion.rfc import load_verified_rfc

_ROOT = Path(__file__).resolve().parents[3]
_RFC9112 = _ROOT / "artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml"


def _source() -> object:
    return load_verified_rfc(_RFC9112, RfcLimits())


pytestmark = pytest.mark.skipif(
    not _RFC9112.exists(), reason="frozen RFC 9112 rendition is not present"
)


def test_a_term_scan_returns_every_clause_carrying_the_term() -> None:
    hits = scan_requirements(
        _source(), RfcLimits(), ClauseLimits(), terms=("Content-Length",)
    )

    assert hits
    assert all("content-length" in hit.matched_terms_lower for hit in hits)
    # Ordering is the document's, so a reader can walk it against the source.
    ordinals = [(hit.section_number or "", hit.ordinal) for hit in hits]
    assert ordinals == sorted(ordinals, key=lambda item: (item[0], item[1]))


def test_several_terms_are_conjunctive() -> None:
    both = scan_requirements(
        _source(),
        RfcLimits(),
        ClauseLimits(),
        terms=("Content-Length", "Transfer-Encoding"),
    )
    single = scan_requirements(
        _source(), RfcLimits(), ClauseLimits(), terms=("Content-Length",)
    )

    assert both
    assert len(both) < len(single)
    assert {hit.clause_id for hit in both} <= {hit.clause_id for hit in single}


def test_the_unconditional_sender_prohibition_is_found(
) -> None:
    """The clause that invalidated a drafted group, found by the terms it uses.

    Section 6.2's sender prohibition is two sections away from the clause the
    draft was built on and carries no shared section path, so nothing about
    proximity would have surfaced it. Its terms would have.
    """
    hits = scan_requirements(
        _source(),
        RfcLimits(),
        ClauseLimits(),
        terms=("Content-Length", "Transfer-Encoding"),
        keywords=("MUST NOT",),
    )

    assert "8bc686a451ca572cd2a5cccae5cd3c587f90e58eff83e4091e40f2be5e085daa" in {
        hit.clause_id for hit in hits
    }


def test_a_keyword_filter_keeps_only_clauses_stating_that_strength() -> None:
    must_not = scan_requirements(
        _source(),
        RfcLimits(),
        ClauseLimits(),
        terms=("Content-Length",),
        keywords=("MUST NOT",),
    )

    assert must_not
    assert all("MUST NOT" in hit.keyword_counts for hit in must_not)


def test_must_not_is_never_counted_as_a_must() -> None:
    hits = scan_requirements(
        _source(),
        RfcLimits(),
        ClauseLimits(),
        terms=("Content-Length", "Transfer-Encoding"),
        keywords=("MUST",),
    )

    assert "8bc686a451ca572cd2a5cccae5cd3c587f90e58eff83e4091e40f2be5e085daa" not in {
        hit.clause_id for hit in hits
    }


def test_a_scan_carries_no_clause_prose() -> None:
    """§8.1 holds here too, and the aid does not need the text to do its job.

    It says where to read. The author reads the source. Asserted against the
    real sentence rather than a length bound, so the check fails if a field
    ever starts carrying the clause it located.
    """
    import dataclasses

    sentence = (
        "A sender MUST NOT send a Content-Length header field in any message "
        "that contains a Transfer-Encoding header field."
    )
    hits = scan_requirements(
        _source(),
        RfcLimits(),
        ClauseLimits(),
        terms=("Content-Length", "Transfer-Encoding"),
        keywords=("MUST NOT",),
    )
    located = next(
        hit
        for hit in hits
        if hit.clause_id
        == "8bc686a451ca572cd2a5cccae5cd3c587f90e58eff83e4091e40f2be5e085daa"
    )

    rendered = repr(dataclasses.asdict(located))
    assert "Content-Length header field in any message" not in rendered
    assert sentence not in rendered


def test_a_scan_without_terms_is_refused() -> None:
    """An unfiltered dump is not an exhaustiveness check, it is the document."""
    with pytest.raises(ValueError):
        scan_requirements(_source(), RfcLimits(), ClauseLimits(), terms=())


def test_the_scan_has_no_parameter_that_could_carry_a_ranked_list() -> None:
    """The enforcement, in the same shape `select_distractors` uses.

    A rule saying "do not pass the retriever's hits in here" lasts as long as
    the person who remembers it. A signature that cannot accept a query, a
    score, or a limit outlives them.
    """
    import inspect

    parameters = set(inspect.signature(scan_requirements).parameters)

    assert not parameters & {"query", "scores", "ranked", "limit", "top_k"}
