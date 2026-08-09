"""Wrong answers chosen by where a clause sits, never by what a retriever scored.

A forced choice is only a test if the wrong answers are plausible. Plausible
here means structurally near: the clause next door, then the sibling section,
then the same top-level section. Those are the confusions an author reading the
document actually makes. Clauses drawn at random are obvious, and clauses drawn
from the system's own ranking would put the retriever back inside the gold path
through the side door — which §8.2.1 forbids, and which the report would have no
way to detect afterwards.

`select_distractors` therefore has no parameter that could carry a query, a
score, or a ranked list. That is the enforcement. A rule saying "do not pass the
retriever's hits in here" survives exactly as long as the person who remembers
it; a signature that cannot accept them survives the codebase.

The tiers are the source's own section numbering read as nested scopes: the
gold's section, then its parent, then higher ancestors, then its top-level
section, then the rest of the document. Because each scope contains the one
before it, "widen only when the nearer tier is exhausted" is a property of the
ordering rather than a rule someone has to apply.

Nothing here reads clause text. A `Clause` is a locator with counts, so a
distractor set cannot leak prose even into a log.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from specpilot.corpus.clauses import Clause
from specpilot.corpus.walk import SEPARATOR


class DistractorTier(StrEnum):
    """How far the selector had to widen, nearest first.

    Declaration order is the search order, so adding a tier in the wrong place
    changes behaviour rather than only naming.

    ``SAME_SECTION`` covers the gold's own section *and its subsections*: §5.6.2
    contains §5.6.2.1, and a reader citing the outer number has cited both.
    ``SAME_PARENT`` is the enclosing section's other children and its own
    clauses; ``SAME_ANCESTOR`` is a higher ancestor below the top level, which
    only exists for sections nested four deep or more.
    """

    SAME_SECTION = "same_section"
    SAME_PARENT = "same_parent"
    SAME_ANCESTOR = "same_ancestor"
    SAME_TOP_LEVEL = "same_top_level"
    SAME_DOCUMENT = "same_document"


_TIER_ORDER = {tier: index for index, tier in enumerate(DistractorTier)}


@dataclass(frozen=True, slots=True)
class Distractor:
    clause: Clause
    tier: DistractorTier


class UnknownGoldClauseError(ValueError):
    """The gold clause is not in the pool the distractors are drawn from.

    Raised rather than returning an empty set, because a candidate list built
    without its right answer is a choice the reviewer cannot get right, and it
    would look like an ordinary review afterwards.
    """


def _components(section_number: str | None) -> tuple[str, ...]:
    """Split a section number into its place in the numbering tree.

    An unnumbered section has no position in that tree, so it gets none here.
    It can still be the gold's own section — matched by anchor — but it is not
    near anything else, and guessing a position for it would invent structure
    the document did not publish.
    """
    if not section_number:
        return ()
    return tuple(section_number.split("."))


def _shared(gold: tuple[str, ...], candidate: tuple[str, ...]) -> int:
    shared = 0
    for left, right in zip(gold, candidate, strict=False):
        if left != right:
            break
        shared += 1
    return shared


def _tier(shared: int, gold_depth: int, same_anchor: bool) -> DistractorTier:
    if same_anchor or (gold_depth and shared == gold_depth):
        return DistractorTier.SAME_SECTION
    if shared < 1:
        return DistractorTier.SAME_DOCUMENT
    if shared == gold_depth - 1:
        return DistractorTier.SAME_PARENT
    if shared == 1:
        return DistractorTier.SAME_TOP_LEVEL
    return DistractorTier.SAME_ANCESTOR


def _ordering_key(seed: str, clause_id: str) -> bytes:
    """Shuffle within a tier, reproducibly.

    The order has to vary between items or the same few clauses become the
    wrong answer every time and a reviewer learns them. It also has to be
    recoverable from the record, or a review cannot be reconstructed — so it is
    a hash of the seed and the clause, not a random number generator whose state
    nobody wrote down.
    """
    return hashlib.sha256(f"{seed}{SEPARATOR}{clause_id}".encode()).digest()


def select_distractors(
    clauses: tuple[Clause, ...] | list[Clause],
    gold_clause_id: str,
    *,
    count: int,
    seed: str,
) -> tuple[Distractor, ...]:
    """Pick ``count`` structurally near clauses to sit beside the gold.

    Returns fewer than asked for when the document has fewer to give. That is
    deliberate: a short candidate set is visible to the caller and refused by
    `ReviewDecision`, whereas padding it from further away would hide how thin
    the section was.
    """
    if count < 1:
        raise ValueError("a forced choice needs at least one wrong answer")

    gold = next((c for c in clauses if c.clause_id == gold_clause_id), None)
    if gold is None:
        raise UnknownGoldClauseError("the gold clause is not in the clause pool")

    gold_parts = _components(gold.section_number)
    scored: list[tuple[DistractorTier, int, Clause]] = []
    for candidate in clauses:
        # A clause from another RFC is not a plausible wrong answer, it is a
        # giveaway: the gold is bound to one document and version, so anything
        # else is excluded before a tier is even considered.
        if (
            candidate.clause_id == gold_clause_id
            or candidate.document_id != gold.document_id
            or candidate.document_version != gold.document_version
        ):
            continue
        shared = _shared(gold_parts, _components(candidate.section_number))
        tier = _tier(
            shared,
            len(gold_parts),
            candidate.section_anchor == gold.section_anchor,
        )
        scored.append((tier, shared, candidate))

    # Deeper agreement first within a tier, so `same_ancestor` — which spans
    # every ancestor below the top level — still runs nearest outwards.
    scored.sort(
        key=lambda row: (
            _TIER_ORDER[row[0]],
            -row[1],
            _ordering_key(seed, row[2].clause_id),
        )
    )
    return tuple(
        Distractor(clause=candidate, tier=tier) for tier, _, candidate in scored[:count]
    )


__all__ = [
    "Distractor",
    "DistractorTier",
    "UnknownGoldClauseError",
    "select_distractors",
]
