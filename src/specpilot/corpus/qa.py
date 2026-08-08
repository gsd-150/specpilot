"""Product plan §4.1's parse QA, as a command rather than a paragraph.

The plan says the corpus manifest may not be frozen until these lines pass. A
gate that has to be remembered before freezing is a gate that gets skipped once
and then forever, so it is executable, it reports every measured value rather
than a verdict, and `corpus freeze` will refuse without it.

Two of the lines come out stronger here than the plan asked for. §4.1 settles
for a stratified sample of twenty clauses and ten cross-references because
checking a DOCX means checking by hand. RFC v3 publishes its own numbering in
every `pn` and its own reference targets as anchors, so both are checked over
the whole corpus: 1909 of 1909 clauses and 2977 of 2977 references on the frozen
documents.

The coverage line is the one that earns its keep. It is what caught 2913 words
of grammar sitting in no unit at all — content that no citation could name and
no retriever could reach, which reads as a perfectly healthy corpus from every
other angle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, build_clauses, iter_clause_texts
from specpilot.corpus.tables import iter_table_rows
from specpilot.corpus.walk import (
    element_text,
    owned_paragraphs,
    owned_tables,
    parse_verified,
    sections,
)

# `pn` reads "section-<number>-<ordinal>", with the ordinal sometimes carrying
# sub-positions for list items: section-5.6.2-3.1.
_PN = re.compile(r"^section-(.+?)-\d+(?:\.\d+)*$")


@dataclass(frozen=True, slots=True)
class QaThresholds:
    """The blocking values from §4.1, named so a reader can check them.

    Every one is a decision recorded in the plan, not a dial. `run_parse_qa`
    fails if any line fails, so loosening one cannot rescue a document that
    fails another.
    """

    min_section_numbering: float = 1.0
    min_cross_reference_targets: float = 0.9
    min_table_fidelity: float = 0.9
    max_uncaptured: float = 0.02
    max_orphan_normatives: float = 0.01


@dataclass(frozen=True, slots=True)
class QaLine:
    name: str
    measured: float
    threshold: float
    passed: bool
    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class QaReport:
    document_id: str
    passed: bool
    lines: tuple[QaLine, ...] = field(default_factory=tuple)


def _declared_section(pn: str) -> str | None:
    """The section number the source itself assigns to this paragraph."""
    match = _PN.match(pn or "")
    if match is None:
        return None
    declared = match.group(1)
    if declared.startswith("appendix."):
        # appendix.b.2 is cited as B.2.
        parts = declared.split(".")[1:]
        return ".".join([parts[0].upper(), *parts[1:]])
    return declared


def _at_least(name: str, hit: int, total: int, threshold: float) -> QaLine:
    measured = hit / total if total else 1.0
    return QaLine(name, measured, threshold, measured >= threshold, hit, total)


def _at_most(name: str, hit: int, total: int, threshold: float) -> QaLine:
    measured = hit / total if total else 0.0
    return QaLine(name, measured, threshold, measured <= threshold, hit, total)


def run_parse_qa(
    path: Path,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
    thresholds: QaThresholds,
) -> QaReport:
    """Measure every blocking line and report all of them."""
    root = parse_verified(path, rfc_limits)
    document_id = f"ietf-rfc-{root.get('number') or 'unknown'}"
    corpus_sections = [
        section
        for section in sections(root)
        if section.anchor not in clause_limits.excluded_sections
    ]

    clauses = build_clauses(path, rfc_limits, clause_limits)
    matching = sum(
        1
        for clause in clauses
        if _declared_section(clause.anchor or "") == (clause.section_number or "")
    )

    anchors = {
        value
        for element in root.iter()
        for key in ("anchor", "pn", "slugifiedName")
        if (value := element.get(key))
    }
    references = [element.get("target") or "" for element in root.iter("xref")]
    resolved = sum(1 for target in references if target in anchors)

    captured = sum(
        len(text.split())
        for _, text in iter_clause_texts(path, rfc_limits, clause_limits)
    )
    table_words = 0
    for _, rows in iter_table_rows(path, rfc_limits, clause_limits):
        table_words += len(" ".join(cell for row in rows for cell in row).split())
    captured += table_words

    source_words = 0
    source_table_words = 0
    owned: set[int] = set()
    for section in corpus_sections:
        owned.update(id(element) for element in owned_paragraphs(section.element))
        for child in section.element:
            if child.tag in ("section", "name"):
                continue
            source_words += len(element_text(child).split())
        for table in owned_tables(section.element):
            source_table_words += len(element_text(table).split())

    # Counted once per keyword against the whole document, not once per
    # enclosing section. A keyword inside a nested subsection belongs to that
    # subsection's clause; attributing it to every ancestor would inflate both
    # sides of the ratio and report attached keywords as orphans.
    parents = {id(child): element for element in root.iter() for child in element}
    in_corpus = {
        id(element) for section in corpus_sections for element in section.element.iter()
    }
    orphan = 0
    normative = 0
    for keyword in root.iter("bcp14"):
        if id(keyword) not in in_corpus:
            continue
        normative += 1
        node = parents.get(id(keyword))
        while node is not None and id(node) not in owned:
            node = parents.get(id(node))
        if node is None:
            orphan += 1

    lines = (
        _at_least(
            "section_numbering",
            matching,
            len(clauses),
            thresholds.min_section_numbering,
        ),
        _at_least(
            "cross_references",
            resolved,
            len(references),
            thresholds.min_cross_reference_targets,
        ),
        _at_least(
            "table_fidelity",
            table_words,
            source_table_words,
            thresholds.min_table_fidelity,
        ),
        _at_most(
            "coverage",
            max(source_words - captured, 0),
            source_words,
            thresholds.max_uncaptured,
        ),
        _at_most(
            "orphan_normatives", orphan, normative, thresholds.max_orphan_normatives
        ),
    )
    return QaReport(document_id, all(line.passed for line in lines), lines)
