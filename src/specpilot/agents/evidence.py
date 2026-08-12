"""Bounded, deterministic execution of model-authored plans through MCP."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from specpilot.agents.contracts import (
    DirectClauseIds,
    ExpandReferencesStep,
    GetClauseStep,
    ToolCallSummary,
    ToolName,
    ToolPlan,
    ToolStep,
    validate_tool_plan,
)
from specpilot.answer.evidence import Evidence, build_evidence_from_unit
from specpilot.corpus.indexable import IndexUnit
from specpilot.mcp_server.client import McpEvidenceClient
from specpilot.mcp_server.contracts import (
    ExpandReferencesResult,
    GetClauseResult,
    GetTocResult,
    LookupTermResult,
    McpToolErrorCode,
    McpToolErrorDetail,
    SearchClausesResult,
)

_MAX_ATTEMPTS = 6


class UnitResolver(Protocol):
    def get_clause(self, clause_id: str) -> IndexUnit: ...


class EvidenceCollectionError(Exception):
    """A stable local integrity failure; raw MCP result text is never carried."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    evidence: tuple[Evidence, ...]
    calls: tuple[ToolCallSummary, ...]


@dataclass(frozen=True, slots=True)
class _DecodedResult:
    ids: tuple[str, ...]
    count: int
    evidence: Evidence | None = None


class EvidenceAgent:
    def __init__(
        self,
        client: McpEvidenceClient,
        units: UnitResolver,
    ) -> None:
        self._client = client
        self._units = units

    async def collect(
        self, plan: ToolPlan, corpus_manifest_id: str
    ) -> EvidenceResult:
        bounded = validate_tool_plan(plan)
        _require_corpus_scope(bounded, corpus_manifest_id)
        outputs: dict[str, tuple[str, ...]] = {}
        evidence: list[Evidence] = []
        calls: list[ToolCallSummary] = []
        attempts = 0

        for step in bounded.steps:
            invocations = _invocations(step, outputs)
            if invocations is None:
                calls.append(_unresolved_summary(step))
                break
            step_ids: list[str] = []
            for arguments in invocations:
                started = time.monotonic()
                retries = 0
                terminal_error: str | None = (
                    "tool_call_budget_exceeded"
                    if attempts >= _MAX_ATTEMPTS
                    else None
                )
                decoded: _DecodedResult | None = None
                while attempts < _MAX_ATTEMPTS:
                    attempts += 1
                    result = await self._client.call_tool(step.tool.value, arguments)
                    error_code = _error_code(result)
                    if error_code is None:
                        decoded = _decode_success(
                            step.tool,
                            result,
                            arguments,
                            corpus_manifest_id,
                            self._units,
                        )
                        break
                    terminal_error = error_code
                    if (
                        error_code == McpToolErrorCode.TOOL_TIMEOUT.value
                        and retries == 0
                        and attempts < _MAX_ATTEMPTS
                    ):
                        retries = 1
                        continue
                    break

                duration_ms = max(int((time.monotonic() - started) * 1000), 0)
                calls.append(
                    ToolCallSummary(
                        step_id=step.step_id,
                        tool=step.tool,
                        argument_keys=tuple(sorted(arguments)),
                        result_count=decoded.count if decoded is not None else 0,
                        duration_ms=duration_ms,
                        retry_count=retries,
                        error_code=None if decoded is not None else terminal_error,
                    )
                )
                if decoded is None:
                    return EvidenceResult(
                        evidence=_dedupe_evidence(evidence), calls=tuple(calls)
                    )
                step_ids.extend(decoded.ids)
                if decoded.evidence is not None:
                    evidence.append(decoded.evidence)
            outputs[step.step_id] = _dedupe_ids(step_ids)

        return EvidenceResult(evidence=_dedupe_evidence(evidence), calls=tuple(calls))


def _require_corpus_scope(plan: ToolPlan, corpus_manifest_id: str) -> None:
    if any(step.args.corpus_manifest_id != corpus_manifest_id for step in plan.steps):
        raise EvidenceCollectionError("invalid_corpus_scope")


def _invocations(
    step: ToolStep,
    outputs: Mapping[str, tuple[str, ...]],
) -> tuple[dict[str, Any], ...] | None:
    dumped = step.args.model_dump(mode="json")
    if not isinstance(step, (GetClauseStep, ExpandReferencesStep)):
        return (dumped,)
    clauses = step.args.clauses
    if isinstance(clauses, DirectClauseIds):
        clause_ids = _dedupe_ids(clauses.clause_ids)
    else:
        clause_ids = outputs.get(clauses.step_id, ())[: clauses.take]
        if not clause_ids:
            return None
    base = {key: value for key, value in dumped.items() if key != "clauses"}
    if isinstance(step, GetClauseStep):
        return tuple({**base, "clause_id": clause_id} for clause_id in clause_ids)
    return tuple({**base, "clause_ids": [clause_id]} for clause_id in clause_ids)


def _error_code(result: CallToolResult) -> str | None:
    if not result.isError:
        return None
    if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
        return McpToolErrorCode.BACKEND_UNAVAILABLE.value
    try:
        detail = McpToolErrorDetail.model_validate_json(result.content[0].text)
    except (ValidationError, ValueError):
        return McpToolErrorCode.BACKEND_UNAVAILABLE.value
    return detail.code.value


def _decode_success(
    tool: ToolName,
    result: CallToolResult,
    arguments: Mapping[str, Any],
    corpus_manifest_id: str,
    units: UnitResolver,
) -> _DecodedResult:
    structured = result.structuredContent
    if not isinstance(structured, dict):
        raise EvidenceCollectionError("invalid_tool_result")
    try:
        if tool is ToolName.SEARCH_CLAUSES:
            parsed = SearchClausesResult.model_validate(structured)
            wanted_documents = set(cast(list[str], arguments["document_ids"]))
            if any(
                hit.corpus_manifest_id != corpus_manifest_id
                or hit.document_id not in wanted_documents
                for hit in parsed.hits
            ):
                raise EvidenceCollectionError("invalid_tool_result_scope")
            ids = tuple(hit.clause_id for hit in parsed.hits)
            return _DecodedResult(ids=_dedupe_ids(ids), count=len(parsed.hits))
        if tool is ToolName.GET_CLAUSE:
            parsed_clause = GetClauseResult.model_validate(structured)
            evidence = _evidence_from_clause(
                parsed_clause, arguments, corpus_manifest_id, units
            )
            return _DecodedResult(
                ids=(parsed_clause.clause_id,), count=1, evidence=evidence
            )
        if tool is ToolName.GET_TOC:
            toc = GetTocResult.model_validate(structured)
            return _DecodedResult(ids=(), count=len(toc.nodes))
        if tool is ToolName.EXPAND_REFERENCES:
            references = ExpandReferencesResult.model_validate(structured)
            return _DecodedResult(
                ids=_dedupe_ids(references.clause_ids),
                count=len(references.clause_ids),
            )
        definitions = LookupTermResult.model_validate(structured)
        return _DecodedResult(
            ids=_dedupe_ids(definitions.definition_clause_ids),
            count=len(definitions.definition_clause_ids),
        )
    except ValidationError:
        raise EvidenceCollectionError("invalid_tool_result") from None


def _evidence_from_clause(
    result: GetClauseResult,
    arguments: Mapping[str, Any],
    corpus_manifest_id: str,
    units: UnitResolver,
) -> Evidence:
    if (
        result.corpus_manifest_id != corpus_manifest_id
        or result.document_id != arguments.get("document_id")
        or result.clause_id != arguments.get("clause_id")
    ):
        raise EvidenceCollectionError("invalid_tool_result_scope")
    try:
        unit = units.get_clause(result.clause_id)
    except KeyError:
        raise EvidenceCollectionError("invalid_tool_result_scope") from None
    content_hash = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
    if (
        unit.document_id != result.document_id
        or unit.section_number != result.section_number
        or unit.section_path != result.section_path
        or unit.text != result.text
        or content_hash != result.content_hash
    ):
        raise EvidenceCollectionError("invalid_tool_result")
    return build_evidence_from_unit(unit, corpus_manifest_id=corpus_manifest_id)


def _unresolved_summary(step: ToolStep) -> ToolCallSummary:
    return ToolCallSummary(
        step_id=step.step_id,
        tool=step.tool,
        argument_keys=tuple(sorted(step.args.model_dump())),
        result_count=0,
        duration_ms=0,
        retry_count=0,
        error_code="invalid_reference",
    )


def _dedupe_ids(ids: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(cast(tuple[str, ...] | list[str], ids)))


def _dedupe_evidence(items: list[Evidence]) -> tuple[Evidence, ...]:
    unique: list[Evidence] = []
    clause_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for item in items:
        if (
            item.disclosed.clause_id in clause_ids
            or item.disclosed.content_hash in evidence_ids
        ):
            continue
        clause_ids.add(item.disclosed.clause_id)
        evidence_ids.add(item.disclosed.content_hash)
        unique.append(item)
    return tuple(unique)


__all__ = [
    "EvidenceAgent",
    "EvidenceCollectionError",
    "EvidenceResult",
    "UnitResolver",
]
