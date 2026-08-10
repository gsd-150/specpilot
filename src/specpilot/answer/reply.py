"""Read the model's reply without trusting any of it.

The reply is the least trustworthy input in the system: it is generated text
arriving from outside, and every field in it is a claim. So this parses shape
only. It decides nothing about whether a cited clause is real, whether the
answer is supported, or whether the refusal is honest — `verify_answer` does
that against what was actually disclosed.

An unparseable reply is a refusal, not an error. A model that returns prose
where the contract asked for an object has failed to produce a checkable
answer, and the honest outcome of "I cannot tell what it cited" is declining.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from specpilot.answer.verify import ClaimedCitation
from specpilot.contracts.answer import REPLY_INSTRUCTIONS

# Bounded because the reply is untrusted input and every one of these is copied
# into a record or compared against a store. A model that needs more than this
# is not producing a citation list.
_MAX_REPLY_BYTES = 32 * 1024
_MAX_CITATIONS = 32
_MAX_ANSWER_CHARS = 4_096
_SHA256_LENGTH = 64




@dataclass(frozen=True, slots=True)
class ParsedReply:
    sufficient: bool
    answer: str | None
    citations: tuple[ClaimedCitation, ...]
    parse_fault: str | None = None

    @property
    def usable(self) -> bool:
        return self.parse_fault is None


def _unparseable(fault: str) -> ParsedReply:
    return ParsedReply(
        sufficient=False, answer=None, citations=(), parse_fault=fault
    )


def parse_reply(content: str) -> ParsedReply:
    """Read shape only. Every claim in here is checked somewhere else."""
    if len(content.encode("utf-8")) > _MAX_REPLY_BYTES:
        return _unparseable("reply_too_large")

    body = _extract_object(content)
    if body is None:
        return _unparseable("reply_not_json")
    if not isinstance(body, dict):
        return _unparseable("reply_not_an_object")

    sufficient = body.get("sufficient")
    if not isinstance(sufficient, bool):
        # Not defaulted to false. A missing verdict means the model did not
        # answer the question that was asked of it, and guessing the safe value
        # would hide that behind an ordinary-looking refusal.
        return _unparseable("reply_missing_sufficient")

    if not sufficient:
        return ParsedReply(sufficient=False, answer=None, citations=())

    answer = body.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return _unparseable("reply_missing_answer")
    if len(answer) > _MAX_ANSWER_CHARS:
        return _unparseable("reply_answer_too_long")

    raw = body.get("citations")
    if not isinstance(raw, list):
        return _unparseable("reply_missing_citations")
    if len(raw) > _MAX_CITATIONS:
        return _unparseable("reply_too_many_citations")

    citations: list[ClaimedCitation] = []
    for entry in raw:
        clause_id = entry.get("clause_id") if isinstance(entry, dict) else entry
        if not isinstance(clause_id, str):
            return _unparseable("reply_citation_not_a_string")
        clause_id = clause_id.strip().lower()
        if len(clause_id) != _SHA256_LENGTH or not _is_hex(clause_id):
            # Rejected here rather than passed through as an unknown clause.
            # "The model returned a section number instead of a clause id" and
            # "the model invented a clause id" are different faults and the
            # second should not absorb the first.
            return _unparseable("reply_citation_malformed")
        citations.append(ClaimedCitation(clause_id))

    return ParsedReply(
        sufficient=True, answer=answer.strip(), citations=tuple(citations)
    )


def _is_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)


def _extract_object(content: str) -> Any:
    """Parse the reply, tolerating a fenced code block around it.

    Tolerating the fence is not tolerating prose: models wrap JSON in ```json
    routinely and that is a formatting habit, not a failure to answer. Anything
    looser than that stays a parse fault.
    """
    text = content.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.rpartition("```")[0].strip() or rest.strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


__all__ = ["REPLY_INSTRUCTIONS", "ParsedReply", "parse_reply"]
