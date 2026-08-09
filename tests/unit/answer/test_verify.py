"""The claim, and every way it is allowed to fail.

A citation checker that only catches invented clause ids is easy and nearly
useless: the interesting failure is a real clause the model never saw, which
checks out against the corpus and looks perfectly valid. These tests exist to
keep that case caught.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from specpilot.answer.verify import (
    CitationFault,
    ClaimedCitation,
    DisclosedClause,
    check_citation,
    verify_answer,
)
from specpilot.contracts.answer import (
    AnswerVerdict,
    Citation,
    RefusalReason,
    VerifiedAnswer,
)

CORPUS = "c" * 64
OTHER_CORPUS = "d" * 64


def disclosed(clause_id: str = "a" * 64, **overrides: object) -> DisclosedClause:
    fields: dict[str, object] = {
        "clause_id": clause_id,
        "corpus_manifest_id": CORPUS,
        "document_id": "ietf-rfc-9110",
        "document_version": "2022-06",
        "content_hash": "e" * 64,
        "section_number": "15.5.6",
        **overrides,
    }
    return DisclosedClause(**fields)  # type: ignore[arg-type]


def answered(**overrides: object) -> VerifiedAnswer:
    return verify_answer(
        **{  # type: ignore[arg-type]
            "answer_text": "The origin server must send an Allow header field.",
            "claimed_citations": [ClaimedCitation("a" * 64)],
            "disclosed": [disclosed()],
            "corpus_manifest_id": CORPUS,
            **overrides,
        }
    )


def test_a_cited_clause_that_was_disclosed_verifies() -> None:
    result = answered()

    assert result.verdict is AnswerVerdict.ANSWERED
    assert [c.clause_id for c in result.citations] == ["a" * 64]
    assert result.citations[0].section_number == "15.5.6"
    assert result.citations[0].content_hash == "e" * 64


def test_a_clause_the_model_never_saw_is_not_evidence() -> None:
    """The interesting failure. A real clause cited by a model that was never
    shown it checks out against the corpus and is still not support: whatever
    the model said about it came from training, not from the source."""
    result = answered(claimed_citations=[ClaimedCitation("b" * 64)])

    assert result.verdict is AnswerVerdict.REFUSED
    assert result.refusal_reason is RefusalReason.UNVERIFIABLE_CITATION
    assert result.citation_faults == (f"{'b' * 16}:unknown_clause",)


def test_one_bad_citation_sinks_the_whole_answer() -> None:
    """Keeping the good ones would publish a conclusion partly reasoned from
    outside the corpus, with nothing in the output saying so."""
    result = answered(
        claimed_citations=[ClaimedCitation("a" * 64), ClaimedCitation("b" * 64)]
    )

    assert result.verdict is AnswerVerdict.REFUSED
    assert result.citations == ()


def test_content_drift_is_caught_when_the_model_echoes_a_hash() -> None:
    result = answered(
        claimed_citations=[ClaimedCitation("a" * 64, content_hash="f" * 64)]
    )

    assert result.verdict is AnswerVerdict.REFUSED
    assert result.citation_faults == (f"{'a' * 16}:content_drift",)


def test_a_citation_from_another_frozen_corpus_fails_closed() -> None:
    """§6.4: evidence and run carry one corpus_manifest_id."""
    check = check_citation(
        ClaimedCitation("a" * 64),
        {"a" * 64: disclosed(corpus_manifest_id=OTHER_CORPUS)},
        corpus_manifest_id=CORPUS,
    )

    assert check.fault is CitationFault.CROSS_MANIFEST
    assert check.verified is False


def test_an_answer_with_no_citation_at_all_is_refused() -> None:
    result = answered(claimed_citations=[])

    assert result.verdict is AnswerVerdict.REFUSED
    assert result.citation_faults == ("no_citation",)


def test_retrieving_nothing_and_declining_are_different_refusals() -> None:
    """Folded into one number they would hide whether retrieval or reasoning
    is at fault."""
    nothing_found = answered(disclosed=[])
    model_declined = answered(model_refused=True)

    assert nothing_found.refusal_reason is RefusalReason.NO_EVIDENCE_RETRIEVED
    assert model_declined.refusal_reason is RefusalReason.EVIDENCE_INSUFFICIENT


def test_no_evidence_outranks_the_models_own_refusal() -> None:
    """Nothing was sent, so the model had nothing to decline."""
    result = answered(disclosed=[], model_refused=True)

    assert result.refusal_reason is RefusalReason.NO_EVIDENCE_RETRIEVED


def test_repeating_a_clause_is_prose_not_a_fault() -> None:
    """But it must not let one verified clause count as two."""
    result = answered(
        claimed_citations=[ClaimedCitation("a" * 64), ClaimedCitation("a" * 64)]
    )

    assert result.verdict is AnswerVerdict.ANSWERED
    assert len(result.citations) == 1


def citation(**overrides: object) -> Citation:
    fields: dict[str, object] = {
        "clause_id": "a" * 64,
        "corpus_manifest_id": CORPUS,
        "document_id": "ietf-rfc-9110",
        "document_version": "2022-06",
        "content_hash": "e" * 64,
        **overrides,
    }
    return Citation(**fields)  # type: ignore[arg-type]


def test_an_answered_verdict_without_a_citation_cannot_be_constructed() -> None:
    """The forbidden state is unwriteable, not merely unproduced."""
    with pytest.raises(ValidationError):
        VerifiedAnswer(verdict="answered", answer="It must send Allow.")
    with pytest.raises(ValidationError):
        VerifiedAnswer(verdict="answered", citations=(citation(),))


def test_a_refusal_may_not_carry_an_answer_or_citations() -> None:
    """A refusal that cites is claiming support for a conclusion it declined."""
    with pytest.raises(ValidationError):
        VerifiedAnswer(
            verdict="refused",
            refusal_reason="evidence_insufficient",
            answer="It must send Allow.",
        )
    with pytest.raises(ValidationError):
        VerifiedAnswer(
            verdict="refused",
            refusal_reason="evidence_insufficient",
            citations=(citation(),),
        )


def test_a_refusal_must_say_why() -> None:
    with pytest.raises(ValidationError):
        VerifiedAnswer(verdict="refused")


def test_citations_may_not_cross_corpus_manifests() -> None:
    with pytest.raises(ValidationError):
        VerifiedAnswer(
            verdict="answered",
            answer="Both apply.",
            citations=(
                citation(),
                citation(clause_id="b" * 64, corpus_manifest_id=OTHER_CORPUS),
            ),
        )


def test_a_clause_cannot_be_cited_twice_in_one_answer() -> None:
    with pytest.raises(ValidationError):
        VerifiedAnswer(
            verdict="answered", answer="Twice.", citations=(citation(), citation())
        )


def test_a_citation_carries_everything_needed_to_look_it_up() -> None:
    """Without the manifest pair it names a clause in no particular version."""
    assert set(citation().model_dump()) == {
        "clause_id",
        "corpus_manifest_id",
        "document_id",
        "document_version",
        "section_number",
        "content_hash",
    }
