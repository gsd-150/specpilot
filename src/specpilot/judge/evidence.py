"""The sanitized dev scoring evidence for the evaluation freeze.

The freeze gate reads a `dev-scoring-status` whose `evidence_sha256` names this
file's bytes, so the file is what the author reviews before confirming a
freeze. It is deliberately prose-free: the calibration report is embedded in
full (counts, rates, kappa, confusion — numbers only), and every case is
represented by content hashes. The freeze reader recursively rejects the keys
`question`, `claim`, `excerpt`, `answer`, and `rationale` in its status
schemas; this evidence carries none of them, so the same bytes can travel
anywhere a status can.

The builder refuses duplicate or malformed hashes — a freeze must never pin a
record list whose own identity is ambiguous.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from specpilot.contracts.manifests import Identifier, Sha256
from specpilot.judge.calibration import CalibrationReport
from specpilot.judge.prompt import JudgePrompt, JudgePromptIdentity
from specpilot.manifests.canonical import canonical_json


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ScoringEvidence(_FrozenModel):
    """Everything a reviewer needs to re-derive the dev calibration.

    The judge records and human labels are named by content hash; the report is
    inlined so a changed record necessarily changes this evidence's bytes.
    """

    schema_version: Literal["judge-calibration-evidence/v1"] = (
        "judge-calibration-evidence/v1"
    )
    selected_route: Identifier
    split: Literal["dev"] = "dev"
    prompt: JudgePromptIdentity
    model_id: Identifier
    calibration_report: CalibrationReport
    judge_record_sha256s: tuple[Sha256, ...]
    human_label_sha256s: tuple[Sha256, ...]


def build_scoring_evidence(
    *,
    route_id: Identifier,
    prompt: JudgePrompt,
    model_id: Identifier,
    report: CalibrationReport,
    judge_record_sha256s: tuple[Sha256, ...],
    human_label_sha256s: tuple[Sha256, ...],
) -> bytes:
    """Serialize the sanitized evidence bytes for one calibration pass.

    ``judge_record_sha256s`` and ``human_label_sha256s`` must be the content
    hashes the stores published; the caller verifies each hash still resolves
    before calling this, so the evidence can never name a record that no
    longer parses.
    """
    if len(set(judge_record_sha256s)) != len(judge_record_sha256s):
        raise ValueError("duplicate judge record hashes")
    if len(set(human_label_sha256s)) != len(human_label_sha256s):
        raise ValueError("duplicate human label hashes")
    evidence = ScoringEvidence(
        selected_route=route_id,
        prompt=JudgePromptIdentity(
            identifier=prompt.identifier,
            version=prompt.version,
            content_sha256=prompt.content_sha256,
        ),
        model_id=model_id,
        calibration_report=report,
        judge_record_sha256s=judge_record_sha256s,
        human_label_sha256s=human_label_sha256s,
    )
    return canonical_json(evidence)
