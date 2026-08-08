"""Literal overlap between a question and its gold clause.

Section 8.2.2 exists because pure clause-first annotation reuses the clause's
own wording, and a retriever can then hit those items by literal matching alone
while looking like it understood them. Reporting Macro-Recall stratified by
overlap separates the two. That makes this number part of the evaluation's
measuring instrument, so it is frozen here rather than recomputed by hand per
item: twenty-three hand-computed figures would not be the same measure twice.

The figure is deliberately crude — set intersection over set union, no
stemming, no stopword list, no weighting. Crude and stable beats clever and
drifting for a stratification key, and anything smarter would need its own
justification in the report.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens, as a set.

    A set, not a list: repetition is a property of the writing, not of the
    literal overlap between two texts, and a clause that says "response" ten
    times is not ten times more literally similar to a question that says it
    once.
    """
    return set(_TOKEN.findall(text.lower()))


def jaccard_overlap(first: str, second: str) -> float:
    """Return |A ∩ B| / |A ∪ B| over the two token sets."""
    left, right = tokenize(first), tokenize(second)
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


# A key point of at least this many tokens whose vocabulary is this fully drawn
# from its gold clause is restating the clause rather than stating a criterion.
#
# Both numbers are decisions recorded here, not dials. Measured on the first
# drafted L2 item: two criteria written as judgement standards sat at 33%
# containment and one that preserved the clause's sentence sat at 93%. The gap
# between them is wide, so 0.8 catches the restatement without touching the
# others. The length floor exists because a three-word criterion can be 100%
# clause vocabulary without reproducing any expression — §8.1 forbids
# reproducing wording as sentences, and three words are not a sentence.
RESTATEMENT_CONTAINMENT = 0.8
RESTATEMENT_MIN_TOKENS = 8


def containment(text: str, within: str) -> float:
    """The fraction of `text`'s tokens that also appear in `within`.

    Asymmetric on purpose, and the right measure for "how much of this was
    taken from that". Jaccard cannot answer it: a 15-token criterion drawn
    entirely from a 73-token clause scores 0.21, which looks like independence.
    """
    left = tokenize(text)
    if not left:
        return 0.0
    return len(left & tokenize(within)) / len(left)


def restates(criterion: str, clause_text: str) -> bool:
    """True when a key point reproduces its clause's wording (§8.1)."""
    if len(tokenize(criterion)) < RESTATEMENT_MIN_TOKENS:
        return False
    return containment(criterion, clause_text) > RESTATEMENT_CONTAINMENT


def question_gold_jaccard(question: str, gold_texts: Sequence[str]) -> float:
    """Return the overlap with the best-matching gold clause.

    The maximum, not the union. Overlap against the union falls as gold is
    added, so an item would look less literal for being better annotated — and
    the question this figure answers is whether literal matching could have hit
    the item at all, which needs only one clause to match.
    """
    if not gold_texts:
        raise ValueError("an item with no gold clause has no overlap figure")
    return max(jaccard_overlap(question, text) for text in gold_texts)
