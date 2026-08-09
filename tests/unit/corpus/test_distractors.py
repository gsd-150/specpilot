"""Distractors chosen by where a clause sits, never by what a retriever scored.

A forced choice is only a test if the wrong answers are plausible. Plausible
here means structurally near — the clause next door, then the sibling section,
then the same top-level section — because that is what an author reading the
document would confuse. Drawing them at random makes every choice obvious;
drawing them from the retriever's own ranking puts the system inside the gold
path through the side door, which §8.2.1 forbids.

The tiers are nested scopes of the source's own section numbering, so "widen
only when a tier is exhausted" is a property of the ordering rather than a rule
someone has to remember.
"""

from __future__ import annotations

import inspect
from collections import Counter
from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import Clause, ClauseLimits, build_clauses
from specpilot.corpus.distractors import (
    DistractorTier,
    UnknownGoldClauseError,
    select_distractors,
)
from tests.helpers import rfc_factory

# Section tree, with two paragraphs in every section:
#
#   1        intro       1.1  scope
#                        1.2  terms      1.2.1    terms-a
#                                        1.2.1.1  terms-a-1
#                                        1.2.2    terms-b
#   2        other       2.1  other-sub
#
# Depth 4 exists so the tier between "the parent" and "the top-level section"
# has something to hold. RFC 9110 has 15 sections that deep.
STRUCTURED_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Structure</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="intro" numbered="true">
      <name>Introduction</name>
      <t>Intro paragraph one.</t>
      <t>Intro paragraph two.</t>
      <section anchor="scope" numbered="true">
        <name>Scope</name>
        <t>Scope paragraph one.</t>
        <t>Scope paragraph two.</t>
      </section>
      <section anchor="terms" numbered="true">
        <name>Terms</name>
        <t>Terms paragraph one.</t>
        <t>Terms paragraph two.</t>
        <section anchor="terms-a" numbered="true">
          <name>Term A</name>
          <t>Term A paragraph one.</t>
          <t>Term A paragraph two.</t>
          <section anchor="terms-a-1" numbered="true">
            <name>Term A Refined</name>
            <t>Term A refined paragraph one.</t>
            <t>Term A refined paragraph two.</t>
          </section>
        </section>
        <section anchor="terms-b" numbered="true">
          <name>Term B</name>
          <t>Term B paragraph one.</t>
          <t>Term B paragraph two.</t>
        </section>
      </section>
    </section>
    <section anchor="other" numbered="true">
      <name>Other</name>
      <t>Other paragraph one.</t>
      <t>Other paragraph two.</t>
      <section anchor="other-sub" numbered="true">
        <name>Other Sub</name>
        <t>Other sub paragraph one.</t>
        <t>Other sub paragraph two.</t>
      </section>
    </section>
  </middle>
</rfc>
"""

OTHER_DOCUMENT_XML = STRUCTURED_XML.replace('number="9999"', 'number="9998"')

SEED = "r1-2026-08"


@pytest.fixture
def clauses(tmp_path: Path) -> tuple[Clause, ...]:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    path = rfc_factory.write(directory, "structured.xml", STRUCTURED_XML)
    return build_clauses(path, RfcLimits(), ClauseLimits())


def in_section(clauses: tuple[Clause, ...], anchor: str) -> tuple[Clause, ...]:
    return tuple(clause for clause in clauses if clause.section_anchor == anchor)


def test_the_gold_clause_is_never_offered_as_a_distractor(
    clauses: tuple[Clause, ...],
) -> None:
    gold = in_section(clauses, "terms-a")[0]

    picked = select_distractors(
        clauses, gold.clause_id, count=len(clauses), seed=SEED
    )

    assert gold.clause_id not in {item.clause.clause_id for item in picked}
    assert len(picked) == len(clauses) - 1


def test_the_nearest_tier_is_used_first(clauses: tuple[Clause, ...]) -> None:
    gold = in_section(clauses, "terms-a")[0]

    picked = select_distractors(clauses, gold.clause_id, count=2, seed=SEED)

    assert [item.tier for item in picked] == [DistractorTier.SAME_SECTION] * 2


def test_a_subsection_of_the_gold_section_is_still_the_same_section(
    clauses: tuple[Clause, ...],
) -> None:
    """§1.2.1.1 is inside §1.2.1. A reader citing the outer number covers both."""
    gold = in_section(clauses, "terms-a")[0]

    picked = select_distractors(clauses, gold.clause_id, count=3, seed=SEED)

    assert {item.tier for item in picked} == {DistractorTier.SAME_SECTION}
    assert {item.clause.section_anchor for item in picked} == {
        "terms-a",
        "terms-a-1",
    }


def test_a_tier_widens_only_once_it_is_exhausted(
    clauses: tuple[Clause, ...],
) -> None:
    gold = in_section(clauses, "terms-a")[0]

    exact = select_distractors(clauses, gold.clause_id, count=3, seed=SEED)
    one_more = select_distractors(clauses, gold.clause_id, count=4, seed=SEED)

    assert Counter(item.tier for item in exact) == {DistractorTier.SAME_SECTION: 3}
    assert Counter(item.tier for item in one_more) == {
        DistractorTier.SAME_SECTION: 3,
        DistractorTier.SAME_PARENT: 1,
    }


def test_every_tier_is_reached_in_order_of_structural_distance(
    clauses: tuple[Clause, ...],
) -> None:
    """From the deepest section, all five scopes are populated and ordered."""
    gold = in_section(clauses, "terms-a-1")[0]

    picked = select_distractors(clauses, gold.clause_id, count=15, seed=SEED)

    assert [item.tier for item in picked] == [
        DistractorTier.SAME_SECTION,
        DistractorTier.SAME_PARENT,
        DistractorTier.SAME_PARENT,
        *[DistractorTier.SAME_ANCESTOR] * 4,
        *[DistractorTier.SAME_TOP_LEVEL] * 4,
        *[DistractorTier.SAME_DOCUMENT] * 4,
    ]


def test_selection_is_reproducible_from_its_seed(
    clauses: tuple[Clause, ...],
) -> None:
    """A recorded review has to be reconstructable, candidates included."""
    gold = in_section(clauses, "terms-a")[0]

    first = select_distractors(clauses, gold.clause_id, count=6, seed=SEED)
    again = select_distractors(clauses, gold.clause_id, count=6, seed=SEED)

    assert [item.clause.clause_id for item in first] == [
        item.clause.clause_id for item in again
    ]


def test_a_different_seed_selects_a_different_set(
    clauses: tuple[Clause, ...],
) -> None:
    """Within a tier the order is the seed's, so one item's set is not every
    item's set — otherwise the same four clauses are the wrong answer all day."""
    gold = in_section(clauses, "terms-a")[0]

    picked = {
        seed: tuple(
            item.clause.clause_id
            for item in select_distractors(clauses, gold.clause_id, count=4, seed=seed)
        )
        for seed in (f"seed-{n}" for n in range(12))
    }

    assert len(set(picked.values())) > 1


def test_the_signature_admits_no_query_score_or_ranking_input() -> None:
    """§8.2.1 kept out by the type system rather than by a reviewer's memory.

    A later caller cannot pass the retriever's hits into this even by accident,
    because there is no parameter that would take them.
    """
    parameters = inspect.signature(select_distractors).parameters

    assert set(parameters) == {"clauses", "gold_clause_id", "count", "seed"}


def test_a_distractor_never_comes_from_another_document(
    tmp_path: Path, clauses: tuple[Clause, ...]
) -> None:
    """A clause from a different RFC is not a distractor, it is a giveaway."""
    directory = tmp_path / "corpus"
    other = build_clauses(
        rfc_factory.write(directory, "other.xml", OTHER_DOCUMENT_XML),
        RfcLimits(),
        ClauseLimits(),
    )
    gold = in_section(clauses, "terms-a")[0]

    picked = select_distractors(
        (*clauses, *other), gold.clause_id, count=len(clauses) + len(other), seed=SEED
    )

    assert {item.clause.document_id for item in picked} == {gold.document_id}


def test_a_gold_clause_the_pool_does_not_contain_is_refused(
    clauses: tuple[Clause, ...],
) -> None:
    """Returning an empty set would present a choice with no right answer in it."""
    with pytest.raises(UnknownGoldClauseError):
        select_distractors(clauses, "f" * 64, count=3, seed=SEED)


def test_asking_for_more_than_the_document_holds_returns_what_there_is(
    clauses: tuple[Clause, ...],
) -> None:
    """Fewer candidates than asked for is visible; a fabricated one would not be.

    The contract refuses a review with fewer than two candidates, so a short
    set fails where it can be seen rather than here.
    """
    gold = in_section(clauses, "terms-a")[0]

    picked = select_distractors(clauses, gold.clause_id, count=500, seed=SEED)

    assert len(picked) == len(clauses) - 1


@pytest.mark.parametrize("count", [0, -1])
def test_a_choice_needs_at_least_one_wrong_answer(
    clauses: tuple[Clause, ...], count: int
) -> None:
    gold = in_section(clauses, "terms-a")[0]

    with pytest.raises(ValueError, match="at least one"):
        select_distractors(clauses, gold.clause_id, count=count, seed=SEED)


def test_a_distractor_carries_a_locator_and_no_text(
    clauses: tuple[Clause, ...],
) -> None:
    """A `Clause` never held prose, so a distractor set cannot leak any."""
    gold = in_section(clauses, "terms-a")[0]

    picked = select_distractors(clauses, gold.clause_id, count=3, seed=SEED)

    for item in picked:
        assert item.clause.section_number
        assert not any(
            isinstance(value, str) and " " in value
            for value in (item.clause.clause_id, item.clause.section_anchor)
        )
