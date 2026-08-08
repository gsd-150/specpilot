from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits, RfcRejectionCode, UnsafeRfcError
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.rfc.structure import (
    CrossReferenceKind,
    DuplicateAnchorError,
    extract_structure,
)
from tests.helpers import rfc_factory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "rfc"
    directory.mkdir(mode=0o700)
    return directory


def test_nested_sections_are_ordered_and_numbered(workspace: Path) -> None:
    path = rfc_factory.write_safe(workspace)

    structure = extract_structure(path, RfcLimits())

    assert [(s.anchor, s.number, s.title, s.depth) for s in structure.sections] == [
        ("intro", "1", "Introduction", 1),
        ("scope", "1.1", "Scope", 2),
    ]


def test_structure_can_be_extracted_after_the_snapshot_path_disappears(
    workspace: Path,
) -> None:
    """Structure walks the verified tree without reopening its source path."""
    path = rfc_factory.write_safe(workspace)
    verified = load_verified_rfc(path, RfcLimits())
    path.unlink()

    structure = extract_structure(verified, RfcLimits())

    assert [(section.anchor, section.number) for section in structure.sections] == [
        ("intro", "1"),
        ("scope", "1.1"),
    ]
    assert structure.document_sha256 == verified.inspection.document_sha256


def test_unnumbered_sections_do_not_consume_a_number(workspace: Path) -> None:
    """A numbered="false" section is real structure but not a citable number."""
    path = rfc_factory.write(
        workspace, "mixed.xml", rfc_factory.UNNUMBERED_SECTION_XML
    )

    structure = extract_structure(path, RfcLimits())

    assert [(s.anchor, s.number) for s in structure.sections] == [
        ("counted", "1"),
        ("uncounted", None),
        ("counted-again", "2"),
    ]


def test_cross_references_separate_internal_from_inter_document(
    workspace: Path,
) -> None:
    path = rfc_factory.write_safe(workspace)

    structure = extract_structure(path, RfcLimits())
    by_target = {x.target: x for x in structure.cross_references}

    assert by_target["intro"].kind is CrossReferenceKind.INTERNAL
    assert by_target["intro"].source_anchor == "scope"
    assert by_target["RFC9110"].kind is CrossReferenceKind.INTER_DOCUMENT
    assert all(x.resolved for x in structure.cross_references)


def test_a_dangling_cross_reference_is_reported_not_dropped(workspace: Path) -> None:
    """Cross-references are why this corpus was chosen; a broken one is a finding."""
    path = rfc_factory.write(
        workspace, "dangling.xml", rfc_factory.DANGLING_XREF_XML
    )

    structure = extract_structure(path, RfcLimits())

    assert len(structure.cross_references) == 1
    dangling = structure.cross_references[0]
    assert dangling.target == "nowhere"
    assert dangling.resolved is False
    assert dangling.kind is CrossReferenceKind.DANGLING
    assert structure.dangling_count == 1


def test_duplicate_anchors_fail_closed(workspace: Path) -> None:
    """Two definitions of one anchor make every reference to it ambiguous."""
    path = rfc_factory.write(
        workspace, "duplicate.xml", rfc_factory.DUPLICATE_ANCHOR_XML
    )

    with pytest.raises(DuplicateAnchorError):
        extract_structure(path, RfcLimits())


def test_structure_extraction_requires_passing_the_boundary(workspace: Path) -> None:
    """No structure without verification: the unsafe document never gets parsed."""
    path = rfc_factory.write(
        workspace, "hostile.xml", rfc_factory.EXTERNAL_ENTITY_XML
    )

    with pytest.raises(UnsafeRfcError) as raised:
        extract_structure(path, RfcLimits())

    assert raised.value.code is RfcRejectionCode.EXTERNAL_ENTITY


def test_extraction_never_fetches_a_reference_target(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cross-reference is recorded, never followed."""
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("structure extraction attempted a network call")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    path = rfc_factory.write_safe(workspace)

    structure = extract_structure(path, RfcLimits())

    assert structure.cross_references


NESTED_XREF_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Nested</title></front>
  <middle>
    <section anchor="outer" numbered="true">
      <name>Outer</name>
      <section anchor="inner" numbered="true">
        <name>Inner</name>
        <t>One reference to <xref target="outer"/>, counted once.</t>
      </section>
    </section>
  </middle>
</rfc>
"""

IDENTIFIER_FLAVOURS_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Flavours</title></front>
  <middle>
    <section anchor="s" pn="section-1" numbered="true">
      <name slugifiedName="name-only">Only</name>
      <t pn="section-1-1">Targets <xref target="section-1-1"/> and
      <xref target="name-only"/>.</t>
    </section>
  </middle>
</rfc>
"""


def test_a_reference_inside_a_nested_section_is_counted_once(
    workspace: Path,
) -> None:
    """Iterating xrefs per section double-counts everything nested."""
    path = rfc_factory.write(workspace, "nested.xml", NESTED_XREF_XML)

    structure = extract_structure(path, RfcLimits())

    assert len(structure.cross_references) == 1
    only = structure.cross_references[0]
    assert only.target == "outer"
    assert only.source_anchor == "inner"
    assert only.kind is CrossReferenceKind.INTERNAL


def test_pn_and_slugified_targets_resolve_like_anchors(workspace: Path) -> None:
    """Prepped RFC XML names things three ways.

    Resolving only one of them makes a clean document look broken.
    """
    path = rfc_factory.write(workspace, "flavours.xml", IDENTIFIER_FLAVOURS_XML)

    structure = extract_structure(path, RfcLimits())

    assert structure.dangling_count == 0
    assert {x.target for x in structure.cross_references} == {
        "section-1-1",
        "name-only",
    }


def test_structure_carries_the_verified_document_hash(workspace: Path) -> None:
    """Structure is bound to the exact bytes it came from."""
    path = rfc_factory.write_safe(workspace)

    structure = extract_structure(path, RfcLimits())

    assert len(structure.document_sha256) == 64
    assert structure.section_count == len(structure.sections)
