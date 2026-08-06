from __future__ import annotations

import errno
import hashlib
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import BinaryIO, NoReturn, cast
from urllib.parse import unquote_to_bytes, urlsplit
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as DefusedElementTree  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]

from specpilot.ingestion._secure_fs import (
    open_directory_path,
    require_secure_filesystem,
    revalidate_directory_path,
)

_COPY_CHUNK_BYTES = 64 * 1024
_CONTENT_TYPES_PART = "[Content_Types].xml"
_RELATIONSHIPS_SUFFIX = ".rels"
_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".hta",
    ".js",
    ".jse",
    ".msi",
    ".msp",
    ".ps1",
    ".scr",
    ".vbe",
    ".vbs",
    ".wsf",
}
_NESTED_PACKAGE_SUFFIXES = {
    ".docm",
    ".docx",
    ".dotm",
    ".dotx",
    ".pptm",
    ".pptx",
    ".xlsm",
    ".xlsx",
    ".zip",
}
_SAFE_RELATIONSHIP_ID = re.compile(r"rId[1-9][0-9]{0,9}\Z")
_SAFE_RELATIONSHIP_TYPE = re.compile(
    r"http://(?:schemas\.openxmlformats\.org/officeDocument/2006|"
    r"purl\.oclc\.org/ooxml/officeDocument|"
    r"schemas\.microsoft\.com/office/2006)/relationships/"
    r"[A-Za-z0-9._-]{1,128}\Z"
)
_UNSAFE_PERCENT_ESCAPE = re.compile(
    r"%(?:0[0-9a-f]|1[0-9a-f]|25|2e|2f|3a|5c|7f)",
    re.IGNORECASE,
)


class OoxmlRejectionCode(StrEnum):
    """Stable machine-readable reasons for rejecting an OOXML package."""

    INVALID_ZIP = "invalid_zip"
    INPUT_CHANGED = "input_changed"
    INVALID_MEMBER_PATH = "invalid_member_path"
    ABSOLUTE_PATH = "absolute_path"
    PATH_TRAVERSAL = "path_traversal"
    SYMLINK = "symlink"
    SPECIAL_FILE = "special_file"
    ENCRYPTED_MEMBER = "encrypted_member"
    DUPLICATE_MEMBER = "duplicate_member"
    TOO_MANY_MEMBERS = "too_many_members"
    MEMBER_TOO_LARGE = "member_too_large"
    TOTAL_TOO_LARGE = "total_too_large"
    XML_TOO_LARGE = "xml_too_large"
    SIZE_MISMATCH = "size_mismatch"
    MISSING_CONTENT_TYPES = "missing_content_types"
    INVALID_XML = "invalid_xml"
    UNSAFE_XML = "unsafe_xml"
    MACRO = "macro"
    EMBEDDED_ACTIVE_CONTENT = "embedded_active_content"
    NESTED_PACKAGE = "nested_package"
    EXTERNAL_RELATIONSHIP = "external_relationship"
    INVALID_RELATIONSHIP_TARGET = "invalid_relationship_target"
    INVALID_LIMITS = "invalid_limits"


@dataclass(frozen=True, slots=True)
class OoxmlLimits:
    max_members: int = 4096
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_xml_bytes: int = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExternalRelationship:
    source_part_sha256: str
    relationship_id: str | None
    relationship_type: str | None
    target_sha256: str


@dataclass(frozen=True, slots=True)
class OoxmlInspection:
    package_sha256: str
    package_bytes: int
    member_count: int
    relationship_count: int
    external_relationships: tuple[ExternalRelationship, ...] = ()


class UnsafeOoxmlError(Exception):
    """An OOXML package rejected by the fail-closed inspection policy."""

    def __init__(
        self,
        code: OoxmlRejectionCode,
        *,
        external_relationships: tuple[ExternalRelationship, ...] = (),
    ) -> None:
        self.code = code
        self.external_relationships = external_relationships
        super().__init__(code.value)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code.value!r}, "
            f"external_relationship_count={len(self.external_relationships)})"
        )


def _reject(
    code: OoxmlRejectionCode,
    *,
    external_relationships: tuple[ExternalRelationship, ...] = (),
) -> NoReturn:
    raise UnsafeOoxmlError(
        code,
        external_relationships=external_relationships,
    )


def _validate_limits(limits: OoxmlLimits) -> None:
    if (
        limits.max_members <= 0
        or limits.max_member_bytes <= 0
        or limits.max_total_bytes <= 0
        or limits.max_xml_bytes <= 0
    ):
        _reject(OoxmlRejectionCode.INVALID_LIMITS)


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _verify_source_unchanged(
    source_name: str,
    parent_descriptor: int,
    source_descriptor: int,
    initial_status: os.stat_result,
) -> None:
    if not _same_file_state(initial_status, os.fstat(source_descriptor)):
        _reject(OoxmlRejectionCode.INPUT_CHANGED)
    try:
        current_status = os.stat(
            source_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        _reject(OoxmlRejectionCode.INPUT_CHANGED)
    if not _same_file_state(initial_status, current_status):
        _reject(OoxmlRejectionCode.INPUT_CHANGED)


def _snapshot_source(
    source_descriptor: int,
    snapshot: BinaryIO,
    initial_status: os.stat_result,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    while chunk := os.read(source_descriptor, _COPY_CHUNK_BYTES):
        snapshot.write(chunk)
        digest.update(chunk)
        byte_count += len(chunk)
    snapshot.flush()
    snapshot.seek(0)
    if byte_count != initial_status.st_size:
        _reject(OoxmlRejectionCode.INPUT_CHANGED)
    return digest.hexdigest(), byte_count


def _validate_member_path(member_name: str) -> str:
    if not member_name or "\x00" in member_name:
        _reject(OoxmlRejectionCode.INVALID_MEMBER_PATH)
    normalized_separators = member_name.replace("\\", "/")
    windows_path = PureWindowsPath(member_name)
    if normalized_separators.startswith("/") or windows_path.drive:
        _reject(OoxmlRejectionCode.ABSOLUTE_PATH)
    path_parts = normalized_separators.split("/")
    if ".." in path_parts:
        _reject(OoxmlRejectionCode.PATH_TRAVERSAL)
    normalized_name = posixpath.normpath(normalized_separators)
    if normalized_name in {"", "."} or normalized_name.startswith("../"):
        _reject(OoxmlRejectionCode.INVALID_MEMBER_PATH)
    return normalized_name


def _validate_member_type(member: zipfile.ZipInfo) -> None:
    if member.create_system == 0:
        dos_attributes = member.external_attr & 0xFF
        if dos_attributes & 0x08:
            _reject(OoxmlRejectionCode.SPECIAL_FILE)
        has_directory_attribute = bool(dos_attributes & 0x10)
        if has_directory_attribute != member.is_dir():
            _reject(OoxmlRejectionCode.SPECIAL_FILE)
        return
    unix_mode = member.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        _reject(OoxmlRejectionCode.SYMLINK)
    if member.is_dir() and file_type in {0, stat.S_IFDIR}:
        return
    if file_type not in {0, stat.S_IFREG}:
        _reject(OoxmlRejectionCode.SPECIAL_FILE)


def _classify_member_name(normalized_name: str) -> None:
    lower_name = normalized_name.lower()
    basename = posixpath.basename(lower_name)
    suffix = posixpath.splitext(basename)[1]
    components = lower_name.split("/")
    if basename == "vbaproject.bin" or "vba" in basename:
        _reject(OoxmlRejectionCode.MACRO)
    if suffix in _NESTED_PACKAGE_SUFFIXES:
        _reject(OoxmlRejectionCode.NESTED_PACKAGE)
    if (
        suffix in _EXECUTABLE_SUFFIXES
        or "activex" in components
        or "embeddings" in components
        or basename.startswith("oleobject")
    ):
        _reject(OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT)


def _preflight_members(
    members: list[zipfile.ZipInfo],
    limits: OoxmlLimits,
) -> dict[str, zipfile.ZipInfo]:
    if len(members) > limits.max_members:
        _reject(OoxmlRejectionCode.TOO_MANY_MEMBERS)
    accepted: dict[str, zipfile.ZipInfo] = {}
    declared_total = 0
    for member in members:
        normalized_name = _validate_member_path(member.filename)
        _validate_member_type(member)
        if normalized_name in accepted:
            _reject(OoxmlRejectionCode.DUPLICATE_MEMBER)
        if member.flag_bits & 1:
            _reject(OoxmlRejectionCode.ENCRYPTED_MEMBER)
        if member.file_size > limits.max_member_bytes:
            _reject(OoxmlRejectionCode.MEMBER_TOO_LARGE)
        if (
            normalized_name.lower().endswith((".xml", _RELATIONSHIPS_SUFFIX))
            and member.file_size > limits.max_xml_bytes
        ):
            _reject(OoxmlRejectionCode.XML_TOO_LARGE)
        declared_total += member.file_size
        if declared_total > limits.max_total_bytes:
            _reject(OoxmlRejectionCode.TOTAL_TOO_LARGE)
        if not member.is_dir():
            _classify_member_name(normalized_name)
        accepted[normalized_name] = member
    content_types_member = accepted.get(_CONTENT_TYPES_PART)
    if content_types_member is None or content_types_member.is_dir():
        _reject(OoxmlRejectionCode.MISSING_CONTENT_TYPES)
    return accepted


def _read_and_validate_members(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    limits: OoxmlLimits,
) -> dict[str, bytes]:
    xml_payloads: dict[str, bytes] = {}
    actual_total = 0
    for normalized_name, member in members.items():
        if member.is_dir():
            continue
        payload = bytearray()
        actual_member_bytes = 0
        should_capture = (
            normalized_name == _CONTENT_TYPES_PART
            or normalized_name.lower().endswith(_RELATIONSHIPS_SUFFIX)
        )
        try:
            with archive.open(member, "r") as source:
                while chunk := source.read(_COPY_CHUNK_BYTES):
                    actual_member_bytes += len(chunk)
                    actual_total += len(chunk)
                    if actual_member_bytes > limits.max_member_bytes:
                        _reject(OoxmlRejectionCode.MEMBER_TOO_LARGE)
                    if actual_total > limits.max_total_bytes:
                        _reject(OoxmlRejectionCode.TOTAL_TOO_LARGE)
                    if (
                        normalized_name.lower().endswith(
                            (".xml", _RELATIONSHIPS_SUFFIX)
                        )
                        and actual_member_bytes > limits.max_xml_bytes
                    ):
                        _reject(OoxmlRejectionCode.XML_TOO_LARGE)
                    if should_capture:
                        payload.extend(chunk)
        except zipfile.BadZipFile as error:
            raise UnsafeOoxmlError(OoxmlRejectionCode.INVALID_ZIP) from error
        if actual_member_bytes != member.file_size:
            _reject(OoxmlRejectionCode.SIZE_MISMATCH)
        if should_capture:
            xml_payloads[normalized_name] = bytes(payload)
    return xml_payloads


def _parse_xml(payload: bytes) -> Element[str]:
    rejection_code: OoxmlRejectionCode | None = None
    parsed: object | None = None
    try:
        parsed = DefusedElementTree.fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except DefusedXmlException:
        rejection_code = OoxmlRejectionCode.UNSAFE_XML
    except (SyntaxError, ValueError):
        rejection_code = OoxmlRejectionCode.INVALID_XML
    if rejection_code is not None:
        _reject(rejection_code)
    return cast(Element, parsed)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _inspect_content_types(payload: bytes) -> None:
    root = _parse_xml(payload)
    if _local_name(root.tag) != "Types":
        _reject(OoxmlRejectionCode.INVALID_XML)
    for entry in root:
        if _local_name(entry.tag) not in {"Default", "Override"}:
            continue
        content_type = entry.attrib.get("ContentType", "").lower()
        if not content_type:
            _reject(OoxmlRejectionCode.INVALID_XML)
        if "macroenabled" in content_type or "vba" in content_type:
            _reject(OoxmlRejectionCode.MACRO)
        if any(
            marker in content_type
            for marker in ("activex", "oleobject", "embedded", "executable")
        ):
            _reject(OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT)


def _relationship_source_part(relationship_part: str) -> str:
    directory, basename = posixpath.split(relationship_part)
    if basename == ".rels" and directory == "_rels":
        return "/"
    if (
        basename.endswith(_RELATIONSHIPS_SUFFIX)
        and posixpath.basename(directory) == "_rels"
    ):
        source_directory = posixpath.dirname(directory)
        source_basename = basename[: -len(_RELATIONSHIPS_SUFFIX)]
        return posixpath.join(source_directory, source_basename)
    return relationship_part


def _safe_relationship_metadata(
    value: str | None,
    pattern: re.Pattern[str],
) -> str | None:
    if value is None or pattern.fullmatch(value) is None:
        return None
    return value


def _decoded_relationship_target(target: str) -> str:
    decoded = target
    for _ in range(8):
        if "%" not in decoded:
            return decoded
        if _UNSAFE_PERCENT_ESCAPE.search(decoded):
            _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
        try:
            next_decoded = unquote_to_bytes(decoded).decode("utf-8")
        except UnicodeDecodeError:
            _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
        if next_decoded == decoded:
            _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
        decoded = next_decoded
    if "%" in decoded:
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    return decoded


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _validate_internal_relationship_target(
    source_part: str,
    target: str,
) -> None:
    if not target or _has_control_characters(target):
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    if "\\" in target or any(character.isspace() for character in target):
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)

    decoded = _decoded_relationship_target(target)
    if _has_control_characters(decoded):
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    if "\\" in decoded or decoded.startswith("/"):
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    if parsed.path != decoded:
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)

    segments = decoded.split("/")
    if not segments[0] or ":" in segments[0] or "" in segments or "." in segments:
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    source_directory = "" if source_part == "/" else posixpath.dirname(source_part)
    resolved = posixpath.normpath(posixpath.join(source_directory, decoded))
    if resolved in {"", ".", ".."} or resolved.startswith(("../", "/")):
        _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)


def _inspect_relationships(
    relationship_part: str,
    payload: bytes,
) -> tuple[int, tuple[ExternalRelationship, ...]]:
    root = _parse_xml(payload)
    if _local_name(root.tag) != "Relationships":
        _reject(OoxmlRejectionCode.INVALID_XML)
    relationship_count = 0
    external: list[ExternalRelationship] = []
    source_part = _relationship_source_part(relationship_part)
    for entry in root:
        if _local_name(entry.tag) != "Relationship":
            continue
        relationship_count += 1
        target = entry.attrib.get("Target")
        relationship_type = entry.attrib.get("Type")
        if target is None or relationship_type is None:
            _reject(OoxmlRejectionCode.INVALID_XML)
        lower_relationship_type = relationship_type.lower()
        if "vbaproject" in lower_relationship_type:
            _reject(OoxmlRejectionCode.MACRO)
        if lower_relationship_type.endswith("/package"):
            _reject(OoxmlRejectionCode.NESTED_PACKAGE)
        if any(
            marker in lower_relationship_type
            for marker in ("activex", "oleobject", "embedded", "executable")
        ):
            _reject(OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT)
        target_mode = entry.attrib.get("TargetMode")
        if target_mode == "External":
            external.append(
                ExternalRelationship(
                    source_part_sha256=hashlib.sha256(
                        source_part.encode("utf-8")
                    ).hexdigest(),
                    relationship_id=_safe_relationship_metadata(
                        entry.attrib.get("Id"),
                        _SAFE_RELATIONSHIP_ID,
                    ),
                    relationship_type=_safe_relationship_metadata(
                        relationship_type,
                        _SAFE_RELATIONSHIP_TYPE,
                    ),
                    target_sha256=hashlib.sha256(target.encode("utf-8")).hexdigest(),
                )
            )
        elif target_mode in {None, "Internal"}:
            _validate_internal_relationship_target(source_part, target)
        else:
            _reject(OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET)
    return relationship_count, tuple(external)


def _inspect_snapshot(
    snapshot: BinaryIO,
    limits: OoxmlLimits,
) -> tuple[int, int, tuple[ExternalRelationship, ...]]:
    try:
        with zipfile.ZipFile(snapshot, "r") as archive:
            members = _preflight_members(archive.infolist(), limits)
            xml_payloads = _read_and_validate_members(archive, members, limits)
    except UnsafeOoxmlError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise UnsafeOoxmlError(OoxmlRejectionCode.INVALID_ZIP) from error

    _inspect_content_types(xml_payloads[_CONTENT_TYPES_PART])
    relationship_count = 0
    external: list[ExternalRelationship] = []
    for part_name in sorted(xml_payloads):
        if part_name.lower().endswith(_RELATIONSHIPS_SUFFIX):
            part_relationship_count, part_external = _inspect_relationships(
                part_name,
                xml_payloads[part_name],
            )
            relationship_count += part_relationship_count
            external.extend(part_external)
    if external:
        _reject(
            OoxmlRejectionCode.EXTERNAL_RELATIONSHIP,
            external_relationships=tuple(external),
        )
    return len(members), relationship_count, ()


def inspect_docx(path: Path, limits: OoxmlLimits) -> OoxmlInspection:
    """Inspect one securely opened DOCX snapshot without parsing document text."""
    require_secure_filesystem()
    _validate_limits(limits)
    parent_descriptor = open_directory_path(path.parent, create=False)
    source_descriptor: int | None = None
    try:
        try:
            source_descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            if error.errno == errno.ELOOP:
                _reject(OoxmlRejectionCode.SYMLINK)
            raise
        initial_status = os.fstat(source_descriptor)
        if not stat.S_ISREG(initial_status.st_mode):
            _reject(OoxmlRejectionCode.SPECIAL_FILE)
        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            package_sha256, package_bytes = _snapshot_source(
                source_descriptor,
                snapshot,
                initial_status,
            )
            _verify_source_unchanged(
                path.name,
                parent_descriptor,
                source_descriptor,
                initial_status,
            )
            member_count, relationship_count, external = _inspect_snapshot(
                snapshot,
                limits,
            )
            _verify_source_unchanged(
                path.name,
                parent_descriptor,
                source_descriptor,
                initial_status,
            )
            revalidate_directory_path(path.parent, parent_descriptor)
            return OoxmlInspection(
                package_sha256=package_sha256,
                package_bytes=package_bytes,
                member_count=member_count,
                relationship_count=relationship_count,
                external_relationships=external,
            )
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(parent_descriptor)
