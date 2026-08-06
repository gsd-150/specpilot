from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from specpilot.contracts.archive import (
    ArchivePolicy,
    ArchiveRejectionCode,
    UnsafeArchiveError,
)
from specpilot.ingestion.archive import extract_expected_docx

_SENSITIVE_PAYLOAD = b"<document><secret>do not log me</secret></document>"


def policy() -> ArchivePolicy:
    return ArchivePolicy(
        expected_docx_name="expected.docx",
        max_members=1,
        max_member_bytes=1_024,
        max_total_bytes=1_024,
    )


def build_rejected_archive(tmp_path: Path) -> Path:
    archive_path = tmp_path / "submission.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("payload.exe", _SENSITIVE_PAYLOAD)
    return archive_path


def reject(archive: Path, tmp_path: Path) -> UnsafeArchiveError:
    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(
            archive,
            tmp_path / "corpus",
            tmp_path / "quarantine",
            policy(),
        )
    return raised.value


def test_quarantine_is_content_addressed_private_and_content_preserving(
    tmp_path: Path,
) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_bytes = archive.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()

    reject(archive, tmp_path)

    record_dir = tmp_path / "quarantine" / archive_sha256
    stored_archive = record_dir / "archive.zip"
    record_path = record_dir / "record.json"
    assert stored_archive.read_bytes() == archive_bytes
    assert stat.S_IMODE(stored_archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600


def test_quarantine_record_contains_only_safe_metadata(tmp_path: Path) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    error = reject(archive, tmp_path)

    record_path = tmp_path / "quarantine" / archive_sha256 / "record.json"
    record = json.loads(record_path.read_text())
    assert record == {
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": archive_sha256,
        "rejection_code": ArchiveRejectionCode.UNEXPECTED_MEMBER,
    }
    assert _SENSITIVE_PAYLOAD.decode() not in record_path.read_text()
    assert _SENSITIVE_PAYLOAD.decode() not in str(error)


def test_quarantine_replay_is_idempotent_for_identical_archive(
    tmp_path: Path,
) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    reject(archive, tmp_path)
    record_dir = tmp_path / "quarantine" / archive_sha256
    stored_archive = record_dir / "archive.zip"
    record_path = record_dir / "record.json"
    fixed_timestamp_ns = 1_700_000_000_000_000_000
    os.utime(stored_archive, ns=(fixed_timestamp_ns, fixed_timestamp_ns))
    os.utime(record_path, ns=(fixed_timestamp_ns, fixed_timestamp_ns))

    reject(archive, tmp_path)

    assert sorted(path.name for path in record_dir.iterdir()) == [
        "archive.zip",
        "record.json",
    ]
    assert stored_archive.stat().st_mtime_ns == fixed_timestamp_ns
    assert record_path.stat().st_mtime_ns == fixed_timestamp_ns
