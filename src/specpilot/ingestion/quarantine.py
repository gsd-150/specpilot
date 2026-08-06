from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from specpilot.contracts.archive import ArchiveRejectionCode
from specpilot.ingestion._secure_fs import (
    create_private_file,
    directory_open_flags,
    open_directory_path,
    require_secure_filesystem,
)

_COPY_CHUNK_BYTES = 64 * 1024


@dataclass(slots=True)
class _RecordDirectory:
    path: Path
    name: str
    parent_descriptor: int
    descriptor: int
    device: int
    inode: int

    def revalidate(self) -> None:
        try:
            entry_status = os.stat(
                self.name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError as error:
            raise FileExistsError(self.path) from error
        if (
            not stat.S_ISDIR(entry_status.st_mode)
            or entry_status.st_dev != self.device
            or entry_status.st_ino != self.inode
        ):
            raise FileExistsError(self.path)

    def close(self) -> None:
        os.close(self.descriptor)
        os.close(self.parent_descriptor)


def _open_existing_regular(record_dir: _RecordDirectory, name: str) -> int:
    record_dir.revalidate()
    try:
        file_descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=record_dir.descriptor,
        )
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise FileExistsError(record_dir.path / name) from error
        raise

    file_status = os.fstat(file_descriptor)
    if not stat.S_ISREG(file_status.st_mode) or file_status.st_nlink != 1:
        os.close(file_descriptor)
        raise FileExistsError(record_dir.path / name)
    if stat.S_IMODE(file_status.st_mode) != 0o600:
        os.fchmod(file_descriptor, 0o600)
    return file_descriptor


def _verify_archive(
    record_dir: _RecordDirectory,
    *,
    archive_sha256: str,
    archive_bytes: int,
) -> bool:
    try:
        file_descriptor = _open_existing_regular(record_dir, "archive.zip")
    except FileNotFoundError:
        return False

    digest = hashlib.sha256()
    byte_count = 0
    with os.fdopen(file_descriptor, "rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
            byte_count += len(chunk)
    if byte_count != archive_bytes or digest.hexdigest() != archive_sha256:
        raise FileExistsError(record_dir.path / "archive.zip")
    return True


def _verify_record(record_dir: _RecordDirectory, expected_record: bytes) -> bool:
    try:
        file_descriptor = _open_existing_regular(record_dir, "record.json")
    except FileNotFoundError:
        return False
    with os.fdopen(file_descriptor, "rb") as source:
        if source.read(len(expected_record) + 1) != expected_record:
            raise FileExistsError(record_dir.path / "record.json")
    return True


def _publish_private_file(
    record_dir: _RecordDirectory,
    destination_name: str,
    writer: Callable[[BinaryIO], object],
) -> bool:
    record_dir.revalidate()
    file_descriptor, temporary_name = create_private_file(
        record_dir.descriptor,
        prefix=".quarantine-",
    )
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            writer(temporary_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        record_dir.revalidate()
        try:
            os.link(
                temporary_name,
                destination_name,
                src_dir_fd=record_dir.descriptor,
                dst_dir_fd=record_dir.descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        record_dir.revalidate()
        return True
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=record_dir.descriptor)


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


def _prepare_record_directory(
    quarantine_dir: Path,
    archive_sha256: str,
) -> _RecordDirectory:
    require_secure_filesystem()
    quarantine_descriptor = open_directory_path(quarantine_dir, create=True)
    try:
        with suppress(FileExistsError):
            os.mkdir(archive_sha256, mode=0o700, dir_fd=quarantine_descriptor)
        try:
            record_descriptor = os.open(
                archive_sha256,
                directory_open_flags(),
                dir_fd=quarantine_descriptor,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise FileExistsError(quarantine_dir / archive_sha256) from error
            raise
        record_status = os.fstat(record_descriptor)
        if not stat.S_ISDIR(record_status.st_mode):
            os.close(record_descriptor)
            raise FileExistsError(quarantine_dir / archive_sha256)
        os.fchmod(record_descriptor, 0o700)
        return _RecordDirectory(
            path=quarantine_dir / archive_sha256,
            name=archive_sha256,
            parent_descriptor=quarantine_descriptor,
            descriptor=record_descriptor,
            device=record_status.st_dev,
            inode=record_status.st_ino,
        )
    except BaseException:
        os.close(quarantine_descriptor)
        raise


def quarantine_archive(
    archive_source: Path | BinaryIO,
    quarantine_dir: Path,
    *,
    archive_sha256: str,
    archive_bytes: int,
    rejection_code: ArchiveRejectionCode,
) -> Path:
    """Atomically store a rejected archive and content-free rejection record."""
    require_secure_filesystem()
    record_dir = _prepare_record_directory(quarantine_dir, archive_sha256)
    try:
        record_dir.revalidate()
        if not _verify_archive(
            record_dir,
            archive_sha256=archive_sha256,
            archive_bytes=archive_bytes,
        ):
            _publish_private_file(
                record_dir,
                "archive.zip",
                lambda destination: _write_archive(
                    archive_source,
                    destination,
                    archive_sha256=archive_sha256,
                    archive_bytes=archive_bytes,
                ),
            )
            _verify_archive(
                record_dir,
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
        record_path = record_dir.path / "record.json"
        if not _verify_record(record_dir, expected_record):
            _publish_private_file(
                record_dir,
                "record.json",
                lambda output: output.write(expected_record),
            )
            _verify_record(record_dir, expected_record)
        record_dir.revalidate()
        return record_path
    finally:
        record_dir.close()
