"""What text a retrieval route actually indexes, decided once for both routes.

Task 3 surfaced the question and deliberately left it here. Searching `5.6.2`
reaches the three clauses that *cite* §5.6.2 and not §5.6.2 itself, because a
section number is a locator and never appears in its own body text. So a
question naming a section is unanswerable through retrieval unless the heading
joins the indexed text.

**The heading is included, and the reason is not that it tests better.** It
cannot be tested better yet: comparing the two needs gold, and no quality metric
may be computed in W2. What decides it is that the alternative fails a whole
class of question outright — "what does §5.6.2 require" returns the citations
instead of the clause — while the cost of including it is bounded and known: the
section path repeats across every clause in a section, raising the document
frequency of its words and making them cheaper, which is what BM25's IDF is for.

It is recorded as a versioned policy rather than a default, so the corpus
manifest binds it, W6 can report which one produced its numbers, and a later
change is a new corpus rather than a quiet edit. Both routes read from here, so
they cannot drift apart about what a unit is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import (
    CLAUSE_KIND,
    ClauseLimits,
    iter_clause_texts,
)
from specpilot.corpus.tables import TABLE_KIND, iter_table_rows
from specpilot.ingestion.rfc import RfcInput

CHUNKER_VERSION: Final = "rfc-clause-table/v1"


@dataclass(frozen=True, slots=True)
class IndexTextPolicy:
    """How a unit's indexable text is assembled. Bound by the corpus manifest."""

    version: str = "index-text/v1"
    include_section_heading: bool = True


@dataclass(frozen=True, slots=True)
class IndexUnit:
    """One retrievable thing, with its locator and both forms of its text.

    `text` is the source as written, for local display and excerpting. `indexed`
    is what the routes see. Keeping them apart means a change to indexing
    strategy cannot silently change what an excerpt shows a reader.
    """

    unit_id: str
    kind: str
    document_id: str
    document_version: str
    section_number: str | None
    section_path: str
    ordinal: int
    text: str
    indexed: str


def _assemble(
    section_number: str | None,
    section_path: str,
    text: str,
    policy: IndexTextPolicy,
) -> str:
    if not policy.include_section_heading:
        return text
    heading = " ".join(part for part in (section_number, section_path) if part)
    return f"{heading}\n{text}" if heading else text


def build_index_units(
    source: RfcInput,
    rfc_limits: RfcLimits,
    clause_limits: ClauseLimits,
    policy: IndexTextPolicy | None = None,
) -> tuple[IndexUnit, ...]:
    """Return every retrievable unit of one document, clauses and tables."""
    settings = policy or IndexTextPolicy()
    units: list[IndexUnit] = []

    for clause, text in iter_clause_texts(source, rfc_limits, clause_limits):
        units.append(
            IndexUnit(
                unit_id=clause.clause_id,
                kind=CLAUSE_KIND,
                document_id=clause.document_id,
                document_version=clause.document_version,
                section_number=clause.section_number,
                section_path=clause.section_path,
                ordinal=clause.ordinal,
                text=text,
                indexed=_assemble(
                    clause.section_number, clause.section_path, text, settings
                ),
            )
        )

    for table, rows in iter_table_rows(source, rfc_limits, clause_limits):
        # Rows are joined with the header first, so a cell reading "200" is
        # indexed near the word that says it is a status code.
        text = " ".join(cell for row in rows for cell in row if cell)
        units.append(
            IndexUnit(
                unit_id=table.table_id,
                kind=TABLE_KIND,
                document_id=table.document_id,
                document_version=table.document_version,
                section_number=table.section_number,
                section_path=table.section_path,
                ordinal=table.ordinal,
                text=text,
                indexed=_assemble(
                    table.section_number, table.section_path, text, settings
                ),
            )
        )

    return tuple(units)
