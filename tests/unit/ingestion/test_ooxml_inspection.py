from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from specpilot.ingestion.ooxml import (
    OoxmlLimits,
    OoxmlRejectionCode,
    UnsafeOoxmlError,
    inspect_docx,
)
from tests.helpers.ooxml_factory import build_docx


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("macro_content_type", OoxmlRejectionCode.MACRO),
        ("vba_project", OoxmlRejectionCode.MACRO),
        ("embedded_executable", OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT),
        ("ole_object", OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT),
        ("nested_package", OoxmlRejectionCode.NESTED_PACKAGE),
        ("external_relationship", OoxmlRejectionCode.EXTERNAL_RELATIONSHIP),
    ],
)
def test_active_content_never_becomes_parseable_fixture(
    tmp_path: Path,
    mutation: str,
    code: OoxmlRejectionCode,
) -> None:
    source = build_docx(tmp_path, mutation)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is code


def test_safe_docx_returns_only_sanitized_package_metadata(tmp_path: Path) -> None:
    source = build_docx(tmp_path)
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()

    result = inspect_docx(source, OoxmlLimits())

    assert result.package_sha256 == expected_digest
    assert result.package_bytes == source.stat().st_size
    assert result.member_count == 2
    assert result.relationship_count == 0
    assert result.external_relationships == ()


def test_external_target_is_recorded_only_as_a_hash(tmp_path: Path) -> None:
    source = build_docx(tmp_path, "external_relationship")
    target = "https://secret.example.invalid/private?q=token"

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.EXTERNAL_RELATIONSHIP
    assert len(raised.value.external_relationships) == 1
    finding = raised.value.external_relationships[0]
    assert finding.target_sha256 == hashlib.sha256(target.encode()).hexdigest()
    assert finding.source_part == "word/document.xml"
    assert finding.relationship_id == "rId9"
    assert target not in str(raised.value)
    assert target not in repr(raised.value)


def test_internal_ole_relationship_is_rejected(tmp_path: Path) -> None:
    source = build_docx(tmp_path, "ole_relationship")

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.EMBEDDED_ACTIVE_CONTENT


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("traversal", OoxmlRejectionCode.PATH_TRAVERSAL),
        ("symlink", OoxmlRejectionCode.SYMLINK),
    ],
)
def test_unsafe_member_is_rejected_before_xml_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    code: OoxmlRejectionCode,
) -> None:
    source = build_docx(tmp_path, mutation)

    def forbidden_parse(*args: object, **kwargs: object) -> object:
        raise AssertionError("XML parsing must not start")

    monkeypatch.setattr("specpilot.ingestion.ooxml._parse_xml", forbidden_parse)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is code


def test_doctype_and_entity_are_rejected_without_xml_leakage(tmp_path: Path) -> None:
    source = build_docx(tmp_path, "doctype")

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.UNSAFE_XML
    assert "SHOULD-NOT-LEAK" not in str(raised.value)
    assert "SHOULD-NOT-LEAK" not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_declared_member_limit_is_rejected(tmp_path: Path) -> None:
    source = build_docx(tmp_path)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, replace(OoxmlLimits(), max_member_bytes=32))

    assert raised.value.code is OoxmlRejectionCode.MEMBER_TOO_LARGE


def test_actual_streamed_limit_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_docx(tmp_path)

    def oversized_open(*args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(b"x" * 513)

    monkeypatch.setattr(zipfile.ZipFile, "open", oversized_open)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(
            source,
            replace(
                OoxmlLimits(),
                max_member_bytes=512,
                max_total_bytes=1_024,
                max_xml_bytes=512,
            ),
        )

    assert raised.value.code is OoxmlRejectionCode.MEMBER_TOO_LARGE


def test_actual_streamed_total_limit_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_docx(tmp_path)

    def inflated_open(*args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(b"x" * 651)

    monkeypatch.setattr(zipfile.ZipFile, "open", inflated_open)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(
            source,
            replace(
                OoxmlLimits(),
                max_member_bytes=700,
                max_total_bytes=650,
                max_xml_bytes=700,
            ),
        )

    assert raised.value.code is OoxmlRejectionCode.TOTAL_TOO_LARGE


def test_actual_streamed_xml_limit_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_docx(tmp_path)
    original_open = zipfile.ZipFile.open

    def inflated_content_types(
        archive: zipfile.ZipFile,
        member: str | zipfile.ZipInfo,
        *args: object,
        **kwargs: object,
    ) -> object:
        member_name = member.filename if isinstance(member, zipfile.ZipInfo) else member
        if member_name == "[Content_Types].xml":
            return io.BytesIO(b"x" * 513)
        return original_open(archive, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", inflated_content_types)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(
            source,
            replace(
                OoxmlLimits(),
                max_member_bytes=1_024,
                max_total_bytes=2_048,
                max_xml_bytes=512,
            ),
        )

    assert raised.value.code is OoxmlRejectionCode.XML_TOO_LARGE


def test_input_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    source = build_docx(tmp_path)
    link = tmp_path / "linked.docx"
    link.symlink_to(source)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(link, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.SYMLINK


def test_content_types_directory_is_rejected_with_a_stable_code(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml/", b"")
        archive.writestr("word/document.xml", b"<document/>")

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.MISSING_CONTENT_TYPES


def test_path_replacement_during_inspection_cannot_return_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_docx(tmp_path)
    replacement_dir = tmp_path / "replacement"
    replacement_dir.mkdir()
    replacement = build_docx(replacement_dir)
    original_init = zipfile.ZipFile.__init__
    mutated = False

    def mutating_init(
        instance: zipfile.ZipFile,
        file: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal mutated
        if not mutated:
            source.write_bytes(replacement.read_bytes())
            mutated = True
        original_init(instance, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", mutating_init)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.INPUT_CHANGED
