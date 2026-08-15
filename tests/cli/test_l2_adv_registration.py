from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.cli import main

_DIMENSIONS = (
    "request_vs_response",
    "role_attribution",
    "document_attribution",
    "normative_strength",
    "received_vs_generated",
)


def _record(index: int, split: str) -> dict[str, object]:
    tag = f"{split}-{index:03d}"
    base = index + (0 if split == "dev" else 1_000)
    return {
        "group_id": f"adv-{tag}",
        "family": f"family-{tag}",
        "split": split,
        "dimension": _DIMENSIONS[index % len(_DIMENSIONS)],
        "negative_claim_id": f"adv-{tag}-neg",
        "negative_claim": f"the proxy must reject request {tag}",
        "distractor_clause_ids": [f"{base:064x}"],
        "positive_claim_id": f"adv-{tag}-pos",
        "positive_claim": f"the origin server must reject request {tag}",
        "supporting_clause_ids": [f"{base + 100:064x}"],
        "proposed_verdict": "violating",
    }


def _add(tmp_path: Path, group_dir: Path, record: dict[str, object]) -> int:
    path = tmp_path / f"{record['group_id']}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return main(
        [
            "annotation",
            "adv-add",
            "--record",
            str(path),
            "--group-dir",
            str(group_dir),
        ]
    )


def _register_all(tmp_path: Path, group_dir: Path) -> None:
    for index in range(6):
        assert _add(tmp_path, group_dir, _record(index, "dev")) == 0
    for index in range(10):
        assert _add(tmp_path, group_dir, _record(index, "locked")) == 0


def test_the_template_names_every_field_an_author_must_fill(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["annotation", "adv-template"]) == 0

    template = json.loads(capsys.readouterr().out)
    assert set(template) >= {
        "group_id",
        "family",
        "split",
        "dimension",
        "negative_claim_id",
        "negative_claim",
        "distractor_clause_ids",
        "positive_claim_id",
        "positive_claim",
        "supporting_clause_ids",
        "proposed_verdict",
    }


def test_a_group_round_trips_through_the_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    group_dir = tmp_path / "l2-adv"
    assert _add(tmp_path, group_dir, _record(0, "dev")) == 0

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["status"] == "stored"
    assert emitted["group_id"] == "adv-dev-000"
    assert len(emitted["group_record_id"]) == 64


def test_a_pair_built_from_one_claim_is_refused_at_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    group_dir = tmp_path / "l2-adv"
    record = _record(0, "dev")
    record["positive_claim"] = record["negative_claim"]

    assert _add(tmp_path, group_dir, record) != 0
    assert "invalid_adversarial_group" in capsys.readouterr().err


def test_a_duplicate_group_id_does_not_overwrite_the_stored_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    group_dir = tmp_path / "l2-adv"
    assert _add(tmp_path, group_dir, _record(0, "dev")) == 0
    capsys.readouterr()

    duplicate = _record(0, "dev")
    duplicate["family"] = "family-somewhere-else"
    assert _add(tmp_path, group_dir, duplicate) != 0
    assert "adversarial_group_exists" in capsys.readouterr().err


def test_a_complete_registration_writes_the_report_and_the_gate_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    group_dir = tmp_path / "l2-adv"
    _register_all(tmp_path, group_dir)
    capsys.readouterr()

    status_out = tmp_path / "l2-adv-status.json"
    report_out = tmp_path / "l2-adv-overlap.json"
    assert (
        main(
            [
                "annotation",
                "adv-status",
                "--group-dir",
                str(group_dir),
                "--status-out",
                str(status_out),
                "--report-out",
                str(report_out),
            ]
        )
        == 0
    )

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["status"] == "registered"
    assert emitted["dev"] == 6
    assert emitted["locked"] == 10
    assert emitted["clean"] is True

    status = json.loads(status_out.read_text(encoding="utf-8"))
    assert status["schema_version"] == "l2-adv-registration/v1"
    assert len(status["dev"]["item_ids"]) == 6
    assert len(status["locked"]["item_ids"]) == 10

    report = json.loads(report_out.read_text(encoding="utf-8"))
    assert report["schema_version"] == "l2-adv-overlap/v1"
    assert sum(report["dimension_counts"].values()) == 16


def test_an_incomplete_registration_refuses_and_writes_no_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A partial subset must not produce a file a freeze would accept.

    Sixteen groups is a gate condition, so a status written at fifteen is a
    file whose only use is to fail later, further from the cause.
    """
    group_dir = tmp_path / "l2-adv"
    for index in range(6):
        assert _add(tmp_path, group_dir, _record(index, "dev")) == 0
    for index in range(9):
        assert _add(tmp_path, group_dir, _record(index, "locked")) == 0
    capsys.readouterr()

    status_out = tmp_path / "l2-adv-status.json"
    report_out = tmp_path / "l2-adv-overlap.json"
    assert (
        main(
            [
                "annotation",
                "adv-status",
                "--group-dir",
                str(group_dir),
                "--status-out",
                str(status_out),
                "--report-out",
                str(report_out),
            ]
        )
        != 0
    )

    assert "l2_adv_registration_refused" in capsys.readouterr().err
    assert not status_out.exists()
    # The report is diagnostic and is written anyway: it is how the author sees
    # which axis or count is short.
    assert report_out.exists()


def test_an_overlapping_registration_names_the_axis_that_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    group_dir = tmp_path / "l2-adv"
    for index in range(6):
        assert _add(tmp_path, group_dir, _record(index, "dev")) == 0
    for index in range(9):
        assert _add(tmp_path, group_dir, _record(index, "locked")) == 0
    colliding = _record(9, "locked")
    colliding["negative_claim"] = _record(0, "dev")["negative_claim"]
    assert _add(tmp_path, group_dir, colliding) == 0
    capsys.readouterr()

    report_out = tmp_path / "l2-adv-overlap.json"
    assert (
        main(
            [
                "annotation",
                "adv-status",
                "--group-dir",
                str(group_dir),
                "--status-out",
                str(tmp_path / "l2-adv-status.json"),
                "--report-out",
                str(report_out),
            ]
        )
        != 0
    )

    report = json.loads(report_out.read_text(encoding="utf-8"))
    assert report["checks"]["claim"]["disjoint"] is False
    assert not (tmp_path / "l2-adv-status.json").exists()
