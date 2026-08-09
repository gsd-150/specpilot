"""Turn retrieved clauses into the evidence a request may actually disclose.

Two objects come out of one clause and they are deliberately different. The
`EvidenceExcerpt` is what leaves the machine: quote, hashes, span, and the
frozen identity the enforcer prices. The `DisclosedClause` never leaves: it is
the record of what was sent, which is what a citation is later checked against.

Keeping them separate is the point. Verifying a citation against the corpus asks
"does this clause exist"; verifying it against what was disclosed asks "did the
model see it". Only the second catches a model citing a real clause it was never
shown, and the two questions look identical until you have both objects.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from specpilot.answer.verify import DisclosedClause
from specpilot.contracts.egress import EvidenceExcerpt, NormalizedExcerptSpan
from specpilot.corpus.clauses import Clause


@dataclass(frozen=True, slots=True)
class Evidence:
    """One clause, in both the outbound and the local-only shape."""

    excerpt: EvidenceExcerpt
    disclosed: DisclosedClause


def build_evidence(
    clause: Clause, text: str, *, corpus_manifest_id: str
) -> Evidence:
    """Build one whole-clause excerpt.

    The span covers the clause end to end rather than a window inside it. A
    clause is already the unit the source chose to number, and the outbound caps
    are set against whole clauses, so slicing one would produce a citation
    pointing at text the reader cannot locate by the anchor it names.
    """
    if not text:
        raise ValueError("an excerpt needs text")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Evidence(
        excerpt=EvidenceExcerpt(
            corpus_manifest_id=corpus_manifest_id,
            content_hash=content_hash,
            quote=text,
            quote_hash=content_hash,
            span=NormalizedExcerptSpan(
                paragraph_start=clause.ordinal,
                paragraph_end=clause.ordinal,
                token_start=0,
                token_end=max(clause.word_count, 1),
            ),
        ),
        disclosed=DisclosedClause(
            clause_id=clause.clause_id,
            corpus_manifest_id=corpus_manifest_id,
            document_id=clause.document_id,
            document_version=clause.document_version,
            content_hash=content_hash,
            section_number=clause.section_number,
        ),
    )


def build_evidence_set(
    clauses: Sequence[tuple[Clause, str]], *, corpus_manifest_id: str
) -> tuple[Evidence, ...]:
    """Build evidence for a ranked list, dropping nothing silently.

    A duplicate clause would be priced twice by the enforcer and would let one
    disclosure count against the caps as two, so it is an error rather than a
    thing to quietly collapse.
    """
    seen: set[str] = set()
    for clause, _ in clauses:
        if clause.clause_id in seen:
            raise ValueError("the same clause appears twice in one evidence set")
        seen.add(clause.clause_id)
    return tuple(
        build_evidence(clause, text, corpus_manifest_id=corpus_manifest_id)
        for clause, text in clauses
    )


__all__ = ["Evidence", "build_evidence", "build_evidence_set"]
