"""Tests for the versioned judge prompt.

Written RED-first per the judge scoring plan; the module under test did not
exist when these were written. The assertions pin the two coupling points the
project has been bitten by: the rendered text must name exactly the
identifiers the payload prints, and the prompt body must print the exact reply
schema the parser accepts.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import JudgePayload, ScoringPoint
from specpilot.judge.prompt import (
    JUDGE_PROMPT_V1_BODY,
    JudgePrompt,
    prompt_identity,
    render_judge_prompt,
)

PROMPT = JudgePrompt(
    identifier="judge-answer-scorer",
    version="1",
    body=JUDGE_PROMPT_V1_BODY,
)


def _payload() -> JudgePayload:
    return JudgePayload(
        query="What must an origin server send with a 405 response?",
        final_answer="The origin server sends an Allow header field.",
        scoring_points=(
            ScoringPoint(point_id="p1", text="Names the Allow requirement"),
            ScoringPoint(point_id="p2", text="Names the 405 status"),
        ),
        gold_excerpts=(),
    )


def test_the_prompt_identity_changes_only_with_its_body() -> None:
    identity = prompt_identity(PROMPT)
    assert identity.identifier == "judge-answer-scorer"
    assert identity.version == "1"
    assert len(identity.content_sha256) == 64

    changed = JudgePrompt(
        identifier="judge-answer-scorer", version="1", body="Reworded."
    )
    assert prompt_identity(changed).content_sha256 != identity.content_sha256

    renamed = JudgePrompt(
        identifier="judge-answer-scorer",
        version="2",
        body=JUDGE_PROMPT_V1_BODY,
    )
    assert prompt_identity(renamed).content_sha256 != identity.content_sha256


def test_rendering_names_every_input_the_payload_prints() -> None:
    rendered = render_judge_prompt(PROMPT, _payload())
    assert "What must an origin server send with a 405 response?" in rendered
    assert "The origin server sends an Allow header field." in rendered
    assert "p1" in rendered and "p2" in rendered
    assert "Names the Allow requirement" in rendered
    assert "Names the 405 status" in rendered


def test_rendering_names_excerpts_by_content_hash() -> None:
    import hashlib

    from specpilot.contracts.egress import EvidenceExcerpt

    quote = "The Allow field names the methods."
    excerpt = EvidenceExcerpt(
        corpus_manifest_id="a" * 64,
        content_hash="e" * 64,
        quote=quote,
        quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        span={
            "paragraph_start": 0,
            "paragraph_end": 1,
            "token_start": 0,
            "token_end": 5,
        },
    )
    payload = _payload().model_copy(update={"gold_excerpts": (excerpt,)})
    rendered = render_judge_prompt(PROMPT, payload)
    assert "Evidence " + "e" * 64 in rendered
    assert quote in rendered


def test_the_body_prints_the_schema_the_parser_accepts() -> None:
    body = PROMPT.body
    for token in (
        '"key_points"',
        '"point_id"',
        '"hit"',
        '"miss_reason"',
        '"answer_claims"',
        '"claim_id"',
        '"claim"',
        '"verdict"',
        '"severe"',
        '"supported"',
    ):
        assert token in body
    with pytest.raises(ValidationError):
        JudgePrompt(identifier="x", version="1")  # type: ignore[call-arg]
