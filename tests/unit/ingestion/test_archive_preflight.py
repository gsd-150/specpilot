from __future__ import annotations

import hashlib
import json
import zipfile
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
        max_members=4,
        max_member_bytes=1_024,
        max_total_bytes=2_048,
    )


def build_zip(
    tmp_path: Path,
    member_name: str,
    *,
    external_attr: int = 0,
) -> Path:
    archive_path = tmp_path / "submission.zip"
    member = zipfile.ZipInfo(member_name)
    member.create_system = 3
    member.external_attr = external_attr or (0o100600 << 16)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"not really a docx")
    return archive_path


def quarantine_records(quarantine_dir: Path) -> list[Path]:
    return list(quarantine_dir.glob("*/record.json"))


@pytest.mark.parametrize(
    ("member_name", "external_attr", "expected_code"),
    [
        ("../escape.docx", 0, ArchiveRejectionCode.PATH_TRAVERSAL),
        ("/absolute.docx", 0, ArchiveRejectionCode.ABSOLUTE_PATH),
        ("expected.docx", 0o120777 << 16, ArchiveRejectionCode.SYMLINK),
        ("payload.exe", 0, ArchiveRejectionCode.UNEXPECTED_MEMBER),
        ("nested.ZIP", 0, ArchiveRejectionCode.NESTED_ARCHIVE),
    ],
)
def test_unsafe_member_quarantines_whole_archive(
    tmp_path: Path,
    member_name: str,
    external_attr: int,
    expected_code: ArchiveRejectionCode,
) -> None:
    archive = build_zip(tmp_path, member_name, external_attr=external_attr)
    corpus_dir = tmp_path / "corpus"
    quarantine_dir = tmp_path / "quarantine"

    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(archive, corpus_dir, quarantine_dir, policy())

    assert raised.value.code is expected_code
    assert not corpus_dir.exists() or not any(corpus_dir.iterdir())
    records = quarantine_records(quarantine_dir)
    assert len(records) == 1
    assert json.loads(records[0].read_text())["rejection_code"] == expected_code


def test_all_members_are_preflighted_before_any_member_is_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "submission.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("expected.docx", b"expected")
        archive.writestr("../escape.docx", b"unsafe")

    opened_members: list[str] = []
    original_open = zipfile.ZipFile.open

    def recording_open(
        archive: zipfile.ZipFile,
        member: str | zipfile.ZipInfo,
        *args: object,
        **kwargs: object,
    ) -> object:
        opened_members.append(
            member.filename if isinstance(member, zipfile.ZipInfo) else member
        )
        return original_open(archive, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", recording_open)

    with pytest.raises(UnsafeArchiveError):
        extract_expected_docx(
            archive_path,
            tmp_path / "corpus",
            tmp_path / "quarantine",
            policy(),
        )

    assert opened_members == []


def test_safe_expected_member_is_extracted_under_the_expected_name(
    tmp_path: Path,
) -> None:
    payload = b"not really a docx"
    archive = build_zip(tmp_path, "incoming/expected.docx")
    corpus_dir = tmp_path / "corpus"

    result = extract_expected_docx(
        archive,
        corpus_dir,
        tmp_path / "quarantine",
        policy(),
    )

    assert (corpus_dir / "expected.docx").read_bytes() == payload
    assert result.member_name == "incoming/expected.docx"
    assert result.docx_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.byte_count == len(payload)


def test_dos_directory_attribute_is_not_accepted_as_a_regular_member(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "submission.zip"
    member = zipfile.ZipInfo("expected.docx")
    member.create_system = 0
    member.external_attr = 0x10
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"not a regular member")

    with pytest.raises(UnsafeArchiveError) as raised:
        extract_expected_docx(
            archive_path,
            tmp_path / "corpus",
            tmp_path / "quarantine",
            policy(),
        )

    assert raised.value.code is ArchiveRejectionCode.SPECIAL_FILE
    assert not (tmp_path / "corpus").exists()


def test_archive_path_mutation_cannot_change_bytes_after_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_payload = b"original payload"
    replacement_payload = b"replacement data"
    archive_path = tmp_path / "submission.zip"
    replacement_path = tmp_path / "replacement.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("expected.docx", original_payload)
    original_archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(replacement_path, "w") as archive:
        archive.writestr("expected.docx", replacement_payload)

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
            archive_path.write_bytes(replacement_path.read_bytes())
            mutated = True
        original_init(instance, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", mutating_init)

    result = extract_expected_docx(
        archive_path,
        tmp_path / "corpus",
        tmp_path / "quarantine",
        policy(),
    )

    assert result.archive_sha256 == original_archive_sha256
    assert (tmp_path / "corpus" / "expected.docx").read_bytes() == original_payload
    assert result.docx_sha256 == hashlib.sha256(original_payload).hexdigest()
