from __future__ import annotations

import hashlib
import os
import posixpath
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from specpilot.contracts.archive import (
    ArchivePolicy,
    ArchiveRejectionCode,
    ExtractionResult,
    UnsafeArchiveError,
)
from specpilot.ingestion.quarantine import quarantine_archive

_COPY_CHUNK_BYTES = 64 * 1024
_NESTED_ARCHIVE_SUFFIXES = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tbz2",
    ".tgz",
    ".txz",
    ".xz",
    ".zip",
}


def _snapshot_archive(path: Path, snapshot: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
            byte_count += len(chunk)
            snapshot.write(chunk)
    snapshot.flush()
    os.fsync(snapshot.fileno())
    snapshot.seek(0)
    return digest.hexdigest(), byte_count


def _reject(code: ArchiveRejectionCode) -> None:
    raise UnsafeArchiveError(code)


def _validate_member_path(member_name: str) -> str:
    normalized_separators = member_name.replace("\\", "/")
    windows_path = PureWindowsPath(member_name)
    if normalized_separators.startswith("/") or windows_path.drive:
        _reject(ArchiveRejectionCode.ABSOLUTE_PATH)

    path_parts = normalized_separators.split("/")
    if ".." in path_parts:
        _reject(ArchiveRejectionCode.PATH_TRAVERSAL)
    return posixpath.normpath(normalized_separators)


def _validate_member_type(member: zipfile.ZipInfo) -> None:
    if member.create_system == 0 and member.external_attr & 0x18:
        _reject(ArchiveRejectionCode.SPECIAL_FILE)
    unix_mode = member.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        _reject(ArchiveRejectionCode.SYMLINK)
    if file_type not in {0, stat.S_IFREG}:
        _reject(ArchiveRejectionCode.SPECIAL_FILE)


def _preflight_members(
    members: list[zipfile.ZipInfo],
    policy: ArchivePolicy,
) -> zipfile.ZipInfo:
    if len(members) > policy.max_members:
        _reject(ArchiveRejectionCode.TOO_MANY_MEMBERS)

    accepted: list[zipfile.ZipInfo] = []
    declared_total_bytes = 0
    for member in members:
        normalized_name = _validate_member_path(member.filename)
        _validate_member_type(member)
        if member.flag_bits & 1:
            _reject(ArchiveRejectionCode.ENCRYPTED_MEMBER)
        if member.file_size > policy.max_member_bytes:
            _reject(ArchiveRejectionCode.MEMBER_TOO_LARGE)
        declared_total_bytes += member.file_size
        if declared_total_bytes > policy.max_total_bytes:
            _reject(ArchiveRejectionCode.TOTAL_TOO_LARGE)
        compression_ratio = member.file_size / max(member.compress_size, 1)
        if compression_ratio > policy.max_compression_ratio:
            _reject(ArchiveRejectionCode.COMPRESSION_RATIO_EXCEEDED)
        if Path(normalized_name).suffix.lower() in _NESTED_ARCHIVE_SUFFIXES:
            _reject(ArchiveRejectionCode.NESTED_ARCHIVE)
        if posixpath.basename(normalized_name) != policy.expected_docx_name:
            _reject(ArchiveRejectionCode.UNEXPECTED_MEMBER)
        accepted.append(member)

    if len(accepted) != 1:
        _reject(ArchiveRejectionCode.UNEXPECTED_MEMBER)
    return accepted[0]


def _extract_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    destination: Path,
    policy: ArchivePolicy,
) -> tuple[str, int]:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        output_path = temporary_dir / policy.expected_docx_name
        digest = hashlib.sha256()
        byte_count = 0
        with archive.open(member, "r") as source, output_path.open("xb") as output:
            while chunk := source.read(_COPY_CHUNK_BYTES):
                byte_count += len(chunk)
                if byte_count > policy.max_member_bytes:
                    _reject(ArchiveRejectionCode.MEMBER_TOO_LARGE)
                if byte_count > policy.max_total_bytes:
                    _reject(ArchiveRejectionCode.TOTAL_TOO_LARGE)
                output.write(chunk)
                digest.update(chunk)
            if byte_count != member.file_size:
                _reject(ArchiveRejectionCode.SIZE_MISMATCH)
            output.flush()
            os.fsync(output.fileno())
        os.rename(temporary_dir, destination)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return digest.hexdigest(), byte_count


def extract_expected_docx(
    archive_path: Path,
    destination: Path,
    quarantine_dir: Path,
    policy: ArchivePolicy,
) -> ExtractionResult:
    """Preflight and atomically extract one expected DOCX from an outer ZIP."""
    with tempfile.TemporaryFile(mode="w+b") as snapshot:
        archive_sha256, archive_bytes = _snapshot_archive(archive_path, snapshot)
        try:
            with zipfile.ZipFile(snapshot, "r") as archive:
                member = _preflight_members(archive.infolist(), policy)
                docx_sha256, byte_count = _extract_member(
                    archive,
                    member,
                    destination,
                    policy,
                )
        except zipfile.BadZipFile as error:
            unsafe_error = UnsafeArchiveError(ArchiveRejectionCode.INVALID_ZIP)
            quarantine_archive(
                snapshot,
                quarantine_dir,
                archive_sha256=archive_sha256,
                archive_bytes=archive_bytes,
                rejection_code=unsafe_error.code,
            )
            raise unsafe_error from error
        except UnsafeArchiveError as error:
            quarantine_archive(
                snapshot,
                quarantine_dir,
                archive_sha256=archive_sha256,
                archive_bytes=archive_bytes,
                rejection_code=error.code,
            )
            raise

        return ExtractionResult(
            archive_sha256=archive_sha256,
            docx_sha256=docx_sha256,
            byte_count=byte_count,
            member_name=member.filename,
        )
