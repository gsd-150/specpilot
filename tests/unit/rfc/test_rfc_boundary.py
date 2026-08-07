from __future__ import annotations

from pathlib import Path

import pytest

import specpilot.ingestion.rfc as rfc_module
from specpilot.contracts.rfc import RfcLimits, RfcRejectionCode, UnsafeRfcError
from specpilot.ingestion.rfc import inspect_rfc_xml
from tests.helpers import rfc_factory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "rfc"
    directory.mkdir(mode=0o700)
    return directory


def test_a_safe_rfc_is_accepted_and_reports_only_metadata(workspace: Path) -> None:
    path = rfc_factory.write_safe(workspace)

    inspection = inspect_rfc_xml(path, RfcLimits())

    assert inspection.root_tag == "rfc"
    assert inspection.document_bytes == path.stat().st_size
    assert len(inspection.document_sha256) == 64
    # The inspection is metadata. Document text must never ride along in it.
    assert "Synthetic" not in repr(inspection)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (rfc_factory.DOCTYPE_XML, RfcRejectionCode.DOCTYPE),
        (rfc_factory.INTERNAL_ENTITY_XML, RfcRejectionCode.ENTITY_DECLARATION),
        (rfc_factory.BILLION_LAUGHS_XML, RfcRejectionCode.ENTITY_DECLARATION),
        (rfc_factory.EXTERNAL_ENTITY_XML, RfcRejectionCode.EXTERNAL_ENTITY),
        (
            rfc_factory.EXTERNAL_PARAMETER_ENTITY_XML,
            RfcRejectionCode.EXTERNAL_ENTITY,
        ),
        (rfc_factory.STYLESHEET_PI_XML, RfcRejectionCode.PROCESSING_INSTRUCTION),
        (rfc_factory.WRONG_ROOT_XML, RfcRejectionCode.UNEXPECTED_ROOT),
        (rfc_factory.MALFORMED_XML, RfcRejectionCode.INVALID_XML),
    ],
    ids=[
        "doctype",
        "internal-entity",
        "billion-laughs",
        "external-entity",
        "external-parameter-entity",
        "stylesheet-pi",
        "wrong-root",
        "malformed",
    ],
)
def test_hostile_documents_are_refused_with_a_stable_code(
    workspace: Path,
    fixture: str,
    expected: RfcRejectionCode,
) -> None:
    path = rfc_factory.write(workspace, "hostile.xml", fixture)

    with pytest.raises(UnsafeRfcError) as raised:
        inspect_rfc_xml(path, RfcLimits())

    assert raised.value.code is expected


def test_invalid_utf8_is_refused_before_parsing(workspace: Path) -> None:
    path = rfc_factory.write_invalid_utf8(workspace)

    with pytest.raises(UnsafeRfcError) as raised:
        inspect_rfc_xml(path, RfcLimits())

    assert raised.value.code is RfcRejectionCode.INVALID_ENCODING


def test_an_oversized_document_is_refused_before_parsing(workspace: Path) -> None:
    limits = RfcLimits(max_bytes=4096)
    path = rfc_factory.write_oversized(workspace, limits.max_bytes)

    with pytest.raises(UnsafeRfcError) as raised:
        inspect_rfc_xml(path, limits)

    assert raised.value.code is RfcRejectionCode.DOCUMENT_TOO_LARGE


def test_refusal_never_carries_document_text(workspace: Path) -> None:
    """A rejection code is the whole public story; text must not leak into it."""
    path = rfc_factory.write(workspace, "hostile.xml", rfc_factory.EXTERNAL_ENTITY_XML)

    with pytest.raises(UnsafeRfcError) as raised:
        inspect_rfc_xml(path, RfcLimits())

    rendered = f"{raised.value!r} {raised.value}"
    assert "passwd" not in rendered
    assert "evil.example" not in rendered


def test_a_symlink_is_refused_rather_than_followed(workspace: Path) -> None:
    real = rfc_factory.write_safe(workspace, "real.xml")
    link = workspace / "link.xml"
    link.symlink_to(real)

    with pytest.raises(UnsafeRfcError) as raised:
        inspect_rfc_xml(link, RfcLimits())

    assert raised.value.code is RfcRejectionCode.NOT_A_REGULAR_FILE


@pytest.mark.parametrize(
    "fixture",
    [
        rfc_factory.DOCTYPE_XML,
        rfc_factory.INTERNAL_ENTITY_XML,
        rfc_factory.BILLION_LAUGHS_XML,
        rfc_factory.EXTERNAL_ENTITY_XML,
        rfc_factory.EXTERNAL_PARAMETER_ENTITY_XML,
    ],
    ids=["doctype", "internal-entity", "billion-laughs", "external", "external-param"],
)
def test_the_parser_refuses_independently_of_the_prologue_scan(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture: str,
) -> None:
    """Both layers must stop each shape, not one layer each.

    Disabling the prologue scan leaves only defusedxml. A shape that survives
    that is single-point defence, whatever the module docstring claims.
    """
    monkeypatch.setattr(rfc_module, "_refuse_hostile_prologue", lambda text: None)
    path = rfc_factory.write(workspace, "hostile.xml", fixture)

    with pytest.raises(UnsafeRfcError):
        inspect_rfc_xml(path, RfcLimits())


def test_the_same_document_hashes_identically_across_calls(workspace: Path) -> None:
    path = rfc_factory.write_safe(workspace)

    first = inspect_rfc_xml(path, RfcLimits())
    second = inspect_rfc_xml(path, RfcLimits())

    assert first.document_sha256 == second.document_sha256
    assert first == second
