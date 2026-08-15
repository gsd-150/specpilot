from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.contracts.evaluation import (
    EvaluationRunSpecCandidate,
    canonical_evaluation_bytes,
)
from specpilot.evaluation.freeze import (
    EvaluationFreezeError,
    EvaluationFreezeInputs,
    build_candidate,
    finalize_candidate,
)
from tests.helpers.evaluation_factory import HASHES, make_evaluation_workspace


def _candidate_fields() -> dict[str, object]:
    return {
        "code_sha256": "a" * 64,
        "dependency_sha256": "b" * 64,
        **{
            f"{name}_sha256": value
            for name, value in HASHES.items()
            if name not in {"overlap", "evidence"}
        },
        "git_commit": "1" * 40,
        "git_tree": "2" * 40,
        "l1_count": 40,
        "l1_dev_count": 15,
        "l1_locked_count": 25,
        "l2_count": 20,
        "l2_dev_count": 8,
        "l2_locked_count": 12,
        "deep_review_count": 12,
        "pooling_count": 60,
        "l2_adv_dev_count": 6,
        "l2_adv_locked_count": 10,
        "l2_adv_registration_sha256": "f" * 64,
        "l2_adv_overlap_report_sha256": HASHES["overlap"],
        "scoring_route_id": "dev-calibrated-v1",
        "dev_scoring_evidence_sha256": HASHES["evidence"],
    }


def _inputs(paths: dict[str, Path]) -> EvaluationFreezeInputs:
    return EvaluationFreezeInputs(**paths)


def _rewrite(path: Path, update: dict[str, object]) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    body.update(update)
    path.write_text(json.dumps(body), encoding="utf-8")


def _advanced_registration(
    *, dev_count: int = 6, locked_count: int = 10
) -> dict[str, object]:
    return {
        "schema_version": "l2-adv-registration/v1",
        "dev": {
            "item_ids": [f"adv-dev-{index}" for index in range(1, dev_count + 1)],
            "families": [f"family-dev-{index}" for index in range(1, dev_count + 1)],
        },
        "locked": {
            "item_ids": [
                f"adv-locked-{index}" for index in range(1, locked_count + 1)
            ],
            "families": [
                f"family-locked-{index}" for index in range(1, locked_count + 1)
            ],
        },
        "overlap_report_sha256": HASHES["overlap"],
    }


def _swap_name_after_open(
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    replacement: Path,
) -> None:
    original_open = os.open
    swapped = False

    def open_then_swap(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == target.name and dir_fd is not None and not swapped:
            swapped = True
            target.rename(target.with_suffix(".pinned"))
            target.symlink_to(replacement)
        return descriptor

    monkeypatch.setattr("specpilot.evaluation.freeze.os.open", open_then_swap)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {open_then_swap})


def test_candidate_contract_requires_every_frozen_identity_hash() -> None:
    fields = _candidate_fields()
    del fields["environment_sha256"]

    with pytest.raises(ValidationError):
        EvaluationRunSpecCandidate.model_validate(fields)


def test_candidate_contract_binds_the_l1_and_l2_split_counts() -> None:
    candidate = EvaluationRunSpecCandidate.model_validate(_candidate_fields())

    assert (candidate.l1_dev_count, candidate.l1_locked_count) == (15, 25)
    assert (candidate.l2_dev_count, candidate.l2_locked_count) == (8, 12)
    assert candidate.scoring_route_id == "dev-calibrated-v1"


@pytest.mark.parametrize(
    "forbidden", ["question", "claim", "excerpt", "answer", "rationale"]
)
def test_contract_recursively_rejects_prose_bearing_keys(forbidden: str) -> None:
    fields = _candidate_fields()
    fields["extensions"] = {"safe": [{forbidden: "must never be stored"}]}

    with pytest.raises(ValidationError, match="forbidden evaluation key"):
        EvaluationRunSpecCandidate.model_validate(fields)


def test_candidate_contract_rejects_even_opaque_extension_payloads() -> None:
    fields = _candidate_fields()
    fields["extensions"] = {"payload": "locked output bytes are not extensible"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvaluationRunSpecCandidate.model_validate(fields)


def test_canonical_candidate_bytes_are_stable_and_exclude_host_paths() -> None:
    candidate = EvaluationRunSpecCandidate.model_validate(_candidate_fields())

    encoded = canonical_evaluation_bytes(candidate)

    assert encoded == canonical_evaluation_bytes(candidate)
    assert b"/Users/" not in encoded
    assert encoded.endswith(b"}")
    assert b"\n" not in encoded


@pytest.mark.parametrize(
    ("status_name", "update", "code"),
    [
        (
            "progress_status",
            {
                "l1": {
                    "target_total": 40,
                    "completed_total": 39,
                    "target_dev": 15,
                    "completed_dev": 15,
                    "target_locked": 25,
                    "completed_locked": 24,
                }
            },
            "incomplete_l1",
        ),
        (
            "progress_status",
            {
                "l2": {
                    "target_total": 20,
                    "completed_total": 19,
                    "target_dev": 8,
                    "completed_dev": 8,
                    "target_locked": 12,
                    "completed_locked": 11,
                }
            },
            "incomplete_l2",
        ),
        ("deep_review_status", {"completed": 11}, "incomplete_deep_review"),
        ("pooling_status", {"fully_sealed": False}, "pooling_unsealed"),
        ("pooling_status", {"all_runs_sealed": False}, "pooling_unsealed"),
    ],
)
def test_candidate_refuses_incomplete_aggregate_status_without_writing(
    tmp_path: Path, status_name: str, update: dict[str, object], code: str
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    _rewrite(paths[status_name], update)

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == code
    assert not paths["candidate_dir"].exists()


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "locked",
            {
                "item_ids": ["adv-dev-1"]
                + [f"adv-locked-{index}" for index in range(2, 11)],
                "families": [
                    f"family-locked-{index}" for index in range(1, 11)
                ],
            },
            "l2_adv_id_overlap",
        ),
        (
            "locked",
            {
                "item_ids": [f"adv-locked-{index}" for index in range(1, 11)],
                "families": ["family-dev-1"]
                + [f"family-locked-{index}" for index in range(2, 11)],
            },
            "l2_adv_family_overlap",
        ),
    ],
)
def test_candidate_refuses_overlapping_l2_advanced_sets(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    _rewrite(paths["l2_adv_status"], {field: value})

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == code
    assert not paths["candidate_dir"].exists()


@pytest.mark.parametrize(("dev_count", "locked_count"), [(1, 1), (5, 10), (6, 9)])
def test_candidate_refuses_unregistered_l2_advanced_cardinality(
    tmp_path: Path, dev_count: int, locked_count: int
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    paths["l2_adv_status"].write_text(
        json.dumps(
            _advanced_registration(
                dev_count=dev_count, locked_count=locked_count
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "l2_adv_cardinality_mismatch"
    assert not paths["candidate_dir"].exists()


def test_candidate_refuses_unpaired_l2_advanced_item_and_family_identities(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    registration = _advanced_registration()
    assert isinstance(registration["dev"], dict)
    registration["dev"]["families"] = registration["dev"]["families"][:-1]
    paths["l2_adv_status"].write_text(
        json.dumps(registration), encoding="utf-8"
    )

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "invalid_l2_adv_status"
    assert not paths["candidate_dir"].exists()


def test_candidate_binds_exact_registered_l2_advanced_identities(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    registration = _advanced_registration()
    canonical = json.dumps(
        registration,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    paths["l2_adv_status"].write_bytes(canonical)

    report = build_candidate(_inputs(paths))

    candidate = json.loads(report.artifact_path.read_text(encoding="utf-8"))
    assert candidate["l2_adv_dev_count"] == 6
    assert candidate["l2_adv_locked_count"] == 10
    assert candidate["l2_adv_registration_sha256"] == hashlib.sha256(
        canonical
    ).hexdigest()


def test_candidate_refuses_missing_identity_and_dev_scoring_evidence(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    identities = json.loads(paths["identity_status"].read_text(encoding="utf-8"))
    del identities["policy_sha256"]
    paths["identity_status"].write_text(json.dumps(identities), encoding="utf-8")

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))
    assert captured.value.code == "missing_identity"
    assert not paths["candidate_dir"].exists()

    paths = make_evaluation_workspace(tmp_path / "second")
    _rewrite(paths["dev_scoring_status"], {"evidence_sha256": None})
    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))
    assert captured.value.code == "dev_scoring_evidence_missing"
    assert not paths["candidate_dir"].exists()


def test_candidate_refuses_dirty_git_tree_without_writing(tmp_path: Path) -> None:
    paths = make_evaluation_workspace(tmp_path)
    (paths["repository"] / "tracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "dirty_git_tree"
    assert not paths["candidate_dir"].exists()


def test_candidate_refuses_status_name_swapped_after_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    replacement = tmp_path / "replacement-progress.json"
    replacement.write_bytes(paths["progress_status"].read_bytes())
    _swap_name_after_open(monkeypatch, paths["progress_status"], replacement)

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "invalid_progress_status"
    assert not paths["candidate_dir"].exists()


def test_candidate_refuses_dependency_name_swapped_after_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    replacement = tmp_path / "replacement.lock"
    replacement.write_bytes(paths["dependency_lock"].read_bytes())
    _swap_name_after_open(monkeypatch, paths["dependency_lock"], replacement)

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "dependency_lock_unavailable"
    assert not paths["candidate_dir"].exists()


def test_candidate_refuses_fifo_status_without_blocking_or_writing(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    paths["progress_status"].unlink()
    os.mkfifo(paths["progress_status"], mode=0o600)

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "invalid_progress_status"
    assert not paths["candidate_dir"].exists()


def test_candidate_refuses_oversized_status_without_writing(tmp_path: Path) -> None:
    paths = make_evaluation_workspace(tmp_path)
    paths["progress_status"].write_bytes(b"x" * (256 * 1024 + 1))

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(_inputs(paths))

    assert captured.value.code == "invalid_progress_status"
    assert not paths["candidate_dir"].exists()


def test_candidate_is_atomic_content_addressed_and_reports_counts_only(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)

    report = build_candidate(_inputs(paths))

    candidate = report.artifact_path.read_bytes()
    assert hashlib.sha256(candidate).hexdigest() == report.artifact_sha256
    assert report.status == "created"
    assert report.counts == {
        "l1": 40,
        "l2": 20,
        "deep_review": 12,
        "pooling": 60,
        "l2_adv_dev": 6,
        "l2_adv_locked": 10,
    }
    assert list(paths["candidate_dir"].iterdir()) == [report.artifact_path]
    assert (
        set(json.loads(candidate))
        & {"question", "claim", "excerpt", "answer", "rationale"}
        == set()
    )


def test_confirmation_requires_exact_bytes_author_flag_clean_tree_and_no_execution(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    candidate = build_candidate(_inputs(paths))

    cases = [
        (
            {"expected_hash": "0" * 64, "author_id": "chunxue", "confirmed": True},
            "candidate_hash_mismatch",
        ),
        (
            {
                "expected_hash": candidate.artifact_sha256,
                "author_id": "someone",
                "confirmed": True,
            },
            "author_not_authorized",
        ),
        (
            {
                "expected_hash": candidate.artifact_sha256,
                "author_id": "chunxue",
                "confirmed": False,
            },
            "confirmation_required",
        ),
    ]
    for kwargs, code in cases:
        with pytest.raises(EvaluationFreezeError) as captured:
            finalize_candidate(
                candidate.artifact_path,
                repository=paths["repository"],
                output_dir=paths["final_dir"],
                **kwargs,
            )
        assert captured.value.code == code
        assert not paths["final_dir"].exists()

    changed = candidate.artifact_path.read_bytes() + b"\n"
    candidate.artifact_path.write_bytes(changed)
    with pytest.raises(EvaluationFreezeError) as captured:
        finalize_candidate(
            candidate.artifact_path,
            expected_hash=candidate.artifact_sha256,
            author_id="chunxue",
            confirmed=True,
            repository=paths["repository"],
            output_dir=paths["final_dir"],
        )
    assert captured.value.code == "candidate_hash_mismatch"


def test_confirmation_is_create_once_and_identical_retry_is_unchanged(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    candidate = build_candidate(_inputs(paths))

    created = finalize_candidate(
        candidate.artifact_path,
        expected_hash=candidate.artifact_sha256,
        author_id="chunxue",
        confirmed=True,
        repository=paths["repository"],
        output_dir=paths["final_dir"],
    )
    unchanged = finalize_candidate(
        candidate.artifact_path,
        expected_hash=candidate.artifact_sha256,
        author_id="chunxue",
        confirmed=True,
        repository=paths["repository"],
        output_dir=paths["final_dir"],
    )

    assert created.status == "created"
    assert unchanged.status == "unchanged"
    assert created.artifact_path == unchanged.artifact_path
    assert created.artifact_path.read_bytes() == unchanged.artifact_path.read_bytes()


def test_confirmation_refuses_a_candidate_path_replaced_by_a_symlink(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    candidate = build_candidate(_inputs(paths))
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(candidate.artifact_path.read_bytes())
    candidate.artifact_path.unlink()
    candidate.artifact_path.symlink_to(replacement)

    with pytest.raises(EvaluationFreezeError) as captured:
        finalize_candidate(
            candidate.artifact_path,
            expected_hash=candidate.artifact_sha256,
            author_id="chunxue",
            confirmed=True,
            repository=paths["repository"],
            output_dir=paths["final_dir"],
        )

    assert captured.value.code == "candidate_unavailable"
    assert not paths["final_dir"].exists()


def test_confirmation_refuses_dirty_git_or_any_execution_marker(
    tmp_path: Path,
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    candidate = build_candidate(_inputs(paths))
    (paths["repository"] / "tracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(EvaluationFreezeError) as captured:
        finalize_candidate(
            candidate.artifact_path,
            expected_hash=candidate.artifact_sha256,
            author_id="chunxue",
            confirmed=True,
            repository=paths["repository"],
            output_dir=paths["final_dir"],
        )
    assert captured.value.code == "dirty_git_tree"
    assert not paths["final_dir"].exists()

    (paths["repository"] / "tracked.txt").write_text("frozen\n", encoding="utf-8")
    with pytest.raises(EvaluationFreezeError) as captured:
        finalize_candidate(
            candidate.artifact_path,
            expected_hash=candidate.artifact_sha256,
            author_id="chunxue",
            confirmed=True,
            repository=paths["repository"],
            output_dir=paths["final_dir"],
            evaluation_executed=True,
        )
    assert captured.value.code == "evaluation_execution_forbidden"
    assert not paths["final_dir"].exists()


def test_candidate_rejects_any_evaluation_execution_marker(tmp_path: Path) -> None:
    paths = make_evaluation_workspace(tmp_path)

    with pytest.raises(EvaluationFreezeError) as captured:
        build_candidate(EvaluationFreezeInputs(**paths, evaluation_executed=True))

    assert captured.value.code == "evaluation_execution_forbidden"
    assert not paths["candidate_dir"].exists()
