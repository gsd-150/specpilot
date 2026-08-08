"""The sparse retrieval route: independent BM25, no model.

Independent is the whole point. §8.2.3's pooling argument rests on the two
routes not sharing a representation, so this is pure statistics and shares
nothing with BGE-M3 — not the tokenizer, not a learned sparse head, not a
vocabulary. If the two routes agreed because they were the same model wearing
two hats, "reducing bias toward our own system" would be a sentence with no
content behind it.

**The tokenizer is the part that decides whether this works.** §4.1.6 warns that
splitting on whitespace and punctuation destroys exactly the terms that carry
the most signal, and the warning transfers to RFC with new vocabulary: `5.6.2`
becomes three meaningless digits, `Content-Length` becomes two common words,
`HTTP/1.1` becomes a protocol name and two ones. Compounds are therefore kept
whole — and their alphabetic parts emitted alongside, so a question written
"content length" still reaches a clause that says `Content-Length`.

This module deliberately does not reuse `corpus/overlap.py`'s tokenizer. That
one shatters compounds on purpose, because a stratification key wants crude,
stable set overlap. Sharing them would quietly make one of the two wrong.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# Bumped whenever tokenization changes. §8.2.3 requires the pooling
# configuration to be reconstructible, and a different tokenizer is a
# different index even over identical text.
TOKENIZER_VERSION = "bm25-rfc/v1"

# A compound is alphanumerics joined by the separators RFC vocabulary uses:
# 5.6.2, Content-Length, HTTP/1.1, obs_fold.
_COMPOUND = re.compile(r"[0-9a-z]+(?:[-./_][0-9a-z]+)*")
_SEPARATOR = re.compile(r"[-./_]")
# Letters and digits run together in RFC9110; splitting the boundary lets it
# reach the same clauses as "RFC 9110".
_ALPHANUM_BOUNDARY = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")

_MIN_PART = 2


def _parts(compound: str) -> Iterable[str]:
    """Yield sub-terms worth indexing beside the compound itself.

    Only alphabetic runs of two characters or more. Digits pulled out of a
    compound are noise — the `5` in `5.6.2` matches every numbered section in
    the corpus — and single characters match everything.
    """
    for piece in _SEPARATOR.split(compound):
        for part in _ALPHANUM_BOUNDARY.split(piece):
            if len(part) >= _MIN_PART and part.isalpha():
                yield part
            elif len(part) >= _MIN_PART and part.isdigit() and part != piece:
                # A digit run that was glued to letters, as in RFC9110, is a
                # real term. A digit run that was split off a dotted number is
                # not, which is why `piece` has to differ from `part`.
                yield part


def tokenize(text: str) -> list[str]:
    """Return the index terms for a string, compounds kept whole."""
    tokens: list[str] = []
    for match in _COMPOUND.finditer(text.lower()):
        compound = match.group()
        tokens.append(compound)
        if _SEPARATOR.search(compound) or _ALPHANUM_BOUNDARY.search(compound):
            tokens.extend(_parts(compound))
    return tokens


@dataclass(frozen=True, slots=True)
class Bm25Parameters:
    """Frozen at the standard baseline, recorded, and not tuned.

    §8.2.3 is explicit: the online sparse route is this same implementation, so
    later tuning would change what pooling did. Pooling runs once, before any
    tuning, and the configuration it ran under has to be reconstructible from
    the gold metadata afterwards.
    """

    k1: float = 1.2
    b: float = 0.75


@dataclass(frozen=True, slots=True)
class Bm25Hit:
    unit_id: str
    score: float


@dataclass(frozen=True, slots=True)
class Bm25Index:
    parameters: Bm25Parameters
    tokenizer_version: str
    document_count: int
    average_length: float
    fingerprint: str
    _frequencies: dict[str, Counter[str]] = field(repr=False)
    _lengths: dict[str, int] = field(repr=False)
    _document_frequency: Counter[str] = field(repr=False)

    @classmethod
    def build(
        cls,
        units: Sequence[tuple[str, str]],
        parameters: Bm25Parameters | None = None,
    ) -> Bm25Index:
        """Index `(unit_id, text)` pairs. Clauses and tables both qualify."""
        if not units:
            raise ValueError("cannot build an index over an empty corpus")

        settings = parameters or Bm25Parameters()
        frequencies: dict[str, Counter[str]] = {}
        lengths: dict[str, int] = {}
        document_frequency: Counter[str] = Counter()
        digest = hashlib.sha256(
            f"{TOKENIZER_VERSION}\x1f{settings.k1}\x1f{settings.b}".encode()
        )

        for unit_id, text in units:
            if unit_id in frequencies:
                raise ValueError(f"duplicate unit id {unit_id!r}")
            tokens = tokenize(text)
            counts = Counter(tokens)
            frequencies[unit_id] = counts
            lengths[unit_id] = len(tokens)
            document_frequency.update(counts.keys())
            digest.update(unit_id.encode("utf-8"))
            digest.update(b"\x1f")
            digest.update(hashlib.sha256(text.encode("utf-8")).digest())
            digest.update(b"\x1e")

        total = sum(lengths.values())
        return cls(
            parameters=settings,
            tokenizer_version=TOKENIZER_VERSION,
            document_count=len(units),
            average_length=total / len(units),
            fingerprint=digest.hexdigest(),
            _frequencies=frequencies,
            _lengths=lengths,
            _document_frequency=document_frequency,
        )

    def _idf(self, term: str) -> float:
        """Okapi IDF, computed over the frozen corpus and stored with the index.

        The `1 +` inside the logarithm is what keeps this positive. Without it
        the ratio drops below one as soon as a term appears in more than half
        the corpus, and a common term becomes a penalty — ranking documents by
        how little they say. Smoothed this way a ubiquitous term is merely
        cheap, which is what it should be.

        An unseen term returns zero, so a query of nothing but unknown words
        matches nothing rather than everything.
        """
        seen = self._document_frequency.get(term, 0)
        if seen == 0:
            return 0.0
        total = self.document_count
        return math.log(1 + (total - seen + 0.5) / (seen + 0.5))

    def search(self, query: str, k: int) -> list[Bm25Hit]:
        """Return the top `k` units, best first, dropping zero scores."""
        if k <= 0:
            raise ValueError("k must be positive")
        query_terms = set(tokenize(query))
        weights = {term: self._idf(term) for term in query_terms}
        weights = {term: weight for term, weight in weights.items() if weight > 0.0}
        if not weights:
            return []

        k1, b = self.parameters.k1, self.parameters.b
        scored: list[Bm25Hit] = []
        for unit_id, counts in self._frequencies.items():
            length = self._lengths[unit_id]
            norm = k1 * (1 - b + b * length / self.average_length)
            total = 0.0
            for term, weight in weights.items():
                seen = counts.get(term, 0)
                if seen:
                    total += weight * (seen * (k1 + 1)) / (seen + norm)
            if total > 0.0:
                scored.append(Bm25Hit(unit_id, total))

        # Sorted by identifier as well as score, so equal scores order the same
        # way on every run. Pooling is audited long after it is executed.
        scored.sort(key=lambda hit: (-hit.score, hit.unit_id))
        return scored[:k]
