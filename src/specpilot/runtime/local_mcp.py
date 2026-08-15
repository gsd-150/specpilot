"""In-process corpus tools for the single-case L2 author run.

The worker reaches the corpus through a Streamable MCP client over HTTP; the
author-run CLI has no server to reach and no reason to start one. This module
serves the same five read-only tools directly from an in-memory ``McpToolServices``,
so the evidence agent and the recovery runner keep their exact production shapes
while the transport boundary is replaced by a synchronous local dispatch.

The service construction mirrors ``mcp_server.runtime.load_runtime_services``:
the corpus is loaded from the already-verified pooled sources, then bound to the
frozen corpus manifest through the same size, digest, tokenizer-version, and
BM25-fingerprint checks. A mismatch is a ``LocalToolServicesError``, never a
silently different corpus.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from specpilot.contracts.corpus_manifest import CorpusManifest
from specpilot.contracts.manifests import RfcSourceManifest
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import derived_corpus_sha256
from specpilot.corpus.tool_metadata import build_rfc_tool_metadata
from specpilot.ingestion.rfc import VerifiedRfc
from specpilot.mcp_server.contracts import (
    ExpandReferencesRequest,
    GetClauseRequest,
    GetTocRequest,
    LookupTermRequest,
    McpToolError,
    McpToolErrorCode,
    McpToolErrorDetail,
    SearchClausesRequest,
)
from specpilot.mcp_server.services import McpToolServices, SearchBackendHit
from specpilot.retrieval.bm25 import TOKENIZER_VERSION, Bm25Index, Bm25Parameters
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit


class LocalToolServicesError(ValueError):
    """A stable corpus-binding failure; no clause text is ever carried."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _LocalBm25SearchBackend:
    """The sparse route over the frozen corpus, built once and reused."""

    corpus: LocalCorpus
    index: Bm25Index

    def search(
        self,
        query: str,
        *,
        corpus_manifest_id: str,
        document_ids: tuple[str, ...],
        normative_levels: tuple[str, ...],
        limit: int,
    ) -> Sequence[SearchBackendHit]:
        del normative_levels, limit
        wanted_documents = set(document_ids)
        return tuple(
            SearchBackendHit(
                locator=locator_for_unit(
                    corpus_manifest_id, self.corpus.get_clause(hit.unit_id)
                ),
                score=hit.score,
            )
            for hit in self.index.search(query, self.corpus.unit_count())
            if self.corpus.get_clause(hit.unit_id).document_id in wanted_documents
        )


def build_local_tool_services(
    sources: tuple[tuple[RfcSourceManifest, VerifiedRfc], ...],
    corpus_manifest: CorpusManifest,
) -> McpToolServices:
    """Build the frozen local corpus and its tools, bound to the corpus manifest.

    Every check here is the same fail-closed binding ``load_runtime_services``
    applies in the deployed worker: a corpus that disagrees with the manifest on
    size, digest, tokenizer version, or BM25 fingerprint is refused rather than
    run against the wrong document.
    """
    clause_limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
    documents = tuple((document, clause_limits) for _, document in sources)
    corpus = LocalCorpus.load(documents, RfcLimits())
    if corpus.unit_count() != corpus_manifest.point_count:
        raise LocalToolServicesError("corpus_size_mismatch")
    if derived_corpus_sha256(corpus.units()) != corpus_manifest.derived_corpus_sha256:
        raise LocalToolServicesError("corpus_digest_mismatch")
    if corpus_manifest.bm25.tokenizer_version != TOKENIZER_VERSION:
        raise LocalToolServicesError("tokenizer_version_mismatch")
    index = Bm25Index.build(
        corpus.indexable(),
        Bm25Parameters(k1=corpus_manifest.bm25.k1, b=corpus_manifest.bm25.b),
    )
    if index.fingerprint != corpus_manifest.bm25.index_fingerprint:
        raise LocalToolServicesError("bm25_fingerprint_mismatch")
    metadata = build_rfc_tool_metadata(
        corpus_manifest_id=corpus_manifest.manifest_id,
        documents=documents,
        units=corpus.units(),
        rfc_limits=RfcLimits(),
    )
    return McpToolServices(
        corpus=corpus,
        search_backend=_LocalBm25SearchBackend(corpus, index),
        tool_metadata=metadata,
    )


class LocalMcpEvidenceClient:
    """Serve the five corpus tools in-process, returning MCP-shaped results.

    The evidence agent and recovery runner consume ``McpEvidenceClient`` and
    read ``CallToolResult.structuredContent`` / ``isError``. This class builds
    exactly those values from the local services, so the only difference from a
    deployed run is that no HTTP request crosses a socket.
    """

    def __init__(self, services: McpToolServices) -> None:
        self._services = services

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        payload = arguments or {}
        if not isinstance(payload, dict):
            return _error_result(
                McpToolErrorCode.INVALID_ARGUMENT,
                "arguments",
                "Use a mapping of tool arguments.",
            )
        try:
            result = _dispatch(self._services, name, payload)
        except McpToolError as error:
            return _error_result(error.code, error.field, error.correction)
        except ValidationError:
            return _error_result(
                McpToolErrorCode.INVALID_ARGUMENT,
                "arguments",
                "Use the listed fields and their documented bounds.",
            )
        return CallToolResult(
            isError=False,
            content=[],
            structuredContent=result.model_dump(mode="json"),
        )


def _dispatch(services: McpToolServices, name: str, arguments: dict[str, Any]) -> Any:
    """Validate the raw mapping and run one read-only service method."""
    if name == "search_clauses":
        return services.search_clauses(SearchClausesRequest.model_validate(arguments))
    if name == "get_clause":
        return services.get_clause(GetClauseRequest.model_validate(arguments))
    if name == "get_toc":
        return services.get_toc(GetTocRequest.model_validate(arguments))
    if name == "expand_references":
        return services.expand_references(
            ExpandReferencesRequest.model_validate(arguments)
        )
    if name == "lookup_term":
        return services.lookup_term(LookupTermRequest.model_validate(arguments))
    raise McpToolError(
        McpToolErrorCode.INVALID_ARGUMENT,
        "tool",
        "Use a tool from the listed catalog.",
    )


def _error_result(
    code: McpToolErrorCode,
    field: str,
    correction: str,
) -> CallToolResult:
    detail = McpToolErrorDetail(code=code, field=field, correction=correction)
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=detail.model_dump_json())],
    )


__all__ = [
    "LocalMcpEvidenceClient",
    "LocalToolServicesError",
    "build_local_tool_services",
]
