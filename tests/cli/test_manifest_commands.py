from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cli.conftest import parse_stdout
from tests.unit.manifests.test_source_manifest import assessment, initial_fields


def create_args(tmp_path: Path) -> list[str]:
    fields = initial_fields()
    return [
        "source-manifest",
        "create",
        "--manifest-dir",
        str(tmp_path / "manifests"),
        "--document-id",
        str(fields["document_id"]),
        "--document-version",
        str(fields["document_version"]),
        "--download-url",
        str(fields["download_url"]),
        "--archive-sha256",
        str(fields["archive_sha256"]),
        "--docx-sha256",
        str(fields["docx_sha256"]),
        "--downloaded-at",
        str(fields["downloaded_at"]),
        "--created-at",
        str(fields["created_at"]),
    ]


def write_assessment(tmp_path: Path, **kwargs: object) -> Path:
    path = tmp_path / "assessment.json"
    path.write_text(
        assessment(**kwargs).model_dump_json(indent=2),  # type: ignore[arg-type]
        encoding="utf-8",
    )
    return path


def authorize_args(tmp_path: Path, manifest_id: str, evidence: Path) -> list[str]:
    return [
        "source-manifest",
        "authorize-successor",
        "--manifest-dir",
        str(tmp_path / "manifests"),
        "--predecessor",
        manifest_id,
        "--assessment",
        str(evidence),
        "--provider-id",
        "provider-a",
        "--endpoint-purpose",
        "evidence-review",
        "--use",
        "online_main",
        "--created-at",
        "2026-08-06T03:00:00Z",
    ]


def test_created_manifest_is_default_deny(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    code, out, err = run_cli(create_args(tmp_path), capsys)

    assert code == 0, err
    payload = parse_stdout(out)
    assert payload["cloud_egress_authorized"] is False
    assert payload["status"] == "created"
    assert len(str(payload["manifest_id"])) == 64
    assert set(payload) == {
        "status",
        "manifest_id",
        "cloud_egress_authorized",
        "predecessor_manifest_id",
    }


def test_authorizing_a_successor_binds_one_route(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    _, out, _ = run_cli(create_args(tmp_path), capsys)
    initial = str(parse_stdout(out)["manifest_id"])
    evidence = write_assessment(tmp_path)

    code, out, err = run_cli(authorize_args(tmp_path, initial, evidence), capsys)

    assert code == 0, err
    payload = parse_stdout(out)
    assert payload["status"] == "authorized"
    assert payload["predecessor_manifest_id"] == initial
    assert payload["cloud_egress_authorized"] is True
    assert payload["manifest_id"] != initial


def test_a_negative_conclusion_cannot_authorize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    _, out, _ = run_cli(create_args(tmp_path), capsys)
    initial = str(parse_stdout(out)["manifest_id"])
    evidence = write_assessment(tmp_path, authorized=False)

    code, out, err = run_cli(authorize_args(tmp_path, initial, evidence), capsys)

    assert code != 0
    assert out == ""
    assert err.strip() == "invalid_authorization_evidence"


def test_a_conclusion_for_another_route_cannot_authorize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    _, out, _ = run_cli(create_args(tmp_path), capsys)
    initial = str(parse_stdout(out)["manifest_id"])
    evidence = write_assessment(tmp_path, provider_id="provider-b")

    code, _, err = run_cli(authorize_args(tmp_path, initial, evidence), capsys)

    assert code != 0
    assert err.strip() == "invalid_authorization_evidence"


def test_incomplete_evidence_cannot_authorize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    _, out, _ = run_cli(create_args(tmp_path), capsys)
    initial = str(parse_stdout(out)["manifest_id"])
    fields = json.loads(assessment().model_dump_json())
    del fields["provider_policy"]
    evidence = tmp_path / "assessment.json"
    evidence.write_text(json.dumps(fields), encoding="utf-8")

    code, _, err = run_cli(authorize_args(tmp_path, initial, evidence), capsys)

    assert code != 0
    assert err.strip() == "invalid_authorization_evidence"


def test_an_unknown_predecessor_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    evidence = write_assessment(tmp_path)

    code, out, err = run_cli(authorize_args(tmp_path, "0" * 64, evidence), capsys)

    assert code == 2
    assert out == ""
    assert err == "manifest_not_found\n"


def test_an_unsupported_predecessor_schema_version_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(mode=0o700)
    manifest_id = "e" * 64
    manifest_path = manifest_dir / f"{manifest_id}.json"
    manifest_path.write_text(
        '{"schema_version":"source-manifest/v2"}',
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    evidence = write_assessment(tmp_path)

    code, out, err = run_cli(authorize_args(tmp_path, manifest_id, evidence), capsys)

    assert code == 2
    assert out == ""
    assert err == "unsupported_manifest_version\n"


def test_a_malformed_predecessor_is_not_an_unsupported_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(mode=0o700)
    manifest_id = "f" * 64
    manifest_path = manifest_dir / f"{manifest_id}.json"
    manifest_path.write_bytes(b'{"schema_version":')
    manifest_path.chmod(0o600)
    evidence = write_assessment(tmp_path)

    code, out, err = run_cli(authorize_args(tmp_path, manifest_id, evidence), capsys)

    assert code == 2
    assert out == ""
    assert err == "manifest_not_found\n"


def test_a_nonstandard_json_predecessor_is_not_an_unsupported_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(mode=0o700)
    manifest_id = "d" * 64
    manifest_path = manifest_dir / f"{manifest_id}.json"
    manifest_path.write_bytes(b'{"schema_version":"source-manifest/v2","extra":NaN}')
    manifest_path.chmod(0o600)
    evidence = write_assessment(tmp_path)

    code, out, err = run_cli(authorize_args(tmp_path, manifest_id, evidence), capsys)

    assert code == 2
    assert out == ""
    assert err == "manifest_not_found\n"
