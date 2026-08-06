from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path

_SECURE_FILESYSTEM_ERROR = "required secure filesystem primitives unavailable"
_DIR_FD_FUNCTION_NAMES = (
    "link",
    "mkdir",
    "open",
    "rename",
    "rmdir",
    "stat",
    "unlink",
)


def require_secure_filesystem() -> None:
    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise RuntimeError(_SECURE_FILESYSTEM_ERROR)
    if not hasattr(os, "supports_dir_fd") or not hasattr(
        os, "supports_follow_symlinks"
    ):
        raise RuntimeError(_SECURE_FILESYSTEM_ERROR)
    required_functions = []
    for function_name in _DIR_FD_FUNCTION_NAMES:
        function = getattr(os, function_name, None)
        if function is None:
            raise RuntimeError(_SECURE_FILESYSTEM_ERROR)
        required_functions.append(function)
    if any(function not in os.supports_dir_fd for function in required_functions):
        raise RuntimeError(_SECURE_FILESYSTEM_ERROR)
    if os.stat not in os.supports_follow_symlinks:
        raise RuntimeError(_SECURE_FILESYSTEM_ERROR)
    if os.link not in os.supports_follow_symlinks:
        raise RuntimeError(_SECURE_FILESYSTEM_ERROR)


def directory_open_flags() -> int:
    require_secure_filesystem()
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_directory_path(path: Path, *, create: bool) -> int:
    """Open a directory path component-by-component without following links."""
    require_secure_filesystem()
    if path.is_absolute():
        directory_descriptor = os.open("/", directory_open_flags())
        components = path.parts[1:]
    else:
        directory_descriptor = os.open(".", directory_open_flags())
        components = path.parts

    try:
        for component in components:
            if component in {"", "."}:
                continue
            try:
                child_descriptor = os.open(
                    component,
                    directory_open_flags(),
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError:
                if not create:
                    raise
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=directory_descriptor)
                child_descriptor = os.open(
                    component,
                    directory_open_flags(),
                    dir_fd=directory_descriptor,
                )
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise FileExistsError(path) from error
                raise
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
    except BaseException:
        os.close(directory_descriptor)
        raise
    return directory_descriptor


def revalidate_directory_path(path: Path, directory_descriptor: int) -> None:
    """Confirm a pathname still names the pinned directory inode."""
    try:
        current_descriptor = open_directory_path(path, create=False)
    except (FileNotFoundError, FileExistsError) as error:
        raise FileExistsError(path) from error
    try:
        pinned_status = os.fstat(directory_descriptor)
        current_status = os.fstat(current_descriptor)
        if (
            not stat.S_ISDIR(current_status.st_mode)
            or current_status.st_dev != pinned_status.st_dev
            or current_status.st_ino != pinned_status.st_ino
        ):
            raise FileExistsError(path)
    finally:
        os.close(current_descriptor)


def create_private_file(directory_descriptor: int, *, prefix: str) -> tuple[int, str]:
    require_secure_filesystem()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(128):
        name = f"{prefix}{secrets.token_hex(12)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_descriptor), name
        except FileExistsError:
            continue
    raise FileExistsError("unable to allocate private temporary file")


def create_private_directory(
    directory_descriptor: int,
    *,
    prefix: str,
) -> tuple[int, str]:
    require_secure_filesystem()
    for _ in range(128):
        name = f"{prefix}{secrets.token_hex(12)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory_descriptor)
        except FileExistsError:
            continue
        try:
            child_descriptor = os.open(
                name,
                directory_open_flags(),
                dir_fd=directory_descriptor,
            )
        except BaseException:
            os.rmdir(name, dir_fd=directory_descriptor)
            raise
        child_status = os.fstat(child_descriptor)
        if not stat.S_ISDIR(child_status.st_mode):
            os.close(child_descriptor)
            os.rmdir(name, dir_fd=directory_descriptor)
            raise FileExistsError(name)
        return child_descriptor, name
    raise FileExistsError("unable to allocate private temporary directory")
