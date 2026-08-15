from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

HASHES = {
    name: f"{index:x}" * 64
    for index, name in enumerate(
        (
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
            "overlap",
            "evidence",
        ),
        start=1,
    )
}


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_evaluation_workspace(tmp_path: Path) -> dict[str, Path]:
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Fixture"], cwd=repository, check=True
    )
    lock = repository / "requirements.lock"
    lock.write_text("specpilot==1\n", encoding="utf-8")
    tracked = repository / "tracked.txt"
    tracked.write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)

    status_dir = tmp_path / "status"
    status_dir.mkdir()
    progress = _write(
        status_dir / "progress.json",
        {
            "l1": {
                "target_total": 40,
                "completed_total": 40,
                "target_dev": 15,
                "completed_dev": 15,
                "target_locked": 25,
                "completed_locked": 25,
            },
            "l2": {
                "target_total": 20,
                "completed_total": 20,
                "target_dev": 8,
                "completed_dev": 8,
                "target_locked": 12,
                "completed_locked": 12,
            },
        },
    )
    deep = _write(status_dir / "deep.json", {"required": 12, "completed": 12})
    pooling = _write(
        status_dir / "pooling.json",
        {
            "registered_items": 60,
            "adjudicated_items": 60,
            "blocked": 0,
            "fully_sealed": True,
            "all_runs_sealed": True,
        },
    )
    l2_adv = _write(
        status_dir / "l2-adv.json",
        {
            "dev": {"item_ids": ["adv-dev-1"], "families": ["family-dev"]},
            "locked": {
                "item_ids": ["adv-locked-1"],
                "families": ["family-locked"],
            },
            "overlap_report_sha256": HASHES["overlap"],
        },
    )
    identities = _write(
        status_dir / "identities.json",
        {
            f"{name}_sha256": value
            for name, value in HASHES.items()
            if name not in {"overlap", "evidence"}
        },
    )
    scoring = _write(
        status_dir / "scoring.json",
        {
            "selected_route": "dev-calibrated-v1",
            "evidence_sha256": HASHES["evidence"],
            "split": "dev",
        },
    )
    return {
        "repository": repository,
        "dependency_lock": lock,
        "progress_status": progress,
        "deep_review_status": deep,
        "pooling_status": pooling,
        "l2_adv_status": l2_adv,
        "identity_status": identities,
        "dev_scoring_status": scoring,
        "candidate_dir": tmp_path / "candidates",
        "final_dir": tmp_path / "final",
    }
