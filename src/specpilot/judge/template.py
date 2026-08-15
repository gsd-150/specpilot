"""The author's human-label sheet for one scored dev case.

An authoring aid, not a record: it shows the texts the author must read to
label — the question, the final answer, the gold key points, and the
judge-extracted claims — without carrying the judge's own labels, so the
author's hit/miss and triage decisions cannot be primed by what the judge
said. It lives only in restricted output directories, never in git.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from specpilot.contracts.annotation import KeyPoint, QuestionText
from specpilot.contracts.answer import AnswerText
from specpilot.contracts.manifests import Identifier
from specpilot.contracts.scoring import ClaimText


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnswersFile(_FrozenModel):
    """The author's dev-run answer artifact: one final answer per case file."""

    answer: AnswerText


def read_answer_file(path: Path) -> AnswerText | None:
    """Read a `<case_id>.json` answer file, failing closed to `None`."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return AnswersFile.model_validate(payload).answer
    except ValidationError:
        return None


class TemplateClaim(_FrozenModel):
    """One judge-extracted answer claim, for the author to classify."""

    claim_id: Identifier
    claim: ClaimText


class LabelTemplate(_FrozenModel):
    schema_version: Literal["judge-label-template/v1"] = "judge-label-template/v1"
    case_id: Identifier
    question: QuestionText
    final_answer: AnswerText
    key_points: tuple[KeyPoint, ...]
    claims: tuple[TemplateClaim, ...]


def render_template(template: LabelTemplate) -> bytes:
    """Indented JSON for the author to read; not a hashed record."""
    return (
        json.dumps(
            template.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
