"""Closed, prose-free contracts for immutable evaluation run specifications.

The run spec binds identities and counts, never evaluation cases or outputs.
Its schema is deliberately closed: successors require a versioned contract
rather than an untyped extension channel that could carry locked output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from specpilot.contracts.manifests import Identifier, Sha256

_FORBIDDEN_KEYS = frozenset({"question", "claim", "excerpt", "answer", "rationale"})


def reject_evaluation_prose_keys(value: object) -> None:
    """Reject prose-bearing keys at any nesting depth, case-insensitively."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden evaluation key: {key.casefold()}")
            reject_evaluation_prose_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            reject_evaluation_prose_keys(child)


class _ClosedEvaluationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_prose_keys(cls, value: object) -> object:
        reject_evaluation_prose_keys(value)
        return value


class EvaluationConfirmation(_ClosedEvaluationModel):
    candidate_sha256: Sha256
    author_id: Literal["chunxue"]
    confirmed: Literal[True] = True


class _EvaluationRunSpecFields(_ClosedEvaluationModel):
    schema_version: Literal["evaluation-run-spec/v1"] = "evaluation-run-spec/v1"
    code_sha256: Sha256
    dependency_sha256: Sha256
    source_sha256: Sha256
    corpus_sha256: Sha256
    collection_sha256: Sha256
    sets_sha256: Sha256
    scripts_sha256: Sha256
    prompts_sha256: Sha256
    config_sha256: Sha256
    policy_sha256: Sha256
    provider_sha256: Sha256
    models_sha256: Sha256
    scoring_sha256: Sha256
    environment_sha256: Sha256
    git_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    git_tree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    l1_count: int = Field(ge=0)
    l1_dev_count: int = Field(ge=0)
    l1_locked_count: int = Field(ge=0)
    l2_count: int = Field(ge=0)
    l2_dev_count: int = Field(ge=0)
    l2_locked_count: int = Field(ge=0)
    deep_review_count: int = Field(ge=0)
    pooling_count: int = Field(ge=0)
    l2_adv_dev_count: Literal[6]
    l2_adv_locked_count: Literal[10]
    l2_adv_registration_sha256: Sha256
    l2_adv_overlap_report_sha256: Sha256
    scoring_route_id: Identifier
    dev_scoring_evidence_sha256: Sha256

    @model_validator(mode="after")
    def _split_counts_sum_to_totals(self) -> Self:
        if self.l1_dev_count + self.l1_locked_count != self.l1_count:
            raise ValueError("L1 split counts do not sum to the total")
        if self.l2_dev_count + self.l2_locked_count != self.l2_count:
            raise ValueError("L2 split counts do not sum to the total")
        return self


class EvaluationRunSpecCandidate(_EvaluationRunSpecFields):
    confirmation: None = None


class EvaluationRunSpec(_EvaluationRunSpecFields):
    confirmation: EvaluationConfirmation


class EvaluationFreezeReport(_ClosedEvaluationModel):
    """An operator result containing only path, hash, status, and aggregate counts."""

    status: Literal["created", "unchanged"]
    artifact_path: Path
    artifact_sha256: Sha256
    counts: dict[Identifier, int]

    @model_validator(mode="after")
    def _counts_are_nonnegative(self) -> Self:
        if any(value < 0 for value in self.counts.values()):
            raise ValueError("evaluation counts must be nonnegative")
        return self


def canonical_evaluation_bytes(record: BaseModel) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a validated evaluation record."""
    return json.dumps(
        record.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
