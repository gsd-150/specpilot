from __future__ import annotations

import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.helpers.ooxml_factory import build_docx

# Strings that exist inside the synthetic fixtures. None of them may appear on
# stdout or stderr: the CLI reports identifiers and stable codes, never content.
FIXTURE_CONTENT_MARKERS = (
    "secret.example.invalid",
    "vbaProject",
    "oleObject",
    "wordprocessingml",
    "<?xml",
)


@pytest.fixture
def run_cli():
    """Invoke the CLI in-process and capture stdout, stderr, and exit code."""
    from specpilot.cli import main

    def invoke(argv: Sequence[str], capsys: pytest.CaptureFixture[str]):
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


def parse_stdout(out: str) -> dict[str, object]:
    payload = json.loads(out)
    assert isinstance(payload, dict)
    return payload


def build_submission(
    tmp_path: Path,
    *,
    mutation: str = "safe",
    member_name: str = "expected.docx",
) -> Path:
    """Wrap a synthetic DOCX in the outer ZIP shape the pipeline expects."""
    inner = tmp_path / "inner"
    inner.mkdir(parents=True, exist_ok=True)
    docx = build_docx(inner, mutation, name="inner.docx")
    archive_path = tmp_path / "submission.zip"
    member = zipfile.ZipInfo(member_name)
    member.create_system = 3
    member.external_attr = 0o100600 << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, docx.read_bytes())
    return archive_path
