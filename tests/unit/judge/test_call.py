"""Tests for the judge reply parser and request builder.

Written RED-first per the judge scoring plan. The parser tests pin the two
fault codes the calibration CLI branches on; the builder tests pin the stage
separation that keeps judge calls out of the evidence budget and the
default-deny refusal that keeps an unauthorized manifest from sending.
"""

from __future__ import annotations

import json

import pytest

from specpilot.contracts.egress import EgressStage, JudgePayload, TaskLevel
from specpilot.contracts.manifests import SourceManifestDraft
from specpilot.contracts.scoring import ClaimVerdict, JudgeOutput
from specpilot.judge.call import build_judge_request, parse_judge_reply
from tests.unit.egress.test_disclosure_caps import judge_route
from tests.unit.egress.test_policy_projection import authorized_manifest, fixture_store
from tests.unit.manifests.test_source_manifest import initial_fields

VALID_REPLY = {
    "schema_version": "judge-output/v1",
    "key_point_hits": [{"point_id": "p1", "hit": True, "miss_reason": None}],
    "answer_claims": [
        {
            "claim_id": "c1",
            "claim": "A claim.",
            "verdict": "supported",
            "severe": False,
        }
    ],
}


def _payload() -> JudgePayload:
    return JudgePayload(
        query="The question.",
        final_answer="The answer.",
        scoring_points=({"point_id": "p1", "text": "The point."},),
    )


def test_a_valid_reply_parses() -> None:
    output, fault = parse_judge_reply(json.dumps(VALID_REPLY))
    assert fault is None
    assert isinstance(output, JudgeOutput)
    assert output.key_point_hits[0].point_id == "p1"
    assert output.answer_claims[0].verdict is ClaimVerdict.SUPPORTED


def test_the_parser_accepts_the_names_the_prompt_prints() -> None:
    """The cross-join guard: build a reply from the prompt's printed skeleton.

    Reading the field names off the prompt text — not off the contract — is
    what catches the fault where the prompt asks for `key_points` and the
    parser expects `key_point_hits`, which a contract-to-contract test would
    miss the same way the first live call would.
    """
    from specpilot.judge.prompt import REPLY_SCHEMA

    skeleton = json.loads(REPLY_SCHEMA)
    points_key = next(iter(skeleton))
    claims_key = next(key for key in skeleton if key != points_key)
    reply = {
        "schema_version": "judge-output/v1",
        points_key: [{"point_id": "p1", "hit": True, "miss_reason": None}],
        claims_key: [
            {
                "claim_id": "c1",
                "claim": "A claim.",
                "verdict": "supported",
                "severe": False,
            }
        ],
    }
    output, fault = parse_judge_reply(json.dumps(reply))
    assert fault is None
    assert isinstance(output, JudgeOutput)


def test_unreadable_and_invalid_replies_carry_stable_faults() -> None:
    assert parse_judge_reply("not json") == (None, "judge_reply_unreadable")
    malformed = dict(VALID_REPLY)
    malformed["key_point_hits"] = [{"point_id": "p1", "hit": False}]
    assert parse_judge_reply(json.dumps(malformed)) == (None, "judge_reply_invalid")


def test_a_fenced_reply_is_tolerated_but_prose_is_not() -> None:
    fenced = "```json\n" + json.dumps(VALID_REPLY) + "\n```"
    output, fault = parse_judge_reply(fenced)
    assert fault is None
    assert isinstance(output, JudgeOutput)
    assert parse_judge_reply("Here is my assessment: " + json.dumps(VALID_REPLY)) == (
        None,
        "judge_reply_unreadable",
    )


def test_the_judge_request_is_staged_as_judge() -> None:
    request = build_judge_request(
        _payload(),
        source_manifest=authorized_manifest(route=judge_route()),
        corpus_manifest_id="b" * 64,
        model_id="glm-5.2",
        task_level=TaskLevel.L1,
        evaluation_root_id="case-1",
        run_id="judge-run",
    )
    assert request.stage is EgressStage.JUDGE
    assert request.payload.kind == "judge"
    assert request.route.use.value == "offline_judge"


def test_a_manifest_without_a_route_refuses_to_build() -> None:
    manifest = fixture_store().create_source(SourceManifestDraft(**initial_fields()))
    with pytest.raises(ValueError):
        build_judge_request(
            _payload(),
            source_manifest=manifest,
            corpus_manifest_id="b" * 64,
            model_id="glm-5.2",
            task_level=TaskLevel.L1,
            evaluation_root_id="case-1",
            run_id="judge-run",
        )
