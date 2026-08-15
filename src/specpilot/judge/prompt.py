"""The versioned judge prompt — the §8.3 answer scorer's instructions.

The judge must stay independent of the Verifier: its own prompt module, its own
call path, and its own reply schema. Sharing either would make the scorer
inherit the Verifier's blind spots, which is the exact circularity §8.3 warns
about (a scoring judge and a Verifier built from the same judgement cannot
check each other).

The prompt is versioned because §8.3.2 keeps every old prompt and its numbers:
a changed body is a new identity, and the identity hash is what a
`JudgeRecord` pins, so a record can always be re-read against the prompt it
was scored under.

The reply schema the prompt prints is the exact schema the parser accepts —
the same coupling `REPLY_INSTRUCTIONS` guards for the L1 reply path: the
identifiers the model is told to echo (`point_id`, `claim_id`) are the ones
the payload actually prints, and nothing else is named.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from specpilot.contracts.egress import JudgePayload
from specpilot.contracts.manifests import Identifier

# The reply schema, kept beside the parser it must match. The parser is in
# specpilot.judge.call, which cannot be imported here without inverting the
# layering (the prompt module must stay importable by the transport without
# pulling in the call stack), so the two are only correct together and the
# prompt body pins the contract the parser enforces.
REPLY_SCHEMA = """\
{
  "key_point_hits": [
    {"point_id": "...", "hit": true, "miss_reason": null}
  ],
  "answer_claims": [
    {"claim_id": "c1", "claim": "...", "verdict": "supported", "severe": false}
  ]
}"""

JUDGE_PROMPT_V1_BODY = f"""\
You are the answer scorer for a specification question-answering system. Judge \
only the final answer shown below. You are given: the question, the final \
answer, a list of gold scoring points, and the gold evidence excerpts that \
settle the question.

Rules:
- Judge each scoring point independently: "hit" is true when the answer \
states or directly implies what the point requires, judged against the \
question and the gold excerpts; "hit" is false otherwise, and then \
"miss_reason" must name what is missing in one short sentence.
- Extract each factual or normative statement the answer makes into an \
"answer_claim" and classify it: "supported" (the gold excerpts support it), \
"contradicted" (the gold excerpts contradict it), or "insufficient" (the \
excerpts neither support nor contradict it). Set "severe" to true when the \
claim states a requirement that does not exist in the excerpts.
- Ignore how the answer is worded; judge what it asserts.
- Do not answer the question yourself, and do not use any knowledge outside \
the excerpts shown here.

Reply with one JSON object and nothing else, exactly in this schema:
{REPLY_SCHEMA}"""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class JudgePrompt(_FrozenModel):
    """One versioned prompt. The identity hash binds identifier, version, body."""

    identifier: Identifier
    version: Identifier
    body: str

    @property
    def content_sha256(self) -> str:
        return _prompt_sha256(self.identifier, self.version, self.body)


def _prompt_sha256(identifier: str, version: str, body: str) -> str:
    canonical = json.dumps(
        {"identifier": identifier, "version": version, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JudgePromptIdentity(_FrozenModel):
    """The identity a `JudgeRecord` pins, stable across renderings."""

    identifier: Identifier
    version: Identifier
    content_sha256: str


def prompt_identity(prompt: JudgePrompt) -> JudgePromptIdentity:
    return JudgePromptIdentity(
        identifier=prompt.identifier,
        version=prompt.version,
        content_sha256=prompt.content_sha256,
    )


def render_judge_prompt(prompt: JudgePrompt, payload: JudgePayload) -> str:
    """Render the full judge message for one payload.

    Every excerpt is introduced by the exact content hash the enforcer
    disclosed, mirroring the L1 payload's identifier discipline: the model can
    only refer to what it was shown, and what it was shown is named by bytes,
    not by a locator it could invent.
    """
    point_lines = [
        f"- {point.point_id}: {point.text}" for point in payload.scoring_points
    ]
    excerpt_lines = [
        f"Evidence {excerpt.content_hash}:\n{excerpt.quote}"
        for excerpt in payload.gold_excerpts
    ]
    parts = [
        prompt.body,
        "",
        f"Question: {payload.query}",
        "",
        f"Final answer: {payload.final_answer}",
        "",
        "Gold scoring points:",
        *point_lines,
        "",
        "Gold evidence excerpts:",
        *(excerpt_lines or ["(none)"]),
    ]
    return "\n".join(parts)
