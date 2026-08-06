from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArchiveRejectionCode(StrEnum):
    """Stable machine-readable reasons for rejecting a source archive."""

    ABSOLUTE_PATH = "absolute_path"
    PATH_TRAVERSAL = "path_traversal"
    SYMLINK = "symlink"
    SPECIAL_FILE = "special_file"
    UNEXPECTED_MEMBER = "unexpected_member"
    NESTED_ARCHIVE = "nested_archive"
    ENCRYPTED_MEMBER = "encrypted_member"
    TOO_MANY_MEMBERS = "too_many_members"
    MEMBER_TOO_LARGE = "member_too_large"
    TOTAL_TOO_LARGE = "total_too_large"
    COMPRESSION_RATIO_EXCEEDED = "compression_ratio_exceeded"
    SIZE_MISMATCH = "size_mismatch"
    INVALID_ZIP = "invalid_zip"


@dataclass(frozen=True, slots=True)
class ArchivePolicy:
    expected_docx_name: str
    max_members: int
    max_member_bytes: int
    max_total_bytes: int
    max_compression_ratio: float = 100.0


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    archive_sha256: str
    docx_sha256: str
    byte_count: int
    member_name: str


class UnsafeArchiveError(Exception):
    """An archive rejected by a safety policy."""

    def __init__(self, code: ArchiveRejectionCode) -> None:
        self.code = code
        super().__init__(code.value)
