"""The E-context arm of evidence-budget comparison A' (plan §8.5.2).

E-narrow is the frozen default: each retrieved hit goes out alone, so the
answer path discloses up to five clauses and nothing else. E-context spends the
same budget differently: every hit is expanded to its section-adjacent clauses
in section order, so a requirement the source split across neighbouring
paragraphs goes out together instead of as one half of itself.

Both arms share one budget -- five excerpts and 2560 tokens (5 × 512, the same
per-excerpt token figure the corpus QA blocks on) -- and neither may widen the
per-excerpt caps. An arm that needs the gate widened is an arm that destroys the
project's central claim; one that fills an under-used quota does not. This
module is a pure function of its inputs so the two arms can be built for the
same item and compared byte for byte.

The per-excerpt caps are asserted, not enforced: a section sibling over 512
tokens or 8 KiB raises rather than being skipped, because silently dropping it
would hand the model a section with a hole in it. On the frozen corpus that
case cannot arise -- corpus QA's excerpt_fit line cleared 0 of 1,907 clauses
over either cap -- so the raise is a tripwire for a future corpus, not a dial.

The default counter is the byte upper bound, deliberately: it is the exact
bound the runtime gate prices a reservation with, so anything this module
emits under it is guaranteed to fit the frozen caps with no gate change. A
caller with a real tokenizer may pass it; the real count is always <= the byte
upper bound, so a real-tokenizer caller emits at least as much, never more.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from specpilot.corpus.indexable import IndexUnit

# The shared outbound budget, mirrored from the frozen egress policy and
# corpus QA. These are not dials: the comparison is between two ways of
# spending the *same* budget, and widening it here would measure a third arm.
MAX_EXCERPTS = 5
MAX_TOKENS = 2560
EXCERPT_TOKEN_CAP = 512
EXCERPT_BYTE_CAP = 8192


def default_token_counter(text: str) -> int:
    """The byte upper bound -- what the runtime gate actually prices with.

    A byte-level BPE never emits more tokens than the text has UTF-8 bytes, so
    this bound is loose for a real tokenizer and never loose for the gate.
    """
    return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ContextExpansion:
    """One E-context excerpt set, with the numbers the comparison reports."""

    units: tuple[IndexUnit, ...]
    excerpt_count: int
    token_total: int
    byte_total: int
    identical_to_narrow: bool


def expand_evidence_context(
    hits: Sequence[IndexUnit],
    section_siblings: Callable[[IndexUnit], Sequence[IndexUnit]],
    *,
    counter: Callable[[str], int] = default_token_counter,
    max_excerpts: int = MAX_EXCERPTS,
    max_tokens: int = MAX_TOKENS,
    excerpt_token_cap: int | None = None,
    excerpt_byte_cap: int = EXCERPT_BYTE_CAP,
) -> ContextExpansion:
    """Expand each hit to its section-adjacent clauses under one shared budget.

    Emission order, pinned by the tests:

    1. Hits are walked in rank order. A hit's own clause goes out first -- it
       is the ranked evidence and must survive any budget cut.
    2. Then the hit's section is walked in section order starting from the hit:
       paragraphs with a higher ordinal in ascending order, then paragraphs
       with a lower ordinal in ascending order. The context nearest the hit
       fills the budget first.
    3. The first hit that touches a section claims its whole context: a later
       hit in the same section contributes nothing new, and every clause is
       emitted at most once.
    4. The budget is global and hard: emission stops the moment a further
       clause would pass five excerpts or 2560 tokens, whichever binds first.

    'section_siblings' must return the hit's section clauses sorted by
    ordinal; the hit itself is expected among them and is never emitted from
    the sibling walk twice.

    'excerpt_token_cap' defaults to disabled: the default counter is the
    byte upper bound, against which a 512-token cap would read as a 512-byte
    cap and wrongly exclude clauses the gate accepts. A caller using a real
    tokenizer passes the cap it wants enforced (512 for the frozen corpus).
    The byte cap is always enforced -- it is the gate's own binding
    per-excerpt limit.
    """
    if not hits:
        raise ValueError("E-context needs at least one hit")
    seen: set[str] = set()
    emitted: list[IndexUnit] = []
    token_total = 0
    byte_total = 0

    def _claim(unit_to_claim: IndexUnit) -> None:
        nonlocal token_total, byte_total
        if unit_to_claim.unit_id in seen:
            return
        tokens = counter(unit_to_claim.text)
        bytes_ = len(unit_to_claim.text.encode("utf-8"))
        if bytes_ > excerpt_byte_cap:
            raise ValueError(
                "section sibling " + unit_to_claim.unit_id
                + " breaches the excerpt byte cap; the gate would refuse it"
            )
        if excerpt_token_cap is not None and tokens > excerpt_token_cap:
            raise ValueError(
                "section sibling " + unit_to_claim.unit_id
                + " breaches the excerpt token cap; the gate would refuse it"
            )
        seen.add(unit_to_claim.unit_id)
        emitted.append(unit_to_claim)
        token_total += tokens
        byte_total += bytes_

    for hit in hits:
        if hit.unit_id in seen:
            continue
        section = tuple(section_siblings(hit))
        ordered = (
            (hit,)
            + tuple(u for u in section if u.ordinal > hit.ordinal)
            + tuple(u for u in section if u.ordinal < hit.ordinal)
        )
        for unit_to_claim in ordered:
            if unit_to_claim.unit_id in seen:
                continue
            if len(emitted) >= max_excerpts:
                break
            if token_total + counter(unit_to_claim.text) > max_tokens:
                break
            _claim(unit_to_claim)
        if len(emitted) >= max_excerpts or token_total >= max_tokens:
            break

    narrow_ids = tuple(hit.unit_id for hit in hits)
    return ContextExpansion(
        units=tuple(emitted),
        excerpt_count=len(emitted),
        token_total=token_total,
        byte_total=byte_total,
        identical_to_narrow=tuple(u.unit_id for u in emitted) == narrow_ids,
    )


def gold_has_section_context(
    gold_unit_ids: Sequence[str],
    resolve: Callable[[str], IndexUnit],
    section_siblings: Callable[[IndexUnit], Sequence[IndexUnit]],
) -> bool:
    """The A' stratification key: can the gold expand into a neighbour at all.

    Items whose gold clause sits in a single-clause section cannot benefit
    from E-context -- the two arms emit byte-identical payloads for them by
    construction -- so the comparison must count and list those separately
    rather than averaging them in (§8.5.2). This reads the *gold*, not the
    retrieved hits: the stratum describes the item, and the retrieved set is
    what the arm happens to have been handed.
    """
    return any(
        len(section_siblings(resolve(uid))) > 1 for uid in gold_unit_ids
    )


def arms_identical(
    narrow: Sequence[IndexUnit], expanded: Sequence[IndexUnit]
) -> bool:
    """Whether the two arms would send byte-identical payloads.

    Same units in the same order means same excerpt texts in the same order,
    which is what 'byte-identical on the wire' is made of. Order-sensitive on
    purpose: a payload is a sequence, and the parser reads one.
    """
    return tuple(u.unit_id for u in narrow) == tuple(u.unit_id for u in expanded)


__all__ = [
    "EXCERPT_BYTE_CAP",
    "EXCERPT_TOKEN_CAP",
    "MAX_EXCERPTS",
    "MAX_TOKENS",
    "ContextExpansion",
    "arms_identical",
    "default_token_counter",
    "expand_evidence_context",
    "gold_has_section_context",
]
