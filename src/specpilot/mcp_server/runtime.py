"""Fail-closed construction of MCP services from frozen runtime artifacts."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from specpilot.contracts.manifests import RfcSourceManifest, Sha256
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import derived_corpus_sha256
from specpilot.corpus.tool_metadata import build_rfc_tool_metadata
from specpilot.corpus.walk import document_identity
from specpilot.ingestion.rfc import (
    VerifiedRfc,
    read_rfc_snapshot,
    verify_rfc_snapshot,
)
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore
from specpilot.mcp_server.services import McpToolServices, SearchBackendHit
from specpilot.retrieval.bm25 import TOKENIZER_VERSION, Bm25Index, Bm25Parameters
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
LOOPBACK_ORIGINS = (
    "http://127.0.0.1",
    "http://localhost",
    "http://[::1]",
)
_ExactIdentity = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class _RuntimeSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: Sha256
    xml_path: Path


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_manifest_dir: Path
    corpus_manifest_id: Sha256
    source_manifest_dir: Path
    sources: Annotated[tuple[_RuntimeSource, ...], Field(min_length=1, max_length=12)]
    allowed_hosts: Annotated[tuple[_ExactIdentity, ...], Field(min_length=1)] = (
        LOOPBACK_HOSTS
    )
    allowed_origins: Annotated[tuple[_ExactIdentity, ...], Field(min_length=1)] = (
        LOOPBACK_ORIGINS
    )

    @model_validator(mode="after")
    def _validate_exact_transport_identities(self) -> Self:
        if len({source.manifest_id for source in self.sources}) != len(self.sources):
            raise ValueError("runtime source manifests must be unique")
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts):
            raise ValueError("allowed hosts must be unique")
        if len(set(self.allowed_origins)) != len(self.allowed_origins):
            raise ValueError("allowed origins must be unique")
        for host in self.allowed_hosts:
            if "*" in host or host != host.strip() or "/" in host:
                raise ValueError("allowed hosts must be exact identities")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                "*" in origin
                or origin != origin.strip()
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("allowed origins must be exact HTTP origins")
        return self


@dataclass(frozen=True, slots=True)
class _RuntimeBm25SearchBackend:
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


def load_runtime_config() -> RuntimeConfig:
    values: dict[str, object] = {
        "corpus_manifest_dir": os.environ.get(
            "SPECPILOT_MCP_CORPUS_MANIFEST_DIR"
        ),
        "corpus_manifest_id": os.environ.get("SPECPILOT_MCP_CORPUS_MANIFEST_ID"),
        "source_manifest_dir": os.environ.get(
            "SPECPILOT_MCP_SOURCE_MANIFEST_DIR"
        ),
        "sources": json.loads(os.environ.get("SPECPILOT_MCP_SOURCES_JSON", "[]")),
    }
    allowed_hosts = os.environ.get("SPECPILOT_MCP_ALLOWED_HOSTS_JSON")
    if allowed_hosts is not None:
        values["allowed_hosts"] = json.loads(allowed_hosts)
    allowed_origins = os.environ.get("SPECPILOT_MCP_ALLOWED_ORIGINS_JSON")
    if allowed_origins is not None:
        values["allowed_origins"] = json.loads(allowed_origins)
    return RuntimeConfig.model_validate(values)


def load_runtime_services(config: RuntimeConfig) -> McpToolServices:
    corpus_manifest = CorpusManifestStore(config.corpus_manifest_dir).read(
        config.corpus_manifest_id
    )
    source_store = ManifestStore(config.source_manifest_dir)
    resolved: list[tuple[RfcSourceManifest, VerifiedRfc]] = []
    for binding in config.sources:
        manifest = source_store.read_source(binding.manifest_id)
        if not isinstance(manifest, RfcSourceManifest):
            raise ValueError("runtime source is not an RFC manifest")
        snapshot = read_rfc_snapshot(binding.xml_path, RfcLimits())
        if snapshot.document_sha256 != manifest.xml_sha256:
            raise ValueError("runtime source hash does not match its manifest")
        document = verify_rfc_snapshot(snapshot)
        document_id, document_version = document_identity(document.root)
        if (
            document_id != manifest.document_id
            or document_version != manifest.document_version
        ):
            raise ValueError("runtime source identity does not match its manifest")
        resolved.append((manifest, document))
    resolved.sort(
        key=lambda item: (
            item[0].document_id,
            item[0].document_version,
            item[0].manifest_id,
        )
    )
    if tuple(manifest.manifest_id for manifest, _ in resolved) != (
        corpus_manifest.source_manifest_ids
    ):
        raise ValueError("runtime sources do not match the corpus manifest")

    clause_limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
    documents = tuple((document, clause_limits) for _, document in resolved)
    corpus = LocalCorpus.load(documents, RfcLimits())
    if corpus.unit_count() != corpus_manifest.point_count:
        raise ValueError("runtime corpus size does not match the corpus manifest")
    if derived_corpus_sha256(corpus.units()) != corpus_manifest.derived_corpus_sha256:
        raise ValueError("runtime corpus digest does not match the corpus manifest")
    if corpus_manifest.bm25.tokenizer_version != TOKENIZER_VERSION:
        raise ValueError("runtime BM25 tokenizer does not match the corpus manifest")
    index = Bm25Index.build(
        corpus.indexable(),
        Bm25Parameters(k1=corpus_manifest.bm25.k1, b=corpus_manifest.bm25.b),
    )
    if index.fingerprint != corpus_manifest.bm25.index_fingerprint:
        raise ValueError("runtime BM25 index does not match the corpus manifest")
    metadata = build_rfc_tool_metadata(
        corpus_manifest_id=corpus_manifest.manifest_id,
        documents=documents,
        units=corpus.units(),
        rfc_limits=RfcLimits(),
    )
    return McpToolServices(
        corpus=corpus,
        search_backend=_RuntimeBm25SearchBackend(corpus, index),
        tool_metadata=metadata,
    )
