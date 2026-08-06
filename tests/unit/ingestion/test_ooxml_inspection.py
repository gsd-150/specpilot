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
from tests.helpers.ooxml_factory import (
    append_windows_member,
    build_docx,
    build_relationship_docx,
)


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
    assert finding.source_part_sha256 == hashlib.sha256(
        b"word/document.xml"
    ).hexdigest()
    assert finding.relationship_id == "rId9"
    assert target not in str(raised.value)
    assert target not in repr(raised.value)


@pytest.mark.parametrize("target_mode", [None, "Internal"])
@pytest.mark.parametrize(
    "target",
    [
        "https://secret.example.invalid/egress",
        "//secret.example.invalid/egress",
        "/word/media/image.png",
        r"\\secret.example.invalid\share",
        "word/media/image.png\nsecret",
        "word/media/image.png?secret=yes",
        "scheme:payload",
        "%2F%2Fsecret.example.invalid/egress",
        "word%2Fmedia/image.png",
        "%0Asecret.xml",
        "%2e%2e/%2e%2e/escape.xml",
        "..%2f..%2fescape.xml",
        "../../escape.xml",
    ],
)
def test_internal_relationship_target_must_be_a_canonical_relative_part_uri(
    tmp_path: Path,
    target: str,
    target_mode: str | None,
) -> None:
    source = build_relationship_docx(
        tmp_path,
        target=target,
        target_mode=target_mode,
    )

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET


def test_unknown_relationship_target_mode_is_rejected(tmp_path: Path) -> None:
    source = build_relationship_docx(
        tmp_path,
        target="media/image.png",
        target_mode="Unknown",
    )

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.INVALID_RELATIONSHIP_TARGET


@pytest.mark.parametrize("target_mode", [None, "Internal"])
def test_internal_relative_target_that_stays_in_package_is_accepted(
    tmp_path: Path,
    target_mode: str | None,
) -> None:
    source = build_relationship_docx(
        tmp_path,
        target="../media/image.png",
        target_mode=target_mode,
    )

    result = inspect_docx(source, OoxmlLimits())

    assert result.relationship_count == 1


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


@pytest.mark.parametrize(
    ("member_name", "external_attr"),
    [
        ("word/media/volume/", 0x08),
        ("word/media/volume/", 0x18),
        ("word/media/inconsistent/", 0x00),
        ("word/media/not-a-directory.bin", 0x10),
    ],
)
def test_windows_special_or_inconsistent_member_attributes_are_rejected(
    tmp_path: Path,
    member_name: str,
    external_attr: int,
) -> None:
    source = build_docx(tmp_path)
    append_windows_member(source, member_name, external_attr=external_attr)

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    assert raised.value.code is OoxmlRejectionCode.SPECIAL_FILE


def test_windows_directory_attribute_with_directory_name_is_accepted(
    tmp_path: Path,
) -> None:
    source = build_docx(tmp_path)
    append_windows_member(source, "word/media/", external_attr=0x10)

    result = inspect_docx(source, OoxmlLimits())

    assert result.member_count == 3


def test_external_finding_hashes_control_character_source_part(
    tmp_path: Path,
) -> None:
    relationship_part = "word/_rels/private\nsource.xml.rels"
    source_part = "word/private\nsource.xml"
    source = build_relationship_docx(
        tmp_path,
        target="https://secret.example.invalid/egress",
        target_mode="External",
        relationship_part=relationship_part,
    )

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    finding = raised.value.external_relationships[0]
    expected_source_hash = hashlib.sha256(source_part.encode()).hexdigest()
    assert finding.source_part_sha256 == expected_source_hash
    assert source_part not in repr(finding)
    assert source_part not in str(raised.value)


def test_external_finding_omits_untrusted_relationship_metadata(
    tmp_path: Path,
) -> None:
    source = build_relationship_docx(
        tmp_path,
        target="https://secret.example.invalid/egress",
        target_mode="External",
        relationship_id="private metadata",
        relationship_type="https://private.example.invalid/secret-metadata",
    )

    with pytest.raises(UnsafeOoxmlError) as raised:
        inspect_docx(source, OoxmlLimits())

    finding = raised.value.external_relationships[0]
    assert finding.relationship_id is None
    assert finding.relationship_type is None
    assert "private" not in repr(finding)


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
