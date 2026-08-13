"""Behavioral checks for the local-only L2 evidence verifier."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from specpilot.answer.evidence import Evidence, build_evidence_from_unit
from specpilot.contracts.answer import Citation
from specpilot.contracts.egress import EvidenceExcerpt, NormalizedExcerptSpan
from specpilot.contracts.verdict import ComplianceCandidate
from specpilot.corpus.indexable import IndexUnit
from specpilot.retrieval.local import LocalCorpus
from specpilot.verifier.deterministic import DeterministicFault, verify_candidate

MANIFEST_ID = "c" * 64
EVIDENCE_ID = hashlib.sha256(b"A sender MUST emit the field.").hexdigest()


@pytest.fixture
def unit() -> IndexUnit:
    return IndexUnit(
        unit_id="a" * 64,
        kind="clause",
        document_id="RFC9110",
        document_version="RFC9110-2022",
        section_number="5.6",
        section_path="HTTP Semantics > 5.6",
        ordinal=17,
        text="A sender MUST emit the field.",
        indexed="5.6 HTTP Semantics\nA sender MUST emit the field.",
    )


@pytest.fixture
def corpus(unit: IndexUnit) -> LocalCorpus:
    return LocalCorpus(
        _units={unit.unit_id: unit},
        _toc=(),
        _source_hashes=((unit.document_id, "d" * 64),),
    )


@pytest.fixture
def evidence(unit: IndexUnit) -> Evidence:
    return build_evidence_from_unit(unit, corpus_manifest_id=MANIFEST_ID)


@pytest.fixture
def candidate() -> ComplianceCandidate:
    return ComplianceCandidate(
        claim="A sender always emits the field.",
        proposed_verdict="compliant",
        evidence_ids=(EVIDENCE_ID,),
        rationale="The local evidence is checked before semantic review.",
    )


def _candidate(*evidence_ids: str) -> ComplianceCandidate:
    return ComplianceCandidate(
        claim="A sender always emits the field.",
        proposed_verdict="compliant",
        evidence_ids=evidence_ids,
        rationale="The local evidence is checked before semantic review.",
    )


def _with_disclosed(evidence: Evidence, **changes: object) -> Evidence:
    """Corrupt local-only identity without weakening outbound validation."""
    return replace(evidence, disclosed=replace(evidence.disclosed, **changes))


def _with_excerpt(evidence: Evidence, **changes: object) -> Evidence:
    """Build a valid but locally inconsistent outbound excerpt."""
    fields = evidence.excerpt.model_dump()
    fields.update(changes)
    return replace(evidence, excerpt=EvidenceExcerpt(**fields))


def _verify(
    candidate: ComplianceCandidate,
    disclosed: tuple[Evidence, ...],
    corpus: LocalCorpus,
    *,
    allowed_document_ids: frozenset[str] = frozenset({"RFC9110"}),
):
    return verify_candidate(
        candidate,
        disclosed,
        corpus,
        corpus_manifest_id=MANIFEST_ID,
        allowed_document_ids=allowed_document_ids,
    )


def test_verifies_exact_frozen_unit_and_publishes_its_citation(
    candidate: ComplianceCandidate,
    evidence: Evidence,
    corpus: LocalCorpus,
    unit: IndexUnit,
) -> None:
    result = _verify(candidate, (evidence,), corpus)

    assert result.passed
    assert result.citations == (
        Citation(
            clause_id=unit.unit_id,
            corpus_manifest_id=MANIFEST_ID,
            document_id=unit.document_id,
            document_version=unit.document_version,
            section_number=unit.section_number,
            content_hash=hashlib.sha256(unit.text.encode()).hexdigest(),
        ),
    )


def test_rejects_a_full_hash_that_was_not_disclosed(
    evidence: Evidence, corpus: LocalCorpus
) -> None:
    result = _verify(_candidate("f" * 64), (evidence,), corpus)

    assert result.checks[0].fault is DeterministicFault.NOT_DISCLOSED
    assert result.citations == ()


def test_rejects_an_ambiguous_disclosure_handle(
    candidate: ComplianceCandidate, evidence: Evidence, corpus: LocalCorpus
) -> None:
    result = _verify(candidate, (evidence, evidence), corpus)

    assert result.checks[0].fault is DeterministicFault.AMBIGUOUS_EVIDENCE
    assert result.citations == ()


def test_rejects_a_self_consistent_outbound_quote_not_in_the_frozen_unit(
    candidate: ComplianceCandidate, evidence: Evidence, corpus: LocalCorpus
) -> None:
    quote = "A sender MAY omit the field."
    tampered = _with_excerpt(
        evidence,
        quote=quote,
        quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
    )

    result = _verify(candidate, (tampered,), corpus)

    assert result.checks[0].fault is DeterministicFault.QUOTE_HASH_MISMATCH
    assert result.citations == ()


def test_rejects_an_outbound_content_handle_not_bound_to_the_disclosure(
    evidence: Evidence, corpus: LocalCorpus
) -> None:
    outbound_handle = "b" * 64
    tampered = _with_excerpt(evidence, content_hash=outbound_handle)

    result = _verify(_candidate(outbound_handle), (tampered,), corpus)

    assert result.checks[0].fault is DeterministicFault.CONTENT_HASH_MISMATCH
    assert result.citations == ()


def test_rejects_an_outbound_span_not_bound_to_the_disclosure(
    candidate: ComplianceCandidate, evidence: Evidence, corpus: LocalCorpus
) -> None:
    tampered = _with_excerpt(
        evidence,
        span=NormalizedExcerptSpan(
            paragraph_start=18,
            paragraph_end=18,
            token_start=0,
            token_end=6,
        ),
    )

    result = _verify(candidate, (tampered,), corpus)

    assert result.checks[0].fault is DeterministicFault.SPAN_MISMATCH
    assert result.citations == ()


@pytest.mark.parametrize(
    ("changes", "allowed_document_ids", "fault"),
    [
        (
            {"corpus_manifest_id": "b" * 64},
            frozenset({"RFC9110"}),
            DeterministicFault.CORPUS_MANIFEST_MISMATCH,
        ),
        (
            {},
            frozenset({"RFC9112"}),
            DeterministicFault.DOCUMENT_SCOPE_MISMATCH,
        ),
        (
            {"clause_id": "b" * 64},
            frozenset({"RFC9110"}),
            DeterministicFault.CLAUSE_NOT_FOUND,
        ),
        (
            {"document_id": "RFC9112"},
            frozenset({"RFC9110", "RFC9112"}),
            DeterministicFault.DOCUMENT_ID_MISMATCH,
        ),
        (
            {"document_version": "RFC9110-2021"},
            frozenset({"RFC9110"}),
            DeterministicFault.DOCUMENT_VERSION_MISMATCH,
        ),
        (
            {"section_number": "5.7"},
            frozenset({"RFC9110"}),
            DeterministicFault.SECTION_MISMATCH,
        ),
        (
            {"content_hash": "b" * 64},
            frozenset({"RFC9110"}),
            DeterministicFault.CONTENT_HASH_MISMATCH,
        ),
        (
            {"quote_hash": "b" * 64},
            frozenset({"RFC9110"}),
            DeterministicFault.QUOTE_HASH_MISMATCH,
        ),
        (
            {
                "span": NormalizedExcerptSpan(
                    paragraph_start=18,
                    paragraph_end=18,
                    token_start=0,
                    token_end=6,
                )
            },
            frozenset({"RFC9110"}),
            DeterministicFault.SPAN_MISMATCH,
        ),
    ],
)
def test_rejects_one_mutated_disclosure_boundary(
    candidate: ComplianceCandidate,
    evidence: Evidence,
    corpus: LocalCorpus,
    changes: dict[str, object],
    allowed_document_ids: frozenset[str],
    fault: DeterministicFault,
) -> None:
    result = _verify(
        candidate,
        (_with_disclosed(evidence, **changes),),
        corpus,
        allowed_document_ids=allowed_document_ids,
    )

    assert result.checks[0].fault is fault
    assert result.citations == ()


def test_rejects_a_candidate_with_no_evidence_ids(
    corpus: LocalCorpus,
) -> None:
    candidate = ComplianceCandidate(
        claim="A sender always emits the field.",
        proposed_verdict="insufficient_evidence",
        rationale="No evidence was disclosed for semantic verification.",
    )
    result = _verify(candidate, (), corpus)

    assert len(result.checks) == 1
    assert result.checks[0].evidence_id is None
    assert result.checks[0].fault is DeterministicFault.NO_VERIFIED_EVIDENCE
    assert result.citations == ()
