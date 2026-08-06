from __future__ import annotations

from pathlib import Path

import pytest

from tests.cli.conftest import (
    FIXTURE_CONTENT_MARKERS,
    build_submission,
    parse_stdout,
)


def inspect_args(tmp_path: Path, archive: Path) -> list[str]:
    return [
        "archive",
        "inspect",
        "--archive",
        str(archive),
        "--destination",
        str(tmp_path / "corpus" / "iso-9001"),
        "--quarantine",
        str(tmp_path / "quarantine"),
        "--expect-docx",
        "expected.docx",
    ]


def test_safe_archive_reports_hashes_and_counts_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    archive = build_submission(tmp_path)

    code, out, err = run_cli(inspect_args(tmp_path, archive), capsys)

    assert code == 0, err
    payload = parse_stdout(out)
    assert payload["status"] == "accepted"
    assert len(str(payload["archive_sha256"])) == 64
    assert len(str(payload["docx_sha256"])) == 64
    assert isinstance(payload["member_count"], int)
    assert set(payload) == {
        "status",
        "archive_sha256",
        "docx_sha256",
        "byte_count",
        "member_count",
        "relationship_count",
    }, "stdout must carry identifiers and counts only"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("vba_project", "macro"),
        ("embedded_executable", "embedded_active_content"),
        ("external_relationship", "external_relationship"),
    ],
)
def test_unsafe_package_exits_non_zero_with_a_stable_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
    mutation: str,
    expected_code: str,
) -> None:
    archive = build_submission(tmp_path, mutation=mutation)

    code, out, err = run_cli(inspect_args(tmp_path, archive), capsys)

    assert code != 0
    assert err.strip() == expected_code, "stderr carries the code and nothing else"
    assert out == "", "a rejected input produces no result document"


def test_unexpected_member_is_quarantined_and_names_no_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    archive = build_submission(tmp_path, member_name="unexpected.docx")

    code, _, err = run_cli(inspect_args(tmp_path, archive), capsys)

    assert code != 0
    assert err.strip() == "unexpected_member"
    quarantined = list((tmp_path / "quarantine").glob("*/record.json"))
    assert len(quarantined) == 1


def test_diagnostics_never_echo_package_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    archive = build_submission(tmp_path, mutation="external_relationship")

    _, out, err = run_cli(inspect_args(tmp_path, archive), capsys)

    for marker in FIXTURE_CONTENT_MARKERS:
        assert marker not in out
        assert marker not in err
    assert str(archive) not in err, "a filesystem path is not a public diagnostic"
