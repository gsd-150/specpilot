from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from specpilot.contracts.archive import ArchiveRejectionCode

_COPY_CHUNK_BYTES = 64 * 1024


def _open_existing_regular(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise FileExistsError(path) from error
        raise

    file_status = os.fstat(file_descriptor)
    if not stat.S_ISREG(file_status.st_mode) or file_status.st_nlink != 1:
        os.close(file_descriptor)
        raise FileExistsError(path)
    if stat.S_IMODE(file_status.st_mode) != 0o600:
        os.fchmod(file_descriptor, 0o600)
    return file_descriptor


def _verify_archive(
    stored_archive: Path,
    *,
    archive_sha256: str,
    archive_bytes: int,
) -> bool:
    try:
        file_descriptor = _open_existing_regular(stored_archive)
    except FileNotFoundError:
        return False

    digest = hashlib.sha256()
    byte_count = 0
    with os.fdopen(file_descriptor, "rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
            byte_count += len(chunk)
    if byte_count != archive_bytes or digest.hexdigest() != archive_sha256:
        raise FileExistsError(stored_archive)
    return True


def _verify_record(record_path: Path, expected_record: bytes) -> bool:
    try:
        file_descriptor = _open_existing_regular(record_path)
    except FileNotFoundError:
        return False
    with os.fdopen(file_descriptor, "rb") as source:
        if source.read(len(expected_record) + 1) != expected_record:
            raise FileExistsError(record_path)
    return True


def _publish_private_file(
    record_dir: Path,
    destination: Path,
    writer: Callable[[BinaryIO], object],
) -> bool:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".quarantine-", dir=record_dir
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            writer(temporary_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            return False
        return True
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_archive(
    archive_source: Path | BinaryIO,
    destination: BinaryIO,
    *,
    archive_sha256: str,
    archive_bytes: int,
) -> None:
    digest = hashlib.sha256()
    byte_count = 0
    source_context: BinaryIO
    if isinstance(archive_source, Path):
        source_context = archive_source.open("rb")
    else:
        archive_source.seek(0)
        source_context = archive_source

    try:
        while chunk := source_context.read(_COPY_CHUNK_BYTES):
            destination.write(chunk)
            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        if isinstance(archive_source, Path):
            source_context.close()

    if byte_count != archive_bytes or digest.hexdigest() != archive_sha256:
        raise ValueError("archive source changed before quarantine")


def _prepare_record_directory(quarantine_dir: Path, archive_sha256: str) -> Path:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    if quarantine_dir.is_symlink():
        raise FileExistsError(quarantine_dir)
    record_dir = quarantine_dir / archive_sha256
    record_dir.mkdir(mode=0o700, exist_ok=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_descriptor = os.open(record_dir, directory_flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise FileExistsError(record_dir) from error
        raise
    try:
        if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
            raise FileExistsError(record_dir)
        os.fchmod(directory_descriptor, 0o700)
    finally:
        os.close(directory_descriptor)
    return record_dir


def quarantine_archive(
    archive_source: Path | BinaryIO,
    quarantine_dir: Path,
    *,
    archive_sha256: str,
    archive_bytes: int,
    rejection_code: ArchiveRejectionCode,
) -> Path:
    """Atomically store a rejected archive and content-free rejection record."""
    record_dir = _prepare_record_directory(quarantine_dir, archive_sha256)
    stored_archive = record_dir / "archive.zip"
    if not _verify_archive(
        stored_archive,
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
    ):
        _publish_private_file(
            record_dir,
            stored_archive,
            lambda destination: _write_archive(
                archive_source,
                destination,
                archive_sha256=archive_sha256,
                archive_bytes=archive_bytes,
            ),
        )
        _verify_archive(
            stored_archive,
            archive_sha256=archive_sha256,
            archive_bytes=archive_bytes,
        )

    expected_record = (
        json.dumps(
            {
                "archive_bytes": archive_bytes,
                "archive_sha256": archive_sha256,
                "rejection_code": rejection_code,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    record_path = record_dir / "record.json"
    if not _verify_record(record_path, expected_record):
        _publish_private_file(
            record_dir,
            record_path,
            lambda output: output.write(expected_record),
        )
        _verify_record(record_path, expected_record)
    return record_path
