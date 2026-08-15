"""Candidate-only W5 evaluation freeze and explicit author finalization.

This module consumes closed aggregate/status records.  It never imports an
evaluation runner and has no API for reading individual locked cases or their
outputs.  Finalization is a content-addressed publication operation, not an
evaluation execution path.
"""

from __future__ import annotations

import hashlib
import stat
import subprocess
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from specpilot.contracts.evaluation import (
    EvaluationConfirmation,
    EvaluationFreezeReport,
    EvaluationRunSpec,
    EvaluationRunSpecCandidate,
    canonical_evaluation_bytes,
    reject_evaluation_prose_keys,
)
from specpilot.contracts.manifests import Sha256
from specpilot.manifests._secure_records import SecureRecordDirectory

_MAX_STATUS_BYTES = 256 * 1024
_MAX_SPEC_BYTES = 256 * 1024
_IDENTITY_NAMES = (
    "source",
    "corpus",
    "collection",
    "sets",
    "scripts",
    "prompts",
    "config",
    "policy",
    "provider",
    "models",
    "scoring",
    "environment",
)


class EvaluationFreezeError(ValueError):
    """A closed freeze refusal with one stable machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvaluationFreezeInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    repository: Path
    dependency_lock: Path
    progress_status: Path
    deep_review_status: Path
    pooling_status: Path
    l2_adv_status: Path
    identity_status: Path
    dev_scoring_status: Path
    candidate_dir: Path
    final_dir: Path
    evaluation_executed: bool = False


class _ClosedStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_prose(cls, value: object) -> object:
        reject_evaluation_prose_keys(value)
        return value


class _LevelProgress(_ClosedStatus):
    target_total: int = Field(ge=0)
    completed_total: int = Field(ge=0)
    target_dev: int = Field(ge=0)
    completed_dev: int = Field(ge=0)
    target_locked: int = Field(ge=0)
    completed_locked: int = Field(ge=0)

    @model_validator(mode="after")
    def _split_sums_match(self) -> Self:
        if self.target_dev + self.target_locked != self.target_total:
            raise ValueError("progress target split does not sum")
        if self.completed_dev + self.completed_locked != self.completed_total:
            raise ValueError("progress completed split does not sum")
        return self


class _ProgressStatus(_ClosedStatus):
    l1: _LevelProgress
    l2: _LevelProgress


class _DeepReviewStatus(_ClosedStatus):
    required: int = Field(ge=1)
    completed: int = Field(ge=0)


class _PoolingStatus(_ClosedStatus):
    registered_items: int = Field(ge=1)
    adjudicated_items: int = Field(ge=0)
    blocked: int = Field(ge=0)
    fully_sealed: bool
    all_runs_sealed: bool


class _AdvancedSplit(_ClosedStatus):
    item_ids: tuple[str, ...] = Field(min_length=1)
    families: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_members(self) -> Self:
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("advanced item is repeated")
        if len(set(self.families)) != len(self.families):
            raise ValueError("advanced family is repeated")
        return self


class _AdvancedStatus(_ClosedStatus):
    dev: _AdvancedSplit
    locked: _AdvancedSplit
    overlap_report_sha256: Sha256


class _IdentityStatus(_ClosedStatus):
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


class _DevScoringStatus(_ClosedStatus):
    selected_route: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    evidence_sha256: Sha256
    split: Literal["dev"]


def _read_status(path: Path, model: type[BaseModel], code: str) -> BaseModel:
    try:
        status = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(status.st_mode) or status.st_size > _MAX_STATUS_BYTES:
            raise OSError
        data = path.read_bytes()
        if len(data) != status.st_size:
            raise OSError
        return model.model_validate_json(data)
    except (OSError, ValidationError, ValueError):
        raise EvaluationFreezeError(code) from None


def _git(repository: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise EvaluationFreezeError("git_identity_unavailable") from None
    return result.stdout.strip()


def _require_clean_git(repository: Path) -> tuple[str, str]:
    if _git(repository, "status", "--porcelain=v1", "--untracked-files=all"):
        raise EvaluationFreezeError("dirty_git_tree")
    commit = _git(repository, "rev-parse", "--verify", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    if not commit or not tree:
        raise EvaluationFreezeError("git_identity_unavailable")
    return commit, tree


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_dependency_lock(path: Path) -> bytes:
    try:
        status = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(status.st_mode) or status.st_size > _MAX_STATUS_BYTES:
            raise OSError
        data = path.read_bytes()
    except OSError:
        raise EvaluationFreezeError("dependency_lock_unavailable") from None
    if not data or len(data) != status.st_size:
        raise EvaluationFreezeError("dependency_lock_unavailable")
    return data


def _publish(
    directory: Path,
    record: BaseModel,
    counts: dict[str, int],
) -> EvaluationFreezeReport:
    data = canonical_evaluation_bytes(record)
    if len(data) > _MAX_SPEC_BYTES:
        raise EvaluationFreezeError("evaluation_spec_too_large")
    artifact_sha256 = _sha256(data)
    name = f"{artifact_sha256}.json"
    try:
        with SecureRecordDirectory.open(directory, create=True) as records:
            existed = artifact_sha256 in records.content_ids()
            stored = records.publish(name, data, max_bytes=_MAX_SPEC_BYTES)
    except (OSError, ValueError):
        raise EvaluationFreezeError("evaluation_spec_not_written") from None
    if stored != data:
        raise EvaluationFreezeError("evaluation_spec_not_written")
    return EvaluationFreezeReport(
        status="unchanged" if existed else "created",
        artifact_path=directory / name,
        artifact_sha256=artifact_sha256,
        counts=counts,
    )


def build_candidate(inputs: EvaluationFreezeInputs) -> EvaluationFreezeReport:
    """Validate aggregate evidence and atomically publish one candidate spec."""
    if inputs.evaluation_executed:
        raise EvaluationFreezeError("evaluation_execution_forbidden")

    progress = _read_status(
        inputs.progress_status, _ProgressStatus, "invalid_progress_status"
    )
    deep = _read_status(
        inputs.deep_review_status, _DeepReviewStatus, "invalid_deep_review_status"
    )
    pooling = _read_status(
        inputs.pooling_status, _PoolingStatus, "invalid_pooling_status"
    )
    advanced = _read_status(
        inputs.l2_adv_status, _AdvancedStatus, "invalid_l2_adv_status"
    )
    identities = _read_status(
        inputs.identity_status, _IdentityStatus, "missing_identity"
    )
    scoring = _read_status(
        inputs.dev_scoring_status, _DevScoringStatus, "dev_scoring_evidence_missing"
    )
    assert isinstance(progress, _ProgressStatus)
    assert isinstance(deep, _DeepReviewStatus)
    assert isinstance(pooling, _PoolingStatus)
    assert isinstance(advanced, _AdvancedStatus)
    assert isinstance(identities, _IdentityStatus)
    assert isinstance(scoring, _DevScoringStatus)

    if progress.l1 != _LevelProgress(
        target_total=40,
        completed_total=40,
        target_dev=15,
        completed_dev=15,
        target_locked=25,
        completed_locked=25,
    ):
        raise EvaluationFreezeError("incomplete_l1")
    if progress.l2 != _LevelProgress(
        target_total=20,
        completed_total=20,
        target_dev=8,
        completed_dev=8,
        target_locked=12,
        completed_locked=12,
    ):
        raise EvaluationFreezeError("incomplete_l2")
    if deep.completed != deep.required or deep.required != 12:
        raise EvaluationFreezeError("incomplete_deep_review")
    if (
        not pooling.fully_sealed
        or not pooling.all_runs_sealed
        or pooling.blocked != 0
        or pooling.adjudicated_items != pooling.registered_items
    ):
        raise EvaluationFreezeError("pooling_unsealed")
    if set(advanced.dev.item_ids) & set(advanced.locked.item_ids):
        raise EvaluationFreezeError("l2_adv_id_overlap")
    if set(advanced.dev.families) & set(advanced.locked.families):
        raise EvaluationFreezeError("l2_adv_family_overlap")

    commit, tree = _require_clean_git(inputs.repository)
    dependency_bytes = _read_dependency_lock(inputs.dependency_lock)
    code_sha256 = _sha256(f"{commit}\n{tree}\n".encode("ascii"))
    candidate = EvaluationRunSpecCandidate(
        code_sha256=code_sha256,
        dependency_sha256=_sha256(dependency_bytes),
        **{
            name: getattr(identities, name)
            for name in (f"{item}_sha256" for item in _IDENTITY_NAMES)
        },
        git_commit=commit,
        git_tree=tree,
        l1_count=progress.l1.completed_total,
        l1_dev_count=progress.l1.completed_dev,
        l1_locked_count=progress.l1.completed_locked,
        l2_count=progress.l2.completed_total,
        l2_dev_count=progress.l2.completed_dev,
        l2_locked_count=progress.l2.completed_locked,
        deep_review_count=deep.completed,
        pooling_count=pooling.adjudicated_items,
        l2_adv_dev_count=len(advanced.dev.item_ids),
        l2_adv_locked_count=len(advanced.locked.item_ids),
        l2_adv_overlap_report_sha256=advanced.overlap_report_sha256,
        scoring_route_id=scoring.selected_route,
        dev_scoring_evidence_sha256=scoring.evidence_sha256,
    )
    counts = {
        "l1": candidate.l1_count,
        "l2": candidate.l2_count,
        "deep_review": candidate.deep_review_count,
        "pooling": candidate.pooling_count,
        "l2_adv_dev": candidate.l2_adv_dev_count,
        "l2_adv_locked": candidate.l2_adv_locked_count,
    }
    return _publish(inputs.candidate_dir, candidate, counts)


def finalize_candidate(
    candidate_path: Path,
    *,
    expected_hash: str,
    author_id: str,
    confirmed: bool,
    repository: Path | None = None,
    output_dir: Path | None = None,
    evaluation_executed: bool = False,
) -> EvaluationFreezeReport:
    """Publish a final spec without executing or inspecting an evaluation."""
    if evaluation_executed:
        raise EvaluationFreezeError("evaluation_execution_forbidden")
    if author_id != "chunxue":
        raise EvaluationFreezeError("author_not_authorized")
    if not confirmed:
        raise EvaluationFreezeError("confirmation_required")
    if candidate_path.name != f"{expected_hash}.json":
        raise EvaluationFreezeError("candidate_hash_mismatch")
    try:
        with SecureRecordDirectory.open(candidate_path.parent, create=False) as records:
            data = records.read(candidate_path.name, max_bytes=_MAX_SPEC_BYTES)
    except OSError:
        raise EvaluationFreezeError("candidate_unavailable") from None
    if _sha256(data) != expected_hash:
        raise EvaluationFreezeError("candidate_hash_mismatch")
    try:
        candidate = EvaluationRunSpecCandidate.model_validate_json(data)
    except ValidationError:
        raise EvaluationFreezeError("invalid_candidate") from None
    if canonical_evaluation_bytes(candidate) != data:
        raise EvaluationFreezeError("candidate_bytes_changed")

    checked_repository = repository if repository is not None else Path.cwd()
    commit, tree = _require_clean_git(checked_repository)
    if commit != candidate.git_commit or tree != candidate.git_tree:
        raise EvaluationFreezeError("git_identity_changed")
    final_directory = (
        output_dir if output_dir is not None else candidate_path.parent / "final"
    )
    payload = candidate.model_dump(mode="json")
    payload["confirmation"] = EvaluationConfirmation(
        candidate_sha256=expected_hash,
        author_id="chunxue",
        confirmed=True,
    ).model_dump(mode="json")
    final = EvaluationRunSpec.model_validate(payload)
    counts = {
        "l1": final.l1_count,
        "l2": final.l2_count,
        "deep_review": final.deep_review_count,
        "pooling": final.pooling_count,
        "l2_adv_dev": final.l2_adv_dev_count,
        "l2_adv_locked": final.l2_adv_locked_count,
    }
    return _publish(final_directory, final, counts)
