from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.cli import main
from tests.cli.conftest import parse_stdout
from tests.helpers.evaluation_factory import make_evaluation_workspace


def _candidate_args(paths: dict[str, Path]) -> list[str]:
    return [
        "evaluation",
        "freeze-candidate",
        "--repository",
        str(paths["repository"]),
        "--dependency-lock",
        str(paths["dependency_lock"]),
        "--progress-status",
        str(paths["progress_status"]),
        "--deep-review-status",
        str(paths["deep_review_status"]),
        "--pooling-status",
        str(paths["pooling_status"]),
        "--l2-adv-status",
        str(paths["l2_adv_status"]),
        "--identity-status",
        str(paths["identity_status"]),
        "--dev-scoring-status",
        str(paths["dev_scoring_status"]),
        "--candidate-dir",
        str(paths["candidate_dir"]),
    ]


def test_candidate_cli_emits_only_path_hash_and_aggregate_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_evaluation_workspace(tmp_path)

    code = main(_candidate_args(paths))

    captured = capsys.readouterr()
    assert code == 0, captured.err
    payload = parse_stdout(captured.out)
    assert set(payload) == {"path", "hash", "counts"}
    assert payload["counts"] == {
        "deep_review": 12,
        "l1": 40,
        "l2": 20,
        "l2_adv_dev": 1,
        "l2_adv_locked": 1,
        "pooling": 60,
    }
    assert "question" not in captured.out.casefold()
    assert captured.err == ""


def test_candidate_cli_refusal_writes_no_candidate_and_leaks_no_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    body = json.loads(paths["pooling_status"].read_text(encoding="utf-8"))
    body["fully_sealed"] = False
    paths["pooling_status"].write_text(json.dumps(body), encoding="utf-8")

    code = main(_candidate_args(paths))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "pooling_unsealed\n"
    assert str(tmp_path) not in captured.err
    assert not paths["candidate_dir"].exists()


def test_confirmation_cli_requires_the_literal_confirmation_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    assert main(_candidate_args(paths)) == 0
    candidate = parse_stdout(capsys.readouterr().out)
    args = [
        "evaluation",
        "freeze-confirm",
        "--candidate",
        str(candidate["path"]),
        "--expected-hash",
        str(candidate["hash"]),
        "--author-id",
        "chunxue",
        "--repository",
        str(paths["repository"]),
        "--output-dir",
        str(paths["final_dir"]),
    ]

    assert main(args) == 4

    assert not paths["final_dir"].exists()
    assert capsys.readouterr().err == "invalid_evaluation_freeze_arguments\n"


def test_confirmation_cli_retry_keeps_the_same_three_field_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_evaluation_workspace(tmp_path)
    assert main(_candidate_args(paths)) == 0
    candidate = parse_stdout(capsys.readouterr().out)
    args = [
        "evaluation",
        "freeze-confirm",
        "--candidate",
        str(candidate["path"]),
        "--expected-hash",
        str(candidate["hash"]),
        "--author-id",
        "chunxue",
        "--confirm-freeze",
        "--repository",
        str(paths["repository"]),
        "--output-dir",
        str(paths["final_dir"]),
    ]

    assert main(args) == 0
    created = parse_stdout(capsys.readouterr().out)
    assert set(created) == {"path", "hash", "counts"}
    assert main(args) == 0
    unchanged = parse_stdout(capsys.readouterr().out)
    assert unchanged == created


def test_evaluation_cli_has_no_execution_subcommand() -> None:
    parser = __import__("specpilot.cli", fromlist=["_parser"])._parser()
    evaluation_action = next(
        action for action in parser._actions if action.dest == "group"
    )
    evaluation_parser = evaluation_action.choices["evaluation"]
    subcommands = next(
        action.choices
        for action in evaluation_parser._actions
        if action.dest == "evaluation_command"
    )

    assert set(subcommands) == {"freeze-candidate", "freeze-confirm"}
