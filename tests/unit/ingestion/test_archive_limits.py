from __future__ import annotations

import io
import os
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from specpilot.contracts.archive import (
    ArchivePolicy,
    ArchiveRejectionCode,
    UnsafeArchiveError,
)
from specpilot.ingestion.archive import extract_expected_docx


def policy() -> ArchivePolicy:
    return ArchivePolicy(
        expected_docx_name="expected.docx",
        max_members=1,
        max_member_bytes=1_024,
        max_total_bytes=1_024,
    )


def build_zip(
    tmp_path: Path,
    members: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> Path:
    archive_path = tmp_path / "submission.zip"
    with zipfile.ZipFile(archive_path, "w", compression=compression) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return archive_path


def assert_rejected_before_writes(
    archive: Path,
    tmp_path: Path,
    archive_policy: ArchivePolicy,
    expected_code: ArchiveRejectionCode,
) -> None:
    destination = tmp_path / "corpus"
    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(
            archive,
            destination,
            tmp_path / "quarantine",
            archive_policy,
        )
    assert raised.value.code is expected_code
    assert not destination.exists()


def set_encrypted_flags(archive_path: Path) -> None:
    payload = bytearray(archive_path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        header_offset = payload.find(signature)
        assert header_offset >= 0
        absolute_offset = header_offset + flag_offset
        flags = int.from_bytes(payload[absolute_offset : absolute_offset + 2], "little")
        payload[absolute_offset : absolute_offset + 2] = (flags | 1).to_bytes(
            2, "little"
        )
    archive_path.write_bytes(payload)


def test_member_count_limit_is_checked_before_writes(tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path,
        [("expected.docx", b"expected"), ("extra.txt", b"extra")],
    )

    assert_rejected_before_writes(
        archive,
        tmp_path,
        policy(),
        ArchiveRejectionCode.TOO_MANY_MEMBERS,
    )


def test_declared_member_size_limit_is_checked_before_writes(tmp_path: Path) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"x" * 9)])

    assert_rejected_before_writes(
        archive,
        tmp_path,
        replace(policy(), max_member_bytes=8),
        ArchiveRejectionCode.MEMBER_TOO_LARGE,
    )


def test_total_uncompressed_limit_is_checked_before_writes(tmp_path: Path) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"x" * 9)])

    assert_rejected_before_writes(
        archive,
        tmp_path,
        replace(policy(), max_member_bytes=16, max_total_bytes=8),
        ArchiveRejectionCode.TOTAL_TOO_LARGE,
    )


def test_encrypted_flag_is_rejected(tmp_path: Path) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"payload")])
    set_encrypted_flags(archive)

    assert_rejected_before_writes(
        archive,
        tmp_path,
        policy(),
        ArchiveRejectionCode.ENCRYPTED_MEMBER,
    )


def test_high_compression_ratio_is_rejected(tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path,
        [("expected.docx", b"x" * 1_024)],
        compression=zipfile.ZIP_DEFLATED,
    )

    assert_rejected_before_writes(
        archive,
        tmp_path,
        replace(policy(), max_compression_ratio=2.0),
        ArchiveRejectionCode.COMPRESSION_RATIO_EXCEEDED,
    )


def test_empty_member_does_not_divide_by_zero(tmp_path: Path) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"")])

    result = extract_expected_docx(
        archive,
        tmp_path / "corpus",
        tmp_path / "quarantine",
        policy(),
    )

    assert result.byte_count == 0


def test_streamed_size_limit_catches_misreported_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"x")])

    def oversized_open(*args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(b"x" * 9)

    monkeypatch.setattr(zipfile.ZipFile, "open", oversized_open)
    destination = tmp_path / "corpus"

    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(
            archive,
            destination,
            tmp_path / "quarantine",
            replace(policy(), max_member_bytes=8),
        )

    assert raised.value.code is ArchiveRejectionCode.MEMBER_TOO_LARGE
    assert not destination.exists()
    assert list(tmp_path.glob(".corpus-*")) == []


def test_streamed_size_must_match_declared_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"x")])

    def misreported_open(*args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(b"xx")

    monkeypatch.setattr(zipfile.ZipFile, "open", misreported_open)

    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(
            archive,
            tmp_path / "corpus",
            tmp_path / "quarantine",
            policy(),
        )

    assert raised.value.code is ArchiveRejectionCode.SIZE_MISMATCH


def test_extracted_file_is_fsynced_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"payload")])
    fsynced_descriptors: list[int] = []

    def recording_fsync(file_descriptor: int) -> None:
        os.fstat(file_descriptor)
        fsynced_descriptors.append(file_descriptor)

    monkeypatch.setattr("specpilot.ingestion.archive.os.fsync", recording_fsync)

    extract_expected_docx(
        archive,
        tmp_path / "corpus",
        tmp_path / "quarantine",
        policy(),
    )

    assert len(fsynced_descriptors) == 1


def test_existing_destination_symlink_is_not_replaced_or_followed(
    tmp_path: Path,
) -> None:
    archive = build_zip(tmp_path, [("expected.docx", b"payload")])
    symlink_target = tmp_path / "target"
    symlink_target.mkdir()
    destination = tmp_path / "corpus"
    destination.symlink_to(symlink_target, target_is_directory=True)

    with pytest.raises(FileExistsError):
        extract_expected_docx(
            archive,
            destination,
            tmp_path / "quarantine",
            policy(),
        )

    assert destination.is_symlink()
    assert list(symlink_target.iterdir()) == []
    assert list(tmp_path.glob(".corpus-*")) == []
