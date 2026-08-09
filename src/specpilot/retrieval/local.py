"""The local reads §5.1 defines: whole clauses and the section tree.

Local is the operative word. `get_clause` returns the clause as written,
because its purpose is local verification — deterministic citation checking,
the author's own adjudication, the pooling audit — and a truncated clause would
give every one of those a shortened text to check. The 512-token excerpt cap
governs what may *leave*, and that is the enforcer's job. Two different
questions, answered in two different places.

`get_toc` is the same shape. §5.1 caps the model at 12 nodes per call and 24 per
run, and W0's egress policy already carries both figures. Capping the local read
as well would mean the author cannot see the table of contents of a document
they are annotating.

Nothing here sends anything. The candidate pool, the full tree, and the clause
text stay on the machine, which is what §3.2's data minimisation asks for and
what makes it true rather than aspirational.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from specpilot.contracts.egress import TocNode
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.indexable import IndexTextPolicy, IndexUnit, build_index_units
from specpilot.corpus.walk import parse_verified, sections
from specpilot.ingestion.rfc import RfcInput


@dataclass(frozen=True, slots=True)
class LocalCorpus:
    """Every unit of the frozen corpus, in memory, on this machine."""

    _units: dict[str, IndexUnit]
    _toc: tuple[tuple[str | None, str], ...]

    @classmethod
    def load(
        cls,
        documents: Sequence[tuple[RfcInput, ClauseLimits]],
        rfc_limits: RfcLimits,
        policy: IndexTextPolicy | None = None,
    ) -> LocalCorpus:
        """Load one or more frozen documents, each with its own exclusions."""
        units: dict[str, IndexUnit] = {}
        toc: list[tuple[str | None, str]] = []
        for source, clause_limits in documents:
            for unit in build_index_units(source, rfc_limits, clause_limits, policy):
                if unit.unit_id in units:
                    raise ValueError(f"duplicate unit id {unit.unit_id!r}")
                units[unit.unit_id] = unit
            root = parse_verified(source, rfc_limits)
            for section in sections(root):
                if section.anchor in clause_limits.excluded_sections:
                    continue
                toc.append((section.number, section.path))
        return cls(_units=units, _toc=tuple(toc))

    def unit_ids(self) -> Iterable[str]:
        return self._units.keys()

    def unit_count(self) -> int:
        return len(self._units)

    def units(self) -> tuple[IndexUnit, ...]:
        """Return units in canonical document and construction order."""
        return tuple(self._units.values())

    def get_clause(self, unit_id: str) -> IndexUnit:
        """Return the whole unit. Raises rather than returning a partial one."""
        return self._units[unit_id]

    def indexable(self) -> tuple[tuple[str, str], ...]:
        """`(unit_id, indexed_text)` pairs, the input both routes take."""
        return tuple((unit.unit_id, unit.indexed) for unit in self._units.values())

    def get_toc(self, section: str | None = None) -> tuple[TocNode, ...]:
        """Return section titles, optionally narrowed to one subtree.

        Titles only — `TocNode` has no field that can hold body text, which is
        what makes "the TOC never carries prose" a property of the type rather
        than of this function's care.
        """
        nodes: list[TocNode] = []
        for number, path in self._toc:
            if section is not None and not _within(number, section):
                continue
            title = path.rsplit(" > ", 1)[-1] if path else ""
            if not title:
                continue
            nodes.append(TocNode(node_id=number or title, title=title))
        return tuple(nodes)


def _within(number: str | None, prefix: str) -> bool:
    """Compared component by component, so `2` does not select `20`."""
    if number is None:
        return False
    parts, wanted = number.split("."), prefix.split(".")
    return parts[: len(wanted)] == wanted
