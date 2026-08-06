from __future__ import annotations

import hashlib
import importlib
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
from specpilot.ingestion.quarantine import quarantine_archive

quarantine_module = importlib.import_module("specpilot.ingestion.quarantine")

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


def test_quarantine_refuses_a_preexisting_archive_symlink(tmp_path: Path) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_bytes = archive.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    record_dir = tmp_path / "quarantine" / archive_sha256
    record_dir.mkdir(parents=True)
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"do not touch")
    victim.chmod(0o644)
    (record_dir / "archive.zip").symlink_to(victim)

    with pytest.raises(FileExistsError):
        quarantine_archive(
            archive,
            tmp_path / "quarantine",
            archive_sha256=archive_sha256,
            archive_bytes=len(archive_bytes),
            rejection_code=ArchiveRejectionCode.UNEXPECTED_MEMBER,
        )

    assert victim.read_bytes() == b"do not touch"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_quarantine_refuses_a_partial_existing_archive(tmp_path: Path) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_bytes = archive.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    record_dir = tmp_path / "quarantine" / archive_sha256
    record_dir.mkdir(parents=True)
    stored_archive = record_dir / "archive.zip"
    stored_archive.write_bytes(b"partial")
    stored_archive.chmod(0o600)

    with pytest.raises(FileExistsError):
        quarantine_archive(
            archive,
            tmp_path / "quarantine",
            archive_sha256=archive_sha256,
            archive_bytes=len(archive_bytes),
            rejection_code=ArchiveRejectionCode.UNEXPECTED_MEMBER,
        )

    assert stored_archive.read_bytes() == b"partial"


def test_quarantine_publishes_private_files_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_bytes = archive.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    published_modes: list[int] = []
    original_link = os.link

    def recording_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        source_status = os.stat(
            source,
            dir_fd=src_dir_fd,
            follow_symlinks=False,
        )
        published_modes.append(stat.S_IMODE(source_status.st_mode))
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr("specpilot.ingestion.quarantine.os.link", recording_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {recording_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {recording_link},
    )

    quarantine_archive(
        archive,
        tmp_path / "quarantine",
        archive_sha256=archive_sha256,
        archive_bytes=len(archive_bytes),
        rejection_code=ArchiveRejectionCode.UNEXPECTED_MEMBER,
    )

    assert published_modes == [0o600, 0o600]


def test_quarantine_directory_swap_cannot_redirect_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_bytes = archive.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    quarantine_dir = tmp_path / "quarantine"
    outside = tmp_path / "outside"
    outside.mkdir()
    moved_record_dir = tmp_path / "validated-record-dir"
    original_prepare = quarantine_module._prepare_record_directory

    def swapping_prepare(directory: Path, digest: str) -> object:
        prepared = original_prepare(directory, digest)
        record_dir = directory / digest
        record_dir.rename(moved_record_dir)
        record_dir.symlink_to(outside, target_is_directory=True)
        return prepared

    monkeypatch.setattr(
        quarantine_module,
        "_prepare_record_directory",
        swapping_prepare,
    )

    with pytest.raises(FileExistsError):
        quarantine_archive(
            archive,
            quarantine_dir,
            archive_sha256=archive_sha256,
            archive_bytes=len(archive_bytes),
            rejection_code=ArchiveRejectionCode.UNEXPECTED_MEMBER,
        )

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("missing_primitive", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_quarantine_fails_closed_without_secure_filesystem_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_primitive: str,
) -> None:
    archive = build_rejected_archive(tmp_path)
    archive_bytes = archive.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    quarantine_dir = tmp_path / "quarantine"
    monkeypatch.delattr(os, missing_primitive)

    with pytest.raises(RuntimeError, match="secure filesystem primitives"):
        quarantine_archive(
            archive,
            quarantine_dir,
            archive_sha256=archive_sha256,
            archive_bytes=len(archive_bytes),
            rejection_code=ArchiveRejectionCode.UNEXPECTED_MEMBER,
        )

    assert not quarantine_dir.exists()
