from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.tool_metadata import (
    TOOL_METADATA_VERSION,
    InvalidToolReferenceError,
    RfcToolMetadata,
    ToolMetadataIntegrityError,
    build_rfc_tool_metadata,
)
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.retrieval.local import LocalCorpus
from tests.helpers import rfc_factory

CORPUS_ID = "a" * 64

# synthetic-spec-text: a fabricated RFC 9999, not a quotation.
TOOL_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Tools</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t pn="section-1-1">A sender <bcp14>SHOULD</bcp14> retry and
        <bcp14>MUST</bcp14> record it. MAY is unmarked.
        <xref target="section-2-1" derivedContent="Section 2"/>
        <xref target="three" derivedContent="Section 3"/>.</t>
      <dl>
        <dt>  Retry   Token </dt>
        <dd><t pn="section-1-2">First definition.</t>
            <t pn="section-1-3">Second definition.</t></dd>
      </dl>
      <table pn="table-1"><thead><tr><th>Kind</th></tr></thead>
        <tbody><tr><td>statusmarker</td></tr></tbody></table>
    </section>
    <section anchor="two" numbered="true">
      <name>Two</name>
      <t pn="section-2-1">Exact target with
        <xref target="four" derivedContent="Section 4"/>.</t>
    </section>
    <section anchor="three" numbered="true">
      <name>Three</name>
      <t pn="section-3-1">Section target one.</t>
      <t pn="section-3-2">Section target two.</t>
      <t pn="section-3-3">Section target three.</t>
      <t pn="section-3-4">Section target four.</t>
    </section>
    <section anchor="four" numbered="true">
      <name>Four</name>
      <t pn="section-4-1">A second-hop target.</t>
    </section>
    <section anchor="bad" numbered="true">
      <name>Bad</name>
      <t pn="section-5-1"><xref target="RFC9110" derivedContent="RFC 9110"/>.</t>
      <t pn="section-5-2"><xref target="missing" derivedContent="Missing"/>.</t>
    </section>
  </middle>
  <back><references><reference anchor="RFC9110">
    <front><title>Placeholder</title></front>
  </reference></references></back>
</rfc>
"""


@pytest.fixture
def metadata_fixture(tmp_path: Path):
    path = rfc_factory.write(tmp_path, "tools.xml", TOOL_RFC_XML)
    verified = load_verified_rfc(path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    metadata = build_rfc_tool_metadata(
        corpus_manifest_id=CORPUS_ID,
        documents=documents,
        units=corpus.units(),
        rfc_limits=RfcLimits(),
    )
    return corpus, metadata


def _by_text(corpus: LocalCorpus) -> dict[str, str]:
    return {unit.text: unit.unit_id for unit in corpus.units() if unit.kind == "clause"}


def _metadata_from_xml(
    tmp_path: Path, xml: str
) -> tuple[LocalCorpus, RfcToolMetadata]:
    path = rfc_factory.write(tmp_path, "references.xml", xml)
    verified = load_verified_rfc(path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    metadata = build_rfc_tool_metadata(
        corpus_manifest_id=CORPUS_ID,
        documents=documents,
        units=corpus.units(),
        rfc_limits=RfcLimits(),
    )
    return corpus, metadata


def test_sidecar_uses_exact_xml_provenance_and_has_a_canonical_hash(
    metadata_fixture,
) -> None:
    corpus, metadata = metadata_fixture
    clause_ids = _by_text(corpus)
    source = next(
        clause_id
        for text, clause_id in clause_ids.items()
        if text.startswith("A sender")
    )

    assert metadata.schema_version == TOOL_METADATA_VERSION == "rfc-tool-metadata/v1"
    assert metadata.corpus_manifest_id == CORPUS_ID
    assert metadata.normative_levels(source) == ("MUST", "SHOULD")
    assert "MAY" not in metadata.normative_levels(source)
    assert metadata.lookup("  RETRY\t token ") == (
        clause_ids["First definition."],
        clause_ids["Second definition."],
    )
    assert metadata.expand(source, limit=3) == (
        clause_ids["Exact target with Section 4."],
        clause_ids["Section target one."],
        clause_ids["Section target two."],
    )
    assert clause_ids["A second-hop target."] not in metadata.expand(source, limit=3)
    assert len(metadata.metadata_hash) == 64


def test_sidecar_marks_bibliography_and_dangling_references_invalid(
    metadata_fixture,
) -> None:
    corpus, metadata = metadata_fixture
    clause_ids = _by_text(corpus)

    for text in ("RFC 9110.", "Missing."):
        with pytest.raises(ValueError, match="invalid reference"):
            metadata.expand(clause_ids[text], limit=3)


def test_sidecar_refuses_a_changed_hash(metadata_fixture) -> None:
    _, metadata = metadata_fixture
    changed = replace(metadata, metadata_hash="0" * 64)

    with pytest.raises(ToolMetadataIntegrityError):
        changed.verify_integrity()


def test_one_target_node_with_equal_anchor_and_pn_is_not_ambiguous(
    tmp_path: Path,
) -> None:
    corpus, metadata = _metadata_from_xml(
        tmp_path,
        """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9998" version="3">
  <front><title>Same node</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one"><name>One</name>
      <t pn="section-1-1">Source <xref target="shared"
        derivedContent="Section 2"/>.</t>
    </section>
    <section anchor="two"><name>Two</name>
      <t anchor="shared" pn="shared">Exact target.</t>
    </section>
  </middle>
</rfc>
""",
    )
    clause_ids = _by_text(corpus)

    assert metadata.expand(clause_ids["Source Section 2."], limit=3) == (
        clause_ids["Exact target."],
    )


def test_two_distinct_nodes_with_one_identifier_remain_ambiguous(
    tmp_path: Path,
) -> None:
    corpus, metadata = _metadata_from_xml(
        tmp_path,
        """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9998" version="3">
  <front><title>Distinct nodes</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one"><name>One</name>
      <t pn="section-1-1">Source <xref target="shared"
        derivedContent="ambiguous"/>.</t>
    </section>
    <section anchor="two"><name>Two</name>
      <t anchor="shared" pn="section-2-1">First target.</t>
    </section>
    <section anchor="three"><name>Three</name>
      <t slugifiedName="shared" pn="section-3-1">Second target.</t>
    </section>
  </middle>
</rfc>
""",
    )
    source_id = _by_text(corpus)["Source ambiguous."]

    with pytest.raises(InvalidToolReferenceError, match="invalid reference"):
        metadata.expand(source_id, limit=3)
