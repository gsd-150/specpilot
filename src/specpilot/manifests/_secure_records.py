from __future__ import annotations

import errno
import os
import re
import stat
from pathlib import Path
from types import TracebackType
from typing import Self

from specpilot.ingestion._secure_fs import (
    create_private_file,
    open_directory_path,
    revalidate_directory_path,
)

_CONTENT_FILE = re.compile(r"^([0-9a-f]{64})\.json$")
_MAX_ENUMERATED_RECORD_BYTES = 256 * 1024
_FileIdentity = tuple[int, int]


class SecureRecordDirectory:
    """Descriptor-pinned storage for private, create-only byte records."""

    def __init__(
        self,
        path: Path,
        fd: int,
        *,
        close_fd: bool,
    ) -> None:
        self.path = path
        self.fd = fd
        self._close_fd = close_fd
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, create: bool) -> Self:
        fd = open_directory_path(path, create=create)
        try:
            if create:
                os.fchmod(fd, 0o700)
            cls._validate_root(path, fd)
            return cls(path, fd, close_fd=True)
        except BaseException:
            os.close(fd)
            raise

    @classmethod
    def from_fd(
        cls,
        path: Path,
        fd: int,
        *,
        close_fd: bool = False,
    ) -> Self:
        try:
            cls._validate_root(path, fd)
            return cls(path, fd, close_fd=close_fd)
        except BaseException:
            if close_fd:
                os.close(fd)
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._close_fd and not self._closed:
            os.close(self.fd)
            self._closed = True

    def content_ids(
        self,
        *,
        allowed_non_records: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        self._revalidate_root()
        identifiers: list[str] = []
        for name in os.listdir(self.fd):
            match = _CONTENT_FILE.fullmatch(name)
            if match is not None:
                self.read(name, max_bytes=_MAX_ENUMERATED_RECORD_BYTES)
                identifiers.append(match.group(1))
            elif name.startswith(".manifest-"):
                self.read(name, max_bytes=_MAX_ENUMERATED_RECORD_BYTES)
            elif name not in allowed_non_records:
                raise FileExistsError(self.path / name)
        self._revalidate_root()
        return tuple(sorted(identifiers))

    def read(self, name: str, *, max_bytes: int) -> bytes:
        self._validate_read(name, max_bytes=max_bytes)
        self._revalidate_root()
        nonblocking_flag = getattr(os, "O_NONBLOCK", 0)
        if not nonblocking_flag:
            raise RuntimeError("required secure filesystem primitives unavailable")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | nonblocking_flag,
                dir_fd=self.fd,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.EISDIR}:
                raise FileExistsError(self.path / name) from error
            raise

        try:
            opened = os.fstat(descriptor)
            self._validate_file_status(name, opened, max_bytes=max_bytes)
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                data = source.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise FileExistsError(self.path / name)

            opened_after_read = os.fstat(descriptor)
            self._validate_file_status(
                name,
                opened_after_read,
                max_bytes=max_bytes,
            )
            if (
                opened_after_read.st_dev != opened.st_dev
                or opened_after_read.st_ino != opened.st_ino
                or opened_after_read.st_size != len(data)
            ):
                raise FileExistsError(self.path / name)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            self._validate_file_status(name, named, max_bytes=max_bytes)
            if named.st_dev != opened.st_dev or named.st_ino != opened.st_ino:
                raise FileExistsError(self.path / name)
            self._revalidate_root()
            return data
        finally:
            os.close(descriptor)

    def publish(self, name: str, data: bytes, *, max_bytes: int) -> bytes:
        if (
            max_bytes < 0
            or len(data) > max_bytes
            or _CONTENT_FILE.fullmatch(name) is None
        ):
            raise ValueError("invalid secure record publication")

        self._revalidate_root()
        temporary_name: str | None = None
        temporary_descriptor: int | None = None
        temporary_identity: _FileIdentity | None = None
        published = False
        try:
            temporary_descriptor, temporary_name = create_private_file(
                self.fd,
                prefix=".manifest-",
            )
            temporary_status = os.fstat(temporary_descriptor)
            temporary_identity = self._file_identity(temporary_status)
            self._validate_file_status(
                temporary_name,
                temporary_status,
                max_bytes=max_bytes,
            )
            remaining = memoryview(data)
            while remaining:
                written = os.write(temporary_descriptor, remaining)
                if written <= 0:
                    raise OSError("unable to write secure record")
                remaining = remaining[written:]
            os.fsync(temporary_descriptor)
            written_status = os.fstat(temporary_descriptor)
            self._validate_file_status(
                temporary_name,
                written_status,
                max_bytes=max_bytes,
            )
            if (
                self._file_identity(written_status) != temporary_identity
                or written_status.st_size != len(data)
            ):
                raise FileExistsError(self.path / temporary_name)

            self._revalidate_root()
            self._require_named_identity(
                temporary_name,
                temporary_identity,
                links=1,
                max_bytes=max_bytes,
            )
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=self.fd,
                    dst_dir_fd=self.fd,
                    follow_symlinks=False,
                )
                published = True
            except FileExistsError:
                pass
            if published:
                self._require_named_identity(
                    name,
                    temporary_identity,
                    links=2,
                    max_bytes=max_bytes,
                )
                self._require_named_identity(
                    temporary_name,
                    temporary_identity,
                    links=2,
                    max_bytes=max_bytes,
                )
            self._revalidate_root()

            expected_links = 2 if published else 1
            if not self._unlink_if_identity(
                temporary_name,
                temporary_identity,
                links=expected_links,
                max_bytes=max_bytes,
            ):
                raise FileExistsError(self.path / temporary_name)
            temporary_name = None
            os.close(temporary_descriptor)
            temporary_descriptor = None
            self._revalidate_root()

            if published:
                self._require_named_identity(
                    name,
                    temporary_identity,
                    links=1,
                    max_bytes=max_bytes,
                )
            stored = self.read(name, max_bytes=max_bytes)
            if stored != data:
                raise FileExistsError(self.path / name)
            if published:
                self._require_named_identity(
                    name,
                    temporary_identity,
                    links=1,
                    max_bytes=max_bytes,
                )
            self._revalidate_root()
            return stored
        except BaseException:
            if published and temporary_identity is not None:
                self._unlink_if_identity(name, temporary_identity)
            raise
        finally:
            try:
                if temporary_name is not None and temporary_identity is not None:
                    self._unlink_if_identity(temporary_name, temporary_identity)
            finally:
                if temporary_descriptor is not None:
                    os.close(temporary_descriptor)

    @staticmethod
    def _validate_root(path: Path, fd: int) -> None:
        status = os.fstat(fd)
        if not stat.S_ISDIR(status.st_mode) or stat.S_IMODE(status.st_mode) != 0o700:
            raise PermissionError(path)
        revalidate_directory_path(path, fd)

    def _revalidate_root(self) -> None:
        self._validate_root(self.path, self.fd)

    def _validate_file_status(
        self,
        name: str,
        status: os.stat_result,
        *,
        max_bytes: int,
        links: int = 1,
    ) -> None:
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_nlink != links
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_size > max_bytes
        ):
            raise FileExistsError(self.path / name)

    @staticmethod
    def _file_identity(status: os.stat_result) -> _FileIdentity:
        return status.st_dev, status.st_ino

    def _require_named_identity(
        self,
        name: str,
        expected: _FileIdentity,
        *,
        links: int,
        max_bytes: int,
    ) -> None:
        try:
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except OSError as error:
            raise FileExistsError(self.path / name) from error
        self._validate_file_status(
            name,
            named,
            max_bytes=max_bytes,
            links=links,
        )
        if self._file_identity(named) != expected:
            raise FileExistsError(self.path / name)

    def _unlink_if_identity(
        self,
        name: str,
        expected: _FileIdentity,
        *,
        links: int | None = None,
        max_bytes: int | None = None,
    ) -> bool:
        try:
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if self._file_identity(named) != expected:
                return False
            if links is not None and max_bytes is not None:
                self._validate_file_status(
                    name,
                    named,
                    max_bytes=max_bytes,
                    links=links,
                )
            os.unlink(name, dir_fd=self.fd)
        except FileNotFoundError:
            return False
        os.fsync(self.fd)
        return True

    @staticmethod
    def _validate_read(name: str, *, max_bytes: int) -> None:
        if (
            max_bytes < 0
            or "/" in name
            or name in {"", ".", ".."}
            or (
                _CONTENT_FILE.fullmatch(name) is None
                and not name.startswith(".manifest-")
            )
        ):
            raise ValueError("invalid secure record name or size limit")
