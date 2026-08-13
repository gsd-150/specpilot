"""Fail-closed L2 verification before any semantic model can be consulted.

The Compliance model names only an evidence handle.  This module turns that
untrusted handle back into the exact local disclosure and then re-resolves its
clause in the frozen corpus.  It intentionally knows nothing about claim
meaning, providers, or persistence: each check is a deterministic identity
comparison over local data.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from specpilot.answer.evidence import Evidence
from specpilot.contracts.answer import Citation
from specpilot.contracts.egress import NormalizedExcerptSpan
from specpilot.contracts.verdict import ComplianceCandidate
from specpilot.corpus.indexable import IndexUnit
from specpilot.retrieval.local import LocalCorpus


class DeterministicFault(StrEnum):
    NOT_DISCLOSED = "not_disclosed"
    AMBIGUOUS_EVIDENCE = "ambiguous_evidence"
    CORPUS_MANIFEST_MISMATCH = "corpus_manifest_mismatch"
    DOCUMENT_SCOPE_MISMATCH = "document_scope_mismatch"
    CLAUSE_NOT_FOUND = "clause_not_found"
    DOCUMENT_ID_MISMATCH = "document_id_mismatch"
    DOCUMENT_VERSION_MISMATCH = "document_version_mismatch"
    SECTION_MISMATCH = "section_mismatch"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    QUOTE_HASH_MISMATCH = "quote_hash_mismatch"
    SPAN_MISMATCH = "span_mismatch"
    NO_VERIFIED_EVIDENCE = "no_verified_evidence"


@dataclass(frozen=True, slots=True)
class DeterministicCheck:
    evidence_id: str | None
    fault: DeterministicFault | None


@dataclass(frozen=True, slots=True)
class DeterministicResult:
    checks: tuple[DeterministicCheck, ...]
    citations: tuple[Citation, ...]

    @property
    def passed(self) -> bool:
        return bool(self.citations) and all(
            check.fault is None for check in self.checks
        )


def verify_candidate(
    candidate: ComplianceCandidate,
    disclosed: Iterable[Evidence],
    corpus: LocalCorpus,
    *,
    corpus_manifest_id: str,
    allowed_document_ids: frozenset[str],
) -> DeterministicResult:
    """Verify every candidate handle against exactly what this run disclosed.

    A duplicate content hash is ambiguous on the wire.  Keep all records until
    that ambiguity is reported, rather than letting a dictionary overwrite one
    and choose a clause based on construction order.
    """
    by_evidence_id: dict[str, list[Evidence]] = defaultdict(list)
    for item in disclosed:
        by_evidence_id[item.excerpt.content_hash].append(item)

    checks: list[DeterministicCheck] = []
    citations: list[Citation] = []
    for evidence_id in candidate.evidence_ids:
        matches = by_evidence_id.get(evidence_id, [])
        if not matches:
            checks.append(
                DeterministicCheck(evidence_id, DeterministicFault.NOT_DISCLOSED)
            )
            continue
        if len(matches) != 1:
            checks.append(
                DeterministicCheck(evidence_id, DeterministicFault.AMBIGUOUS_EVIDENCE)
            )
            continue

        evidence = matches[0]
        fault, unit = _verify_disclosure(
            evidence,
            corpus,
            corpus_manifest_id=corpus_manifest_id,
            allowed_document_ids=allowed_document_ids,
        )
        checks.append(DeterministicCheck(evidence_id, fault))
        if fault is None:
            assert unit is not None
            citations.append(_citation(unit, corpus_manifest_id))

    if not checks:
        checks.append(DeterministicCheck(None, DeterministicFault.NO_VERIFIED_EVIDENCE))

    if any(check.fault is not None for check in checks):
        citations = []
    return DeterministicResult(tuple(checks), tuple(citations))


def _verify_disclosure(
    evidence: Evidence,
    corpus: LocalCorpus,
    *,
    corpus_manifest_id: str,
    allowed_document_ids: frozenset[str],
) -> tuple[DeterministicFault | None, IndexUnit | None]:
    disclosed = evidence.disclosed
    excerpt = evidence.excerpt
    if disclosed.corpus_manifest_id != corpus_manifest_id:
        return DeterministicFault.CORPUS_MANIFEST_MISMATCH, None
    if disclosed.document_id not in allowed_document_ids:
        return DeterministicFault.DOCUMENT_SCOPE_MISMATCH, None

    unit = corpus.resolve(disclosed.clause_id)
    if unit is None:
        return DeterministicFault.CLAUSE_NOT_FOUND, None
    if disclosed.document_id != unit.document_id:
        return DeterministicFault.DOCUMENT_ID_MISMATCH, None
    if disclosed.document_version != unit.document_version:
        return DeterministicFault.DOCUMENT_VERSION_MISMATCH, None
    if disclosed.section_number != unit.section_number:
        return DeterministicFault.SECTION_MISMATCH, None

    text_hash = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
    if (
        excerpt.content_hash != disclosed.content_hash
        or excerpt.content_hash != text_hash
    ):
        return DeterministicFault.CONTENT_HASH_MISMATCH, None
    excerpt_quote_hash = hashlib.sha256(excerpt.quote.encode("utf-8")).hexdigest()
    if (
        excerpt.quote != unit.text
        or excerpt.quote_hash != excerpt_quote_hash
        or excerpt.quote_hash != disclosed.quote_hash
        or excerpt.quote_hash != text_hash
    ):
        return DeterministicFault.QUOTE_HASH_MISMATCH, None
    if excerpt.span != disclosed.span or excerpt.span != _whole_unit_span(unit):
        return DeterministicFault.SPAN_MISMATCH, None
    return None, unit


def _whole_unit_span(unit: IndexUnit) -> NormalizedExcerptSpan:
    return NormalizedExcerptSpan(
        paragraph_start=unit.ordinal,
        paragraph_end=unit.ordinal,
        token_start=0,
        token_end=max(len(unit.text.split()), 1),
    )


def _citation(unit: IndexUnit, corpus_manifest_id: str) -> Citation:
    return Citation(
        clause_id=unit.unit_id,
        corpus_manifest_id=corpus_manifest_id,
        document_id=unit.document_id,
        document_version=unit.document_version,
        section_number=unit.section_number,
        content_hash=hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "DeterministicCheck",
    "DeterministicFault",
    "DeterministicResult",
    "verify_candidate",
]
