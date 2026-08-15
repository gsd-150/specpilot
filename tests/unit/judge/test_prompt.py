"""Tests for the versioned judge prompt and its shipped rendering.

Written RED-first per the judge scoring plan, then extended after the first
live call: the prompt body was versioned and hashed but never wired into the
shipped renderer, so the model was asked to score without ever being shown the
reply contract and answered in prose. The tests below pin the wiring — the
renderer that actually builds the wire bytes must print this prompt body and
must show the judge the question.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import JudgePayload, ScoringPoint
from specpilot.judge.prompt import (
    JUDGE_PROMPT_V1_BODY,
    REPLY_SCHEMA,
    JudgePrompt,
    prompt_identity,
)
from specpilot.providers.http import _render_messages

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


def test_the_shipped_renderer_sends_the_prompt_body_as_the_system_message() -> None:
    """The wiring guard that the first live call should have had.

    If the system message stops carrying this body, the model is scoring
    without the reply contract — which is exactly how the first live judge
    call came back as prose.
    """
    messages = _render_messages(_payload())
    assert messages[0] == {
        "role": "system",
        "content": JUDGE_PROMPT_V1_BODY,
    }


def test_the_shipped_renderer_shows_the_question_and_every_input() -> None:
    user = _render_messages(_payload())[1]["content"]
    assert "Question: What must an origin server send with a 405 response?" in user
    assert "Final answer under review: The origin server sends an Allow" in user
    assert "Scoring point p1: Names the Allow requirement" in user
    assert "Scoring point p2: Names the 405 status" in user


def test_the_shipped_renderer_names_excerpts_by_full_content_hash() -> None:
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
    user = _render_messages(payload)[1]["content"]
    assert "Evidence " + "e" * 64 in user
    assert quote in user


def test_the_body_prints_the_schema_the_parser_accepts() -> None:
    body = PROMPT.body
    for token in (
        '"key_point_hits"',
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


def test_the_schema_skeleton_is_valid_json() -> None:
    skeleton = json.loads(REPLY_SCHEMA)
    assert set(skeleton) == {"key_point_hits", "answer_claims"}
