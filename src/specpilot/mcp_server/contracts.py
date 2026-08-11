"""Typed, bounded contracts for the five local corpus tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from specpilot.contracts.egress import TocNode
from specpilot.contracts.manifests import Identifier, Sha256

ToolQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
ToolTerm = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
Correction = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class McpToolErrorCode(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    INVALID_REFERENCE = "invalid_reference"
    TOOL_TIMEOUT = "tool_timeout"
    BACKEND_UNAVAILABLE = "backend_unavailable"


class McpToolErrorDetail(_FrozenModel):
    code: McpToolErrorCode
    field: Identifier
    correction: Correction


class McpToolError(Exception):
    """A closed, serializable error with no raw exception or source content."""

    def __init__(
        self,
        code: McpToolErrorCode | str,
        field: str,
        correction: str,
    ) -> None:
        self.detail = McpToolErrorDetail(
            code=McpToolErrorCode(code),
            field=field,
            correction=correction,
        )
        super().__init__(self.detail.model_dump_json())

    @property
    def code(self) -> McpToolErrorCode:
        return self.detail.code

    @property
    def field(self) -> str:
        return self.detail.field

    @property
    def correction(self) -> str:
        return self.detail.correction


class SearchClausesRequest(_FrozenModel):
    query: ToolQuery
    corpus_manifest_id: Sha256
    document_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=12)]
    normative_levels: Annotated[tuple[Identifier, ...], Field(max_length=5)] = ()
    limit: Annotated[int, Field(ge=1, le=20)]


class GetClauseRequest(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    clause_id: Identifier


class GetTocRequest(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    limit: Annotated[int, Field(ge=1, le=12)]


class ExpandReferencesRequest(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    clause_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=3)]


class LookupTermRequest(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    term: ToolTerm


class SearchClauseHit(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    clause_id: Identifier
    section_number: Identifier | None
    section_path: str
    content_hash: Sha256
    score: float


class SearchClausesResult(_FrozenModel):
    hits: Annotated[tuple[SearchClauseHit, ...], Field(max_length=20)]


class GetClauseResult(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    clause_id: Identifier
    section_number: Identifier | None
    section_path: str
    content_hash: Sha256
    text: str


class GetTocResult(_FrozenModel):
    nodes: Annotated[tuple[TocNode, ...], Field(max_length=12)]


class ExpandReferencesResult(_FrozenModel):
    clause_ids: Annotated[tuple[Identifier, ...], Field(max_length=3)]


class LookupTermResult(_FrozenModel):
    definition_clause_ids: tuple[Identifier, ...]
