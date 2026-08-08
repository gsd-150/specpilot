from __future__ import annotations

import pytest

from specpilot.retrieval.bm25 import (
    TOKENIZER_VERSION,
    Bm25Index,
    Bm25Parameters,
    tokenize,
)

CORPUS = (
    ("c1", "A recipient MUST parse the Content-Length field value as a decimal."),
    ("c2", "Section 5.6.2 defines tokens used throughout this specification."),
    ("c3", "The 206 Partial Content status code indicates a range response."),
    ("c4", "Transfer-Encoding and Content-Length together are an error."),
    ("c5", "See RFC 9110 for the semantics of each method."),
)


def terms(text: str) -> list[str]:
    return tokenize(text)


def test_a_clause_number_survives_as_one_term() -> None:
    """§4.1.6's warning, transferred: default splitting makes 5.6.2 into three
    useless digits, and the clause number is this corpus's highest-signal term."""
    assert "5.6.2" in terms("Section 5.6.2 defines tokens")
    assert "5" not in terms("Section 5.6.2 defines tokens")


def test_a_hyphenated_field_name_survives_whole() -> None:
    assert "content-length" in terms("the Content-Length field")


def test_a_hyphenated_name_also_contributes_its_words() -> None:
    """So a question written "content length" still reaches the clause."""
    found = terms("the Content-Length field")

    assert {"content-length", "content", "length"} <= set(found)


def test_a_run_of_letters_and_digits_splits_at_the_boundary() -> None:
    """RFC9110 and "RFC 9110" should reach the same clauses."""
    found = terms("see RFC9110")

    assert {"rfc9110", "rfc", "9110"} <= set(found)


def test_a_status_code_keeps_its_digits_together() -> None:
    assert "206" in terms("the 206 Partial Content response")


def test_a_version_with_a_slash_survives() -> None:
    assert "http/1.1" in terms("the HTTP/1.1 message syntax")


def test_single_letters_from_a_compound_are_not_emitted() -> None:
    """Splitting a compound into one-character terms adds noise, not recall."""
    assert "1" not in terms("HTTP/1.1 syntax")


def test_tokenizing_is_case_insensitive() -> None:
    assert terms("Content-Length") == terms("content-length")


def test_the_index_ranks_a_clause_number_query_onto_its_clause() -> None:
    index = Bm25Index.build(CORPUS)

    hits = index.search("5.6.2", k=1)

    assert hits[0].unit_id == "c2"


def test_the_index_ranks_a_field_name_query_onto_the_right_clauses() -> None:
    index = Bm25Index.build(CORPUS)

    hits = index.search("Transfer-Encoding", k=2)

    assert hits[0].unit_id == "c4"


def test_a_query_written_as_separate_words_still_reaches_the_compound() -> None:
    index = Bm25Index.build(CORPUS)

    hits = index.search("content length", k=2)

    assert {hit.unit_id for hit in hits} == {"c1", "c4"}


def test_a_term_in_every_document_is_worth_far_less_than_a_rare_one() -> None:
    """Not zero — the smoothed IDF never reaches it, and that is standard.

    What has to hold is the ordering: a query carrying both a ubiquitous term
    and a distinctive one must rank on the distinctive one.
    """
    corpus = (("a", "alpha shared"), ("b", "beta shared"), ("c", "gamma shared"))
    index = Bm25Index.build(corpus)

    ubiquitous = index.search("shared", k=3)
    distinctive = index.search("alpha", k=3)

    assert len(ubiquitous) == 3
    assert distinctive[0].score > ubiquitous[0].score * 5
    assert index.search("shared alpha", k=3)[0].unit_id == "a"


def test_a_query_with_no_known_term_returns_nothing_rather_than_everything() -> None:
    index = Bm25Index.build(CORPUS)

    assert index.search("supercalifragilistic", k=5) == []


def test_search_returns_at_most_k_hits_in_descending_score() -> None:
    index = Bm25Index.build(CORPUS)

    hits = index.search("Content-Length field value", k=2)

    assert len(hits) <= 2
    assert [hit.score for hit in hits] == sorted(
        (hit.score for hit in hits), reverse=True
    )


def test_two_builds_of_the_same_corpus_score_identically() -> None:
    """Pooling is run once and audited later; a drifting score is not auditable."""
    first = Bm25Index.build(CORPUS).search("Content-Length", k=5)
    second = Bm25Index.build(CORPUS).search("Content-Length", k=5)

    assert first == second


def test_document_order_does_not_change_the_scores() -> None:
    forward = Bm25Index.build(CORPUS)
    backward = Bm25Index.build(tuple(reversed(CORPUS)))

    assert {
        h.unit_id: round(h.score, 9) for h in forward.search("Content-Length", 5)
    } == {h.unit_id: round(h.score, 9) for h in backward.search("Content-Length", 5)}


def test_the_index_carries_the_parameters_it_was_built_with() -> None:
    """§8.2.3 requires the pooling configuration to be reconstructible."""
    index = Bm25Index.build(CORPUS)

    assert index.parameters == Bm25Parameters()
    assert index.parameters.k1 == pytest.approx(1.2)
    assert index.parameters.b == pytest.approx(0.75)
    assert index.tokenizer_version == TOKENIZER_VERSION
    assert index.document_count == len(CORPUS)


def test_the_frozen_parameters_travel_into_the_fingerprint() -> None:
    """Retuning k1 later must not look like the configuration pooling used."""
    default = Bm25Index.build(CORPUS)
    tuned = Bm25Index.build(CORPUS, parameters=Bm25Parameters(k1=0.9, b=0.4))

    assert default.fingerprint != tuned.fingerprint


def test_the_fingerprint_changes_when_the_corpus_changes() -> None:
    smaller = Bm25Index.build(CORPUS[:-1])
    full = Bm25Index.build(CORPUS)

    assert smaller.fingerprint != full.fingerprint


def test_an_empty_corpus_is_refused_rather_than_scoring_everything_zero() -> None:
    with pytest.raises(ValueError, match="empty"):
        Bm25Index.build(())


def test_two_units_cannot_share_an_identifier() -> None:
    """A duplicate id would make a hit ambiguous about which clause it names."""
    with pytest.raises(ValueError, match="duplicate"):
        Bm25Index.build((("c1", "alpha"), ("c1", "beta")))
