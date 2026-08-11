"""Read-only business services behind the future FastMCP wrappers."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from specpilot.corpus.clauses import CLAUSE_KIND
from specpilot.corpus.indexable import IndexUnit
from specpilot.corpus.tool_metadata import (
    InvalidToolReferenceError,
    RfcToolMetadata,
    ToolMetadataIntegrityError,
)
from specpilot.mcp_server.contracts import (
    ExpandReferencesRequest,
    ExpandReferencesResult,
    GetClauseRequest,
    GetClauseResult,
    GetTocRequest,
    GetTocResult,
    LookupTermRequest,
    LookupTermResult,
    McpToolError,
    McpToolErrorCode,
    SearchClauseHit,
    SearchClausesRequest,
    SearchClausesResult,
)
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import RetrievalLocator


@dataclass(frozen=True, slots=True)
class SearchBackendHit:
    locator: RetrievalLocator
    score: float


class SearchBackend(Protocol):
    def search(
        self,
        query: str,
        *,
        corpus_manifest_id: str,
        document_ids: tuple[str, ...],
        normative_levels: tuple[str, ...],
        limit: int,
    ) -> Sequence[SearchBackendHit]: ...


@dataclass(frozen=True, slots=True)
class McpToolServices:
    corpus: LocalCorpus
    search_backend: SearchBackend
    tool_metadata: RfcToolMetadata

    def inventory_hash(self) -> str:
        joined = f"{self.corpus.inventory_hash()}\x1f{self.tool_metadata.metadata_hash}"
        return hashlib.sha256(joined.encode("ascii")).hexdigest()

    def search_clauses(self, request: SearchClausesRequest) -> SearchClausesResult:
        self._verify_request(request.corpus_manifest_id, request.document_ids)
        try:
            backend_hits = self.search_backend.search(
                request.query,
                corpus_manifest_id=request.corpus_manifest_id,
                document_ids=request.document_ids,
                normative_levels=request.normative_levels,
                limit=request.limit,
            )
        except TimeoutError:
            raise McpToolError(
                McpToolErrorCode.TOOL_TIMEOUT,
                "query",
                "Retry the bounded local search once.",
            ) from None
        except Exception:
            raise McpToolError(
                McpToolErrorCode.BACKEND_UNAVAILABLE,
                "search_backend",
                "Retry after the local search backend is available.",
            ) from None

        wanted_documents = set(request.document_ids)
        wanted_levels = set(request.normative_levels)
        accepted: list[SearchBackendHit] = []
        seen: set[tuple[object, ...]] = set()
        for hit in backend_hits:
            locator = hit.locator
            if (
                locator.corpus_manifest_id != request.corpus_manifest_id
                or locator.document_id not in wanted_documents
                or not math.isfinite(hit.score)
            ):
                raise McpToolError(
                    McpToolErrorCode.BACKEND_UNAVAILABLE,
                    "search_backend",
                    "Reload the bounded local search inventory.",
                )
            try:
                unit = self.corpus.get_clause(locator.clause_id)
                levels = (
                    set(self.tool_metadata.normative_levels(locator.clause_id))
                    if unit.kind == CLAUSE_KIND
                    else set()
                )
            except (KeyError, ToolMetadataIntegrityError):
                raise McpToolError(
                    McpToolErrorCode.BACKEND_UNAVAILABLE,
                    "search_backend",
                    "Reload the bounded local search inventory.",
                ) from None
            if unit.document_id != locator.document_id:
                raise McpToolError(
                    McpToolErrorCode.BACKEND_UNAVAILABLE,
                    "search_backend",
                    "Reload the bounded local search inventory.",
                )
            if wanted_levels and not wanted_levels.intersection(levels):
                continue
            if locator.dedupe_key in seen:
                continue
            seen.add(locator.dedupe_key)
            accepted.append(hit)

        accepted.sort(key=lambda hit: (-hit.score, *hit.locator.stable_tie_key))
        results: list[SearchClauseHit] = []
        for hit in accepted[: request.limit]:
            unit = self.corpus.get_clause(hit.locator.clause_id)
            results.append(
                SearchClauseHit(
                    corpus_manifest_id=request.corpus_manifest_id,
                    document_id=unit.document_id,
                    clause_id=unit.unit_id,
                    section_number=unit.section_number,
                    section_path=unit.section_path,
                    content_hash=hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
                    score=hit.score,
                )
            )
        return SearchClausesResult(hits=tuple(results))

    def get_clause(self, request: GetClauseRequest) -> GetClauseResult:
        self._verify_request(request.corpus_manifest_id, (request.document_id,))
        unit = self._scoped_clause(request.document_id, request.clause_id)
        return GetClauseResult(
            corpus_manifest_id=request.corpus_manifest_id,
            document_id=unit.document_id,
            clause_id=unit.unit_id,
            section_number=unit.section_number,
            section_path=unit.section_path,
            content_hash=hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
            text=unit.text,
        )

    def get_toc(self, request: GetTocRequest) -> GetTocResult:
        self._verify_request(request.corpus_manifest_id, (request.document_id,))
        return GetTocResult(
            nodes=self.corpus.get_toc(document_id=request.document_id)[: request.limit]
        )

    def expand_references(
        self, request: ExpandReferencesRequest
    ) -> ExpandReferencesResult:
        self._verify_request(request.corpus_manifest_id, (request.document_id,))
        source_ids = set(request.clause_ids)
        expanded: list[str] = []
        for clause_id in request.clause_ids:
            self._scoped_clause(request.document_id, clause_id)
            try:
                candidates = self.tool_metadata.expand(clause_id, limit=3)
            except InvalidToolReferenceError:
                raise McpToolError(
                    McpToolErrorCode.INVALID_REFERENCE,
                    "clause_ids",
                    "Choose a clause with resolved local references.",
                ) from None
            for candidate in candidates:
                if candidate in source_ids or candidate in expanded:
                    continue
                self._scoped_clause(request.document_id, candidate)
                expanded.append(candidate)
                if len(expanded) == 3:
                    return ExpandReferencesResult(clause_ids=tuple(expanded))
        return ExpandReferencesResult(clause_ids=tuple(expanded))

    def lookup_term(self, request: LookupTermRequest) -> LookupTermResult:
        self._verify_request(request.corpus_manifest_id, (request.document_id,))
        clause_ids = self.tool_metadata.lookup(
            request.term, document_id=request.document_id
        )
        if not clause_ids:
            raise McpToolError(
                McpToolErrorCode.NOT_FOUND,
                "term",
                "Use a defined term from the selected document.",
            )
        for clause_id in clause_ids:
            self._scoped_clause(request.document_id, clause_id)
        return LookupTermResult(definition_clause_ids=clause_ids)

    def _verify_request(
        self, corpus_manifest_id: str, document_ids: tuple[str, ...]
    ) -> None:
        try:
            self.tool_metadata.verify_integrity()
        except ToolMetadataIntegrityError:
            raise McpToolError(
                McpToolErrorCode.BACKEND_UNAVAILABLE,
                "tool_metadata",
                "Reload the local corpus metadata.",
            ) from None
        if (
            self.tool_metadata.source_hashes() != self.corpus.source_hashes()
            or self.tool_metadata.clause_ids()
            != tuple(
                unit.unit_id for unit in self.corpus.units() if unit.kind == "clause"
            )
        ):
            raise McpToolError(
                McpToolErrorCode.BACKEND_UNAVAILABLE,
                "tool_metadata",
                "Reload the local corpus metadata.",
            )
        if corpus_manifest_id != self.tool_metadata.corpus_manifest_id:
            raise McpToolError(
                McpToolErrorCode.INVALID_ARGUMENT,
                "corpus_manifest_id",
                "Use the loaded corpus manifest ID.",
            )
        available = set(self.tool_metadata.document_ids())
        if any(document_id not in available for document_id in document_ids):
            raise McpToolError(
                McpToolErrorCode.NOT_FOUND,
                "document_id",
                "Use a document ID from the loaded corpus scope.",
            )

    def _scoped_clause(self, document_id: str, clause_id: str) -> IndexUnit:
        try:
            unit = self.corpus.get_clause(clause_id)
        except KeyError:
            raise McpToolError(
                McpToolErrorCode.NOT_FOUND,
                "clause_id",
                "Use a clause ID from the selected document.",
            ) from None
        if unit.document_id != document_id:
            raise McpToolError(
                McpToolErrorCode.NOT_FOUND,
                "clause_id",
                "Use a clause ID from the selected document.",
            )
        return unit
