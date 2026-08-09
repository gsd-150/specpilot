"""Reading an untrusted reply, and building the evidence a citation is checked against.

The reply is generated text arriving from outside; every field in it is a claim.
These tests pin that the parser decides shape and nothing else, and that an
unparseable reply becomes a refusal rather than an exception.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from specpilot.answer.evidence import build_evidence, build_evidence_set
from specpilot.answer.reply import parse_reply
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, build_clauses, iter_clause_texts
from tests.helpers import rfc_factory

CORPUS = "c" * 64
CLAUSE = "a" * 64


def reply(**body: object) -> str:
    return json.dumps(body)


def test_a_sufficient_reply_yields_its_answer_and_citations() -> None:
    parsed = parse_reply(
        reply(sufficient=True, answer="It must send Allow.", citations=[CLAUSE])
    )

    assert parsed.usable
    assert parsed.sufficient is True
    assert parsed.answer == "It must send Allow."
    assert [c.clause_id for c in parsed.citations] == [CLAUSE]


def test_an_insufficient_reply_carries_nothing_else() -> None:
    parsed = parse_reply(reply(sufficient=False, answer=None, citations=[]))

    assert parsed.usable
    assert parsed.sufficient is False
    assert parsed.answer is None
    assert parsed.citations == ()


def test_a_fenced_code_block_is_a_formatting_habit_not_a_failure() -> None:
    parsed = parse_reply(
        f'```json\n{{"sufficient": true, "answer": "Yes.", '
        f'"citations": ["{CLAUSE}"]}}\n```'
    )

    assert parsed.usable
    assert parsed.answer == "Yes."


@pytest.mark.parametrize(
    ("content", "fault"),
    [
        ("The server must send an Allow header.", "reply_not_json"),
        ("[1, 2, 3]", "reply_not_an_object"),
        ('{"answer": "Yes.", "citations": []}', "reply_missing_sufficient"),
        ('{"sufficient": true, "citations": []}', "reply_missing_answer"),
        (
            '{"sufficient": true, "answer": "  ", "citations": []}',
            "reply_missing_answer",
        ),
        ('{"sufficient": true, "answer": "Yes."}', "reply_missing_citations"),
    ],
)
def test_an_unreadable_reply_is_a_fault_not_an_exception(
    content: str, fault: str
) -> None:
    parsed = parse_reply(content)

    assert parsed.usable is False
    assert parsed.parse_fault == fault
    assert parsed.sufficient is False


def test_a_missing_verdict_is_not_defaulted_to_refusal() -> None:
    """Guessing the safe value would hide a model that ignored the contract
    behind an ordinary-looking refusal."""
    parsed = parse_reply('{"answer": "Yes.", "citations": []}')

    assert parsed.parse_fault == "reply_missing_sufficient"


def test_a_section_number_where_a_clause_id_belongs_is_its_own_fault() -> None:
    """Not passed through as an unknown clause: "returned the wrong kind of
    identifier" and "invented an identifier" are different failures."""
    parsed = parse_reply(reply(sufficient=True, answer="Yes.", citations=["15.5.6"]))

    assert parsed.parse_fault == "reply_citation_malformed"


def test_an_oversized_reply_is_refused_before_parsing() -> None:
    parsed = parse_reply("x" * (32 * 1024 + 1))

    assert parsed.parse_fault == "reply_too_large"


def test_too_many_citations_is_refused() -> None:
    parsed = parse_reply(
        reply(sufficient=True, answer="Yes.", citations=[CLAUSE] * 33)
    )

    assert parsed.parse_fault == "reply_too_many_citations"


@pytest.fixture
def clause_with_text(tmp_path: Path) -> tuple[object, str]:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    path = rfc_factory.write_safe(directory)
    return next(iter(iter_clause_texts(path, RfcLimits(), ClauseLimits())))


def test_evidence_carries_the_quote_outward_and_the_locator_inward(
    clause_with_text: tuple[object, str],
) -> None:
    """Two objects from one clause, and only one of them leaves the machine."""
    clause, text = clause_with_text

    evidence = build_evidence(clause, text, corpus_manifest_id=CORPUS)  # type: ignore[arg-type]

    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert evidence.excerpt.quote == text
    assert evidence.excerpt.quote_hash == expected
    assert evidence.excerpt.corpus_manifest_id == CORPUS
    assert evidence.disclosed.content_hash == expected
    assert evidence.disclosed.clause_id == clause.clause_id  # type: ignore[attr-defined]
    # The locator half holds no text at all, which is what makes it safe to
    # keep beside a record.
    stored = [
        getattr(evidence.disclosed, field.name)
        for field in dataclasses.fields(evidence.disclosed)
    ]
    assert not any(
        isinstance(value, str) and text[:20] in value for value in stored
    )


def test_the_span_covers_the_whole_clause(
    clause_with_text: tuple[object, str],
) -> None:
    """A clause is already the unit the source numbered. Slicing one would cite
    text a reader cannot find by the anchor the citation names."""
    clause, text = clause_with_text

    span = build_evidence(clause, text, corpus_manifest_id=CORPUS).excerpt.span  # type: ignore[arg-type]

    assert span.paragraph_start == span.paragraph_end == clause.ordinal  # type: ignore[attr-defined]
    assert span.token_start == 0
    assert span.token_end == clause.word_count  # type: ignore[attr-defined]


def test_the_same_clause_twice_in_one_evidence_set_is_refused(
    tmp_path: Path,
) -> None:
    """It would be priced twice by the enforcer and count as two disclosures."""
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    path = rfc_factory.write_safe(directory)
    pairs = list(iter_clause_texts(path, RfcLimits(), ClauseLimits()))

    with pytest.raises(ValueError, match="twice"):
        build_evidence_set([pairs[0], pairs[0]], corpus_manifest_id=CORPUS)


def test_an_empty_excerpt_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    clause = build_clauses(
        rfc_factory.write_safe(directory), RfcLimits(), ClauseLimits()
    )[0]

    with pytest.raises(ValueError, match="text"):
        build_evidence(clause, "", corpus_manifest_id=CORPUS)
