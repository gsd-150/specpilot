"""The E-context arm of evidence-budget comparison A' (plan §8.5.2).

Pure functions only: the arm must be reconstructible for the same item on both
sides of the comparison, and the locked run will compare the two payloads byte
for byte. Nothing here reaches a provider or the ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from specpilot.corpus.indexable import IndexUnit
from specpilot.retrieval.expansion import (
    EXCERPT_BYTE_CAP,
    MAX_EXCERPTS,
    MAX_TOKENS,
    arms_identical,
    expand_evidence_context,
    gold_has_section_context,
)


def unit(
    uid: str,
    *,
    section: str = "6.3",
    ordinal: int = 1,
    text: str | None = None,
    document: str = "ietf-rfc-9112",
) -> IndexUnit:
    return IndexUnit(
        unit_id=uid,
        kind="clause",
        document_id=document,
        document_version="2022-06",
        section_number=section,
        section_path="Message Body Length",
        ordinal=ordinal,
        text=text if text is not None else f"clause text of {uid}",
        indexed=text if text is not None else f"clause text of {uid}",
    )


def section_of(
    units: dict[str, IndexUnit],
) -> Callable[[IndexUnit], Sequence[IndexUnit]]:
    def siblings(hit: IndexUnit) -> Sequence[IndexUnit]:
        group = [u for u in units.values() if u.section_number == hit.section_number]
        return tuple(sorted(group, key=lambda u: u.ordinal))

    return siblings


def resolve_of(
    units: dict[str, IndexUnit],
) -> Callable[[str], IndexUnit]:
    return lambda uid: units[uid]


SECTION_UNITS = {
    "c1": unit("c1", ordinal=1),
    "c2": unit("c2", ordinal=2),
    "c3": unit("c3", ordinal=3),
    "c4": unit("c4", ordinal=4),
}


def test_a_single_clause_section_makes_the_arms_identical() -> None:
    solo = {"solo": unit("solo", section="7.1")}

    result = expand_evidence_context([solo["solo"]], section_of(solo))

    assert result.excerpt_count == 1
    assert result.identical_to_narrow is True
    assert arms_identical([solo["solo"]], result.units)


def test_a_hit_expands_through_its_section_in_order_hit_first() -> None:
    hit = SECTION_UNITS["c2"]

    result = expand_evidence_context([hit], section_of(SECTION_UNITS))

    # The hit is the ranked evidence and goes first; then the section's later
    # paragraphs in ascending order, then the earlier ones — section order
    # starting from the hit.
    assert [u.unit_id for u in result.units] == ["c2", "c3", "c4", "c1"]
    assert result.excerpt_count == 4


def test_two_hits_in_one_section_emit_each_section_clause_once() -> None:
    result = expand_evidence_context(
        [SECTION_UNITS["c2"], SECTION_UNITS["c4"]], section_of(SECTION_UNITS)
    )

    assert [u.unit_id for u in result.units] == ["c2", "c3", "c4", "c1"]
    assert len({u.unit_id for u in result.units}) == len(result.units)


def test_a_later_hit_still_claims_its_own_clause_first() -> None:
    units = {
        **SECTION_UNITS,
        "c9": unit("c9", ordinal=9, text="far clause"),
    }

    result = expand_evidence_context(
        [units["c2"], units["c9"]], section_of(units)
    )

    assert [u.unit_id for u in result.units] == ["c2", "c3", "c4", "c9", "c1"]
    assert result.excerpt_count == MAX_EXCERPTS


def test_the_excerpt_budget_stops_at_five() -> None:
    units = {f"p{i}": unit(f"p{i}", ordinal=i) for i in range(1, 9)}

    result = expand_evidence_context([units["p1"]], section_of(units))

    assert [u.unit_id for u in result.units] == ["p1", "p2", "p3", "p4", "p5"]
    assert result.excerpt_count == MAX_EXCERPTS


def test_the_token_budget_stops_before_the_next_clause_would_breach_it() -> None:
    fat = "x" * 800  # 800 bytes under the default byte-upper-bound counter
    units = {
        "a": unit("a", ordinal=1, text=fat),
        "b": unit("b", ordinal=2, text=fat),
        "c": unit("c", ordinal=3, text=fat),
        "d": unit("d", ordinal=4, text=fat),
    }

    result = expand_evidence_context([units["a"]], section_of(units))

    # 3 × 800 fits, the fourth would total 3200 > 2560.
    assert [u.unit_id for u in result.units] == ["a", "b", "c"]
    assert result.token_total <= MAX_TOKENS
    assert result.token_total + len(units["d"].text.encode("utf-8")) > MAX_TOKENS


def test_a_sibling_over_the_per_excerpt_caps_raises_not_skips() -> None:
    over = "y" * (EXCERPT_BYTE_CAP + 1)
    units = {
        "ok": unit("ok", ordinal=1),
        "over": unit("over", ordinal=2, text=over),
    }

    # The sibling sits inside the token budget, so the only thing standing
    # between it and the wire is the per-excerpt cap -- which must raise, not
    # silently drop it and hand the model a section with a hole in it.
    with pytest.raises(ValueError, match="byte cap"):
        expand_evidence_context(
            [units["ok"]], section_of(units), max_tokens=20000
        )


def test_the_expansion_is_deterministic() -> None:
    first = expand_evidence_context(
        [SECTION_UNITS["c3"]], section_of(SECTION_UNITS)
    )
    second = expand_evidence_context(
        [SECTION_UNITS["c3"]], section_of(SECTION_UNITS)
    )

    assert [u.unit_id for u in first.units] == [u.unit_id for u in second.units]
    assert first.token_total == second.token_total


def test_expansion_never_exceeds_the_shared_budget_by_construction() -> None:
    import random

    rng = random.Random(7)
    units = {
        f"r{i}": unit(f"r{i}", ordinal=i, text="word " * rng.randint(10, 300))
        for i in range(1, 12)
    }
    for hit_id in ("r1", "r3", "r9"):
        result = expand_evidence_context(
            [units[hit_id]], section_of(units)
        )
        assert result.excerpt_count <= MAX_EXCERPTS
        assert result.token_total <= MAX_TOKENS
        assert all(
            len(u.text.encode("utf-8")) <= EXCERPT_BYTE_CAP
            for u in result.units
        )


def test_a_real_tokenizer_callers_token_cap_is_enforced() -> None:
    def words(text: str) -> int:
        return len(text.split())

    units = {
        "ok": unit("ok", ordinal=1, text="one two three"),
        "over": unit("over", ordinal=2, text=" ".join(f"w{i}" for i in range(12))),
    }

    with pytest.raises(ValueError, match="token cap"):
        expand_evidence_context(
            [units["ok"]],
            section_of(units),
            counter=words,
            excerpt_token_cap=10,
        )


def test_the_stratification_key_reads_the_gold_not_the_hits() -> None:
    solo_gold = {"g": unit("g", section="7.1")}
    grouped_gold = {"g": unit("g", section="6.3")}

    assert (
        gold_has_section_context(
            ["g"], resolve_of(solo_gold), section_of(solo_gold)
        )
        is False
    )
    assert (
        gold_has_section_context(
            ["g"], resolve_of(grouped_gold), section_of(SECTION_UNITS)
        )
        is True
    )


def test_arms_identical_is_order_sensitive_like_the_wire() -> None:
    assert arms_identical(
        [SECTION_UNITS["c1"], SECTION_UNITS["c2"]],
        [SECTION_UNITS["c1"], SECTION_UNITS["c2"]],
    )
    assert not arms_identical(
        [SECTION_UNITS["c1"], SECTION_UNITS["c2"]],
        [SECTION_UNITS["c2"], SECTION_UNITS["c1"]],
    )
