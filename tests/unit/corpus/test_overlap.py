from __future__ import annotations

import pytest

from specpilot.cli import _section_matches
from specpilot.corpus.overlap import (
    containment,
    jaccard_overlap,
    question_gold_jaccard,
    restates,
    tokenize,
)


def test_a_section_filter_matches_components_not_string_prefixes() -> None:
    """Selecting section 1 must not sweep in 10, 11, and 12."""
    assert _section_matches("1", "1")
    assert _section_matches("1.2", "1")
    assert _section_matches("1.2.3", "1.2")
    assert not _section_matches("10", "1")
    assert not _section_matches("12.1", "1")
    assert not _section_matches("2", "1")
    assert not _section_matches(None, "1")


def test_tokenizing_is_case_and_punctuation_insensitive() -> None:
    assert tokenize("A stored response's freshness, per RFC 9111!") == {
        "a",
        "stored",
        "response",
        "s",
        "freshness",
        "per",
        "rfc",
        "9111",
    }


def test_overlap_of_identical_token_sets_is_one() -> None:
    assert jaccard_overlap("the timer default", "The TIMER default!") == 1.0


def test_overlap_of_disjoint_token_sets_is_zero() -> None:
    assert jaccard_overlap("alpha beta", "gamma delta") == 0.0


def test_overlap_counts_distinct_tokens_not_repetitions() -> None:
    """A word repeated ten times is still one token in the set."""
    assert jaccard_overlap("alpha alpha alpha beta", "alpha beta") == 1.0


def test_overlap_is_symmetric() -> None:
    first, second = "alpha beta gamma", "beta gamma delta"

    assert jaccard_overlap(first, second) == jaccard_overlap(second, first)


def test_overlap_is_the_intersection_over_the_union() -> None:
    # {alpha, beta, gamma} against {beta, gamma, delta}: 2 shared of 4 distinct.
    assert jaccard_overlap("alpha beta gamma", "beta gamma delta") == pytest.approx(0.5)


def test_empty_text_has_no_overlap_rather_than_dividing_by_zero() -> None:
    assert jaccard_overlap("", "alpha") == 0.0
    assert jaccard_overlap("alpha", "") == 0.0
    assert jaccard_overlap("", "") == 0.0


def test_the_gold_figure_takes_the_best_matching_clause_not_the_union() -> None:
    """Section 8.2.2 separates semantic hits from lucky literal ones.

    Luck needs only one clause to match, and a union would let the figure fall
    as gold is added — an item would look less literal for being better
    annotated.
    """
    question = "alpha beta"
    clauses = ("alpha beta", "gamma delta epsilon zeta eta theta")

    assert question_gold_jaccard(question, clauses) == 1.0


def test_the_gold_figure_does_not_depend_on_the_order_of_the_clauses() -> None:
    question = "alpha beta"
    clauses = ("gamma delta", "alpha beta gamma")

    assert question_gold_jaccard(question, clauses) == question_gold_jaccard(
        question, tuple(reversed(clauses))
    )


def test_an_item_with_no_gold_clause_has_no_overlap_figure() -> None:
    """An unanswerable item is not in the stratification at all."""
    with pytest.raises(ValueError, match="gold"):
        question_gold_jaccard("alpha beta", ())


CLAUSE = (
    "An intermediary that chooses to forward the message MUST first remove the "
    "received Content-Length field and process the Transfer-Encoding prior to "
    "forwarding the message downstream."
)


def test_containment_asks_how_much_of_this_came_from_that() -> None:
    """Jaccard cannot answer it: a short criterion drawn entirely from a long
    clause scores low, which reads as independence."""
    taken = "the intermediary MUST first remove the received Content-Length field"

    assert containment(taken, CLAUSE) == 1.0
    assert jaccard_overlap(taken, CLAUSE) < 0.5


def test_containment_of_unrelated_text_is_zero() -> None:
    assert containment("alpha beta gamma", CLAUSE) == 0.0


def test_containment_of_nothing_is_zero_rather_than_undefined() -> None:
    assert containment("", CLAUSE) == 0.0


def test_a_criterion_that_preserves_the_clauses_sentence_is_a_restatement() -> None:
    restatement = (
        "requires a forwarding intermediary to remove the received "
        "Content-Length and process Transfer-Encoding first"
    )

    assert restates(restatement, CLAUSE) is True


def test_a_criterion_written_as_a_judgement_standard_is_not() -> None:
    """Measured at 33% containment on the first drafted item, against 93% for
    the restatement above. The gap is what makes the line placeable."""
    criterion = (
        "recognizes that both framing fields trigger the conflicting-framing rule"
    )

    assert restates(criterion, CLAUSE) is False


def test_a_criterion_too_short_to_be_a_sentence_is_never_a_restatement() -> None:
    """Three words can be 100% clause vocabulary without reproducing wording."""
    assert containment("Content-Length field", CLAUSE) == 1.0
    assert restates("Content-Length field", CLAUSE) is False
