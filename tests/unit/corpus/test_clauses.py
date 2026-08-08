from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import (
    ClauseLimits,
    OversizedClauseError,
    build_clauses,
    iter_clause_texts,
)
from specpilot.corpus.walk import document_identity, parse_verified
from specpilot.ingestion.rfc import load_verified_rfc
from tests.helpers import rfc_factory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


MULTI_PARAGRAPH_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Multi</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true" pn="section-1">
      <name>First</name>
      <t pn="section-1-1">Alpha paragraph.</t>
      <t pn="section-1-2">Beta paragraph.</t>
      <section anchor="one-one" numbered="true" pn="section-1.1">
        <name>Nested</name>
        <t pn="section-1.1-1">Gamma paragraph.</t>
      </section>
    </section>
  </middle>
</rfc>
"""


def test_a_clause_is_a_paragraph_carrying_its_section_identity(
    workspace: Path,
) -> None:
    """The source numbers sections; the caps count tokens. A clause holds both."""
    path = rfc_factory.write(workspace, "multi.xml", MULTI_PARAGRAPH_XML)

    clauses = build_clauses(path, RfcLimits(), ClauseLimits())

    assert [(c.section_number, c.ordinal, c.anchor) for c in clauses] == [
        ("1", 1, "section-1-1"),
        ("1", 2, "section-1-2"),
        ("1.1", 1, "section-1.1-1"),
    ]
    assert all(c.document_id for c in clauses)


def test_clauses_can_be_built_after_the_snapshot_path_disappears(
    workspace: Path,
) -> None:
    """A verified snapshot owns the parsed tree, not its former path."""
    path = rfc_factory.write(workspace, "snapshot.xml", MULTI_PARAGRAPH_XML)
    verified = load_verified_rfc(path, RfcLimits())
    path.unlink()

    clauses = build_clauses(verified, RfcLimits(), ClauseLimits())

    assert len(clauses) == 3
    assert {clause.document_version for clause in clauses} == {"2026-08"}


def test_clause_identity_uses_the_publication_version_not_the_xml_format(
    workspace: Path,
) -> None:
    """RFCXML ``version=3`` names the XML grammar, not this RFC edition."""
    path = rfc_factory.write(workspace, "multi.xml", MULTI_PARAGRAPH_XML)

    first = build_clauses(path, RfcLimits(), ClauseLimits())[0]

    assert first.document_version == "2026-08"
    assert first.clause_id == (
        "60cdaf6edfc2975ab6797c26540c0ecc1ec26808d7dd5c68b17f88004d79a448"
    )


def test_clause_ids_are_stable_across_builds_and_never_collide(
    workspace: Path,
) -> None:
    path = rfc_factory.write(workspace, "multi.xml", MULTI_PARAGRAPH_XML)

    first = build_clauses(path, RfcLimits(), ClauseLimits())
    second = build_clauses(path, RfcLimits(), ClauseLimits())

    assert [c.clause_id for c in first] == [c.clause_id for c in second]
    assert len({c.clause_id for c in first}) == len(first)
    assert all(len(c.clause_id) == 64 for c in first)


def test_rfcxml_format_version_is_not_part_of_the_document_identity(
    workspace: Path,
) -> None:
    document = rfc_factory.write(workspace, "v3.xml", MULTI_PARAGRAPH_XML)
    root = parse_verified(document, RfcLimits())
    first = document_identity(root)

    root.set("version", "4")

    assert document_identity(root) == first


def test_changing_the_publication_version_changes_unit_ids(
    workspace: Path,
) -> None:
    august = rfc_factory.write(workspace, "august.xml", MULTI_PARAGRAPH_XML)
    september = rfc_factory.write(
        workspace,
        "september.xml",
        MULTI_PARAGRAPH_XML.replace('month="08"', 'month="09"'),
    )

    first = build_clauses(august, RfcLimits(), ClauseLimits())
    second = build_clauses(september, RfcLimits(), ClauseLimits())

    assert [clause.clause_id for clause in first] != [
        clause.clause_id for clause in second
    ]


COLLIDING_SHAPE_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Collide</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="section-1" numbered="true">
      <name>One</name>
{paras_a}
    </section>
    <section anchor="section-11" numbered="true">
      <name>Eleven</name>
{paras_b}
    </section>
  </middle>
</rfc>
"""


def test_clause_ids_survive_an_ambiguous_anchor_and_ordinal_split(
    workspace: Path,
) -> None:
    """Section "1" paragraph 12 and section "11" paragraph 2 must not collide.

    Concatenating identity components without a separator makes those two the
    same string. The separator is what stops that, so this is the test that
    proves the separator is there.
    """
    paras_a = "\n".join(f"      <t>Body {n}.</t>" for n in range(1, 13))
    paras_b = "\n".join(f"      <t>Other {n}.</t>" for n in range(1, 3))
    path = rfc_factory.write(
        workspace,
        "collide.xml",
        COLLIDING_SHAPE_XML.format(paras_a=paras_a, paras_b=paras_b),
    )

    clauses = build_clauses(path, RfcLimits(), ClauseLimits())

    assert len({c.clause_id for c in clauses}) == len(clauses) == 14


NESTED_CONTAINERS_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front>
    <title>Containers</title>
    <date month="08" year="2026"/>
    <abstract><t>Front matter, not a citable section.</t></abstract>
  </front>
  <middle>
    <section anchor="rules" numbered="true">
      <name>Rules</name>
      <t>A direct paragraph.</t>
      <ul>
        <li><t>A list item paragraph.</t></li>
        <li><t>Another list item paragraph.</t></li>
      </ul>
      <dl><dt>Term</dt><dd><t>A definition paragraph.</t></dd></dl>
      <aside><t>An aside paragraph.</t></aside>
      <section anchor="sub" numbered="true">
        <name>Sub</name>
        <t>Belongs to the subsection, not the parent.</t>
      </section>
    </section>
  </middle>
</rfc>
"""


def test_paragraphs_inside_lists_and_asides_are_citable(workspace: Path) -> None:
    """Normative text lives in list items too.

    Measured on RFC 9110: 365 paragraphs sit inside <li>, 33 inside <aside>,
    and 11 inside <dd>. Collecting only a section's direct <t> children would
    make roughly a quarter of the document impossible to cite.
    """
    path = rfc_factory.write(workspace, "containers.xml", NESTED_CONTAINERS_XML)

    clauses = build_clauses(path, RfcLimits(), ClauseLimits())
    texts = [text for _, text in iter_clause_texts(path, RfcLimits(), ClauseLimits())]

    assert texts == [
        "A direct paragraph.",
        "A list item paragraph.",
        "Another list item paragraph.",
        "A definition paragraph.",
        "An aside paragraph.",
        "Belongs to the subsection, not the parent.",
    ]
    owning = {c.section_anchor for c in clauses}
    assert owning == {"rules", "sub"}
    assert sum(1 for c in clauses if c.section_anchor == "rules") == 5


BACK_MATTER_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Back</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="body" numbered="true">
      <name>Body</name>
      <t>Body paragraph.</t>
    </section>
  </middle>
  <back>
    <section anchor="abnf" numbered="true">
      <name>Collected ABNF</name>
      <t>Appendix paragraph.</t>
    </section>
    <section anchor="ack" numbered="false">
      <name>Acknowledgements</name>
      <t>Thanks to everyone.</t>
    </section>
    <section anchor="addr" numbered="false">
      <name>Authors' Addresses</name>
      <t>Someone, somewhere@example.com, +1 555 0100.</t>
    </section>
  </back>
</rfc>
"""


def test_numbered_appendices_are_citable_and_lettered(workspace: Path) -> None:
    """"Appendix A" is a citation a reader can follow, so it needs a clause."""
    path = rfc_factory.write(workspace, "back.xml", BACK_MATTER_XML)

    clauses = build_clauses(path, RfcLimits(), ClauseLimits())

    assert [(c.section_anchor, c.section_number) for c in clauses] == [
        ("body", "1"),
        ("abnf", "A"),
    ]


def test_unnumbered_back_matter_is_excluded(workspace: Path) -> None:
    """Apparatus is not corpus, and one piece of it is personal data.

    Acknowledgements and the index are noise for retrieval; Authors' Addresses
    carries contact details that should never reach an embedding index or an
    outbound excerpt. The document already marks all three unnumbered.
    """
    path = rfc_factory.write(workspace, "back.xml", BACK_MATTER_XML)

    texts = [text for _, text in iter_clause_texts(path, RfcLimits(), ClauseLimits())]

    assert "Thanks to everyone." not in texts
    assert not any("example.com" in text for text in texts)


def test_front_matter_is_not_a_citable_section(workspace: Path) -> None:
    """The abstract is not a numbered place a citation can point at."""
    path = rfc_factory.write(workspace, "containers.xml", NESTED_CONTAINERS_XML)

    texts = [text for _, text in iter_clause_texts(path, RfcLimits(), ClauseLimits())]

    assert "Front matter, not a citable section." not in texts


def test_a_clause_carries_no_text(workspace: Path) -> None:
    """A clause is a locator. Text in the record would make it uncommittable."""
    path = rfc_factory.write(workspace, "multi.xml", MULTI_PARAGRAPH_XML)

    clauses = build_clauses(path, RfcLimits(), ClauseLimits())

    rendered = repr(clauses)
    assert "Alpha paragraph" not in rendered
    assert "Beta paragraph" not in rendered


def test_every_clause_fits_the_excerpt_caps(workspace: Path) -> None:
    path = rfc_factory.write(workspace, "multi.xml", MULTI_PARAGRAPH_XML)
    limits = ClauseLimits()

    for clause in build_clauses(path, RfcLimits(), limits):
        assert clause.byte_count <= limits.max_bytes
        assert clause.word_count <= limits.max_words


def test_a_paragraph_over_the_cap_fails_closed(workspace: Path) -> None:
    """Refuse rather than silently truncate a clause the enforcer would reject."""
    long_text = " ".join(f"word{n}" for n in range(400))
    xml = MULTI_PARAGRAPH_XML.replace("Alpha paragraph.", long_text)
    path = rfc_factory.write(workspace, "long.xml", xml)

    with pytest.raises(OversizedClauseError):
        build_clauses(path, RfcLimits(), ClauseLimits(max_words=100))


def test_clause_text_is_available_only_through_an_explicit_call(
    workspace: Path,
) -> None:
    """Text is reachable, but never by accident."""
    path = rfc_factory.write(workspace, "multi.xml", MULTI_PARAGRAPH_XML)

    pairs = list(iter_clause_texts(path, RfcLimits(), ClauseLimits()))

    assert [text for _, text in pairs] == [
        "Alpha paragraph.",
        "Beta paragraph.",
        "Gamma paragraph.",
    ]
    assert [c.clause_id for c, _ in pairs] == [
        c.clause_id for c in build_clauses(path, RfcLimits(), ClauseLimits())
    ]
    for clause, text in pairs:
        assert clause.byte_count == len(text.encode("utf-8"))


def test_building_clauses_requires_passing_the_rfc_boundary(
    workspace: Path,
) -> None:
    path = rfc_factory.write(
        workspace, "hostile.xml", rfc_factory.EXTERNAL_ENTITY_XML
    )

    from specpilot.contracts.rfc import UnsafeRfcError

    with pytest.raises(UnsafeRfcError):
        build_clauses(path, RfcLimits(), ClauseLimits())


def test_an_unnumbered_section_still_yields_locatable_clauses(
    workspace: Path,
) -> None:
    """A section without a number is still a place, just not citable by number."""
    xml = MULTI_PARAGRAPH_XML.replace(
        'anchor="one" numbered="true"', 'anchor="one" numbered="false"'
    )
    path = rfc_factory.write(workspace, "unnumbered.xml", xml)

    clauses = build_clauses(path, RfcLimits(), ClauseLimits())

    top = [c for c in clauses if c.section_anchor == "one"]
    assert top
    assert all(c.section_number is None for c in top)
    assert all(c.section_path for c in top)
