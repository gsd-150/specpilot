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

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from specpilot.contracts.egress import TocNode
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.indexable import IndexTextPolicy, IndexUnit, build_index_units
from specpilot.corpus.walk import document_identity, parse_verified, sections
from specpilot.ingestion.rfc import RfcInput, ensure_verified_rfc


@dataclass(frozen=True, slots=True)
class LocalCorpus:
    """Every unit of the frozen corpus, in memory, on this machine."""

    _units: dict[str, IndexUnit]
    _toc: tuple[tuple[str, str | None, str], ...]
    _source_hashes: tuple[tuple[str, str], ...]

    @classmethod
    def load(
        cls,
        documents: Sequence[tuple[RfcInput, ClauseLimits]],
        rfc_limits: RfcLimits,
        policy: IndexTextPolicy | None = None,
    ) -> LocalCorpus:
        """Load one or more frozen documents, each with its own exclusions."""
        units: dict[str, IndexUnit] = {}
        toc: list[tuple[str, str | None, str]] = []
        source_hashes: list[tuple[str, str]] = []
        for source, clause_limits in documents:
            verified = ensure_verified_rfc(source, rfc_limits)
            root = parse_verified(verified, rfc_limits)
            document_id, _ = document_identity(root)
            if document_id in {item[0] for item in source_hashes}:
                raise ValueError(f"duplicate document id {document_id!r}")
            source_hashes.append(
                (document_id, verified.inspection.document_sha256)
            )
            for unit in build_index_units(
                verified, rfc_limits, clause_limits, policy
            ):
                if unit.unit_id in units:
                    raise ValueError(f"duplicate unit id {unit.unit_id!r}")
                units[unit.unit_id] = unit
            for section in sections(root):
                if section.anchor in clause_limits.excluded_sections:
                    continue
                toc.append((document_id, section.number, section.path))
        return cls(
            _units=units,
            _toc=tuple(toc),
            _source_hashes=tuple(source_hashes),
        )

    def unit_ids(self) -> Iterable[str]:
        return self._units.keys()

    def unit_count(self) -> int:
        return len(self._units)

    def units(self) -> tuple[IndexUnit, ...]:
        """Return units in canonical document and construction order."""
        return tuple(self._units.values())

    def document_ids(self) -> tuple[str, ...]:
        return tuple(document_id for document_id, _ in self._source_hashes)

    def source_hashes(self) -> tuple[tuple[str, str], ...]:
        return self._source_hashes

    def inventory_hash(self) -> str:
        payload = {
            "sources": self._source_hashes,
            "units": [
                (
                    unit.unit_id,
                    hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
                )
                for unit in self._units.values()
            ],
        }
        encoded = json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get_clause(self, unit_id: str) -> IndexUnit:
        """Return the whole unit. Raises rather than returning a partial one."""
        return self._units[unit_id]

    def indexable(self) -> tuple[tuple[str, str], ...]:
        """`(unit_id, indexed_text)` pairs, the input both routes take."""
        return tuple((unit.unit_id, unit.indexed) for unit in self._units.values())

    def get_toc(
        self,
        section: str | None = None,
        *,
        document_id: str | None = None,
    ) -> tuple[TocNode, ...]:
        """Return section titles, optionally narrowed to one subtree.

        Titles only — `TocNode` has no field that can hold body text, which is
        what makes "the TOC never carries prose" a property of the type rather
        than of this function's care.
        """
        nodes: list[TocNode] = []
        for node_document_id, number, path in self._toc:
            if document_id is not None and node_document_id != document_id:
                continue
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
