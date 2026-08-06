from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.ooxml_factory import build_docx, build_relationship_docx


@pytest.mark.integration
def test_worker_accepts_safe_docx_from_read_only_input_directory(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    source = build_docx(input_dir)
    input_dir.chmod(0o500)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "specpilot.ingestion.sandbox_worker",
                "inspect",
                "--input",
                os.fspath(source),
                "--output",
                os.fspath(output_dir / "inspection.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        input_dir.chmod(0o700)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    outputs = list(output_dir.iterdir())
    assert [path.name for path in outputs] == ["inspection.json"]
    result = json.loads(outputs[0].read_text())
    assert set(result) == {
        "external_relationships",
        "member_count",
        "package_bytes",
        "package_sha256",
        "relationship_count",
    }
    assert result["external_relationships"] == []


@pytest.mark.integration
def test_worker_rejects_unsafe_docx_without_leaking_relationship_data(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    source = build_docx(input_dir, "external_relationship")
    input_dir.chmod(0o500)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "specpilot.ingestion.sandbox_worker",
                "inspect",
                "--input",
                os.fspath(source),
                "--output",
                os.fspath(output_dir / "inspection.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        input_dir.chmod(0o700)

    combined_output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "external_relationship\n"
    assert "secret.example.invalid" not in combined_output
    assert "Relationship" not in combined_output
    assert list(output_dir.iterdir()) == []


@pytest.mark.integration
def test_worker_rejects_external_relationship_without_leaking_source_part(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    raw_source_part = "private\nsource"
    source = build_relationship_docx(
        input_dir,
        target="https://secret.example.invalid/egress",
        target_mode="External",
        relationship_part=f"word/_rels/{raw_source_part}.xml.rels",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "specpilot.ingestion.sandbox_worker",
            "inspect",
            "--input",
            os.fspath(source),
            "--output",
            os.fspath(output_dir / "inspection.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    combined_output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert completed.stderr == "external_relationship\n"
    assert raw_source_part not in combined_output
    assert list(output_dir.iterdir()) == []
