"""Resolve ledger disclosures back to the clauses they disclosed.

Without this, `citation_count: 0` is unreadable. It can mean the evidence path
never surfaced the governing clause, or that the model was shown it and declined
to cite it. Those are failures in different components with different fixes, and
the ledger is the only record that separates them — the model's own rationale
does not, because a model that ignored a clause is entirely capable of stating
that it was never given one.

A `disclosure_id` is the SHA-256 of the canonical
`(corpus_manifest_id, content_hash, quote_hash, normalized_excerpt_span)` tuple
rather than of the excerpt text: §3.2 counts a disclosure unit by that composite
so a long clause cannot be resliced into several spans and still occupy one
slot. Hashing the text alone builds a table that matches no ledger row, and an
empty intersection reads as "nothing was disclosed" rather than "the key is
wrong" — which is exactly how it reads while it is wrong.

The corpus digest is checked rather than assumed. A table built from another
rendition resolves nothing, and the resulting report says total retrieval
failure when the truth is a wrong corpus.

Locators only. Resolving a disclosure says which clause left, never its text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from specpilot.contracts.egress import NormalizedExcerptSpan
from specpilot.egress.policy import disclosure_id


class CorpusMismatchError(ValueError):
    """The corpus is not the one the ledger rows were written against."""


class _Unit(Protocol):
    """Read-only on purpose.

    A protocol with mutable attributes demands invariance, which a frozen
    `IndexUnit` cannot satisfy — and the resolver only ever reads.
    """

    @property
    def unit_id(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def ordinal(self) -> int: ...

    @property
    def section_number(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class DisclosedUnit:
    clause_id: str
    section_number: str | None


@dataclass(frozen=True, slots=True)
class ResolvedDisclosures:
    clauses: tuple[DisclosedUnit, ...]
    unresolved: tuple[str, ...]


def build_disclosure_index(
    units: Iterable[_Unit],
    *,
    corpus_manifest_id: str,
    expected_derived_sha256: str | None = None,
    derived_sha256: str | None = None,
) -> dict[str, DisclosedUnit]:
    """Map every unit's disclosure identifier to its locator.

    Pass both digests to have the binding checked. Omitting them is for callers
    that have already verified the corpus; passing one without the other is
    treated as unverified rather than as a pass.
    """
    if (
        expected_derived_sha256 is not None
        and derived_sha256 is not None
        and expected_derived_sha256 != derived_sha256
    ):
        raise CorpusMismatchError(
            "corpus digest does not match the manifest the ledger names"
        )

    index: dict[str, DisclosedUnit] = {}
    for unit in units:
        text = unit.text
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        span = NormalizedExcerptSpan(
            paragraph_start=unit.ordinal,
            paragraph_end=unit.ordinal,
            token_start=0,
            token_end=max(len(text.split()), 1),
        )
        identifier = disclosure_id(
            corpus_manifest_id, content_hash, content_hash, span
        )
        index[identifier] = DisclosedUnit(
            clause_id=unit.unit_id, section_number=unit.section_number
        )
    return index


def resolve_disclosures(
    disclosure_ids: Sequence[str],
    index: dict[str, DisclosedUnit],
) -> ResolvedDisclosures:
    """Resolve what can be resolved and name the rest.

    An unresolved identifier is reported, never dropped: silently omitting it
    understates what left the boundary, which is the one quantity the ledger
    exists to keep honest.
    """
    clauses: list[DisclosedUnit] = []
    unresolved: list[str] = []
    for identifier in disclosure_ids:
        found = index.get(identifier)
        if found is None:
            unresolved.append(identifier)
        else:
            clauses.append(found)
    return ResolvedDisclosures(
        clauses=tuple(clauses), unresolved=tuple(unresolved)
    )
