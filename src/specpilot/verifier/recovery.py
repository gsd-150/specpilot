"""One bounded, deterministic L2 evidence recovery.

Recovery is deliberately a local consequence of a closed verifier fault.  It
does not ask a model to explain or repair itself: it selects at most one MCP
retrieval chain, counts every MCP call against the run's eight-call allowance,
and turns a returned clause back into Evidence only through the frozen corpus.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, model_validator

from specpilot.agents.contracts import ToolCallSummary, ToolName
from specpilot.agents.evidence import (
    EvidenceCollectionError,
    _decode_success,
    _dedupe_evidence,
    _error_code,
)
from specpilot.answer.evidence import Evidence
from specpilot.contracts.manifests import Identifier, Sha256, _FrozenModel
from specpilot.contracts.verdict import SemanticReason
from specpilot.corpus.indexable import IndexUnit
from specpilot.mcp_server.client import McpEvidenceClient
from specpilot.retrieval.local import LocalCorpus
from specpilot.verifier.deterministic import DeterministicFault

_L2_ATTEMPT_BUDGET = 8


class RecoveryKind(StrEnum):
    SCOPED_SEARCH = "scoped_search"
    GET_CLAUSE = "get_clause"
    EXPAND_REFERENCES = "expand_references"


class RecoveryRequest(_FrozenModel):
    """Sanitized recovery input safe to checkpoint or trace.

    Claim prose is intentionally not a field.  ``execute_recovery`` accepts it
    as an ephemeral argument only because a local search needs query terms.
    """

    kind: RecoveryKind
    claim_id: Sha256
    reason_code: Identifier
    source_clause_id: Identifier | None
    corpus_manifest_id: Sha256
    allowed_document_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    remaining_attempts: Annotated[int, Field(ge=0, le=_L2_ATTEMPT_BUDGET)]

    @model_validator(mode="after")
    def _require_direct_source_for_direct_recovery(self) -> RecoveryRequest:
        if (
            self.kind in {RecoveryKind.GET_CLAUSE, RecoveryKind.EXPAND_REFERENCES}
            and self.source_clause_id is None
        ):
            raise ValueError("direct recovery requires source_clause_id")
        return self


@dataclass(frozen=True, slots=True)
class RecoverySelection:
    kind: RecoveryKind
    reason_code: str


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    evidence: tuple[Evidence, ...]
    calls: tuple[ToolCallSummary, ...]
    attempts_used: int

    @property
    def call(self) -> ToolCallSummary:
        """Compatibility view of the terminal recovery call.

        A scoped action records search and follow-up get-clause separately; the
        final call preserves the singular shape used by early orchestration.
        """
        return self.calls[-1]


def select_recovery(
    reasons: Iterable[DeterministicFault | SemanticReason],
    *,
    source_clause_id: str | None,
    recovery_attempted: bool = False,
    remaining_attempts: int = 2,
) -> RecoverySelection | None:
    """Select one action from closed verifier reason codes, or no action."""
    if recovery_attempted or remaining_attempts <= 0:
        return None
    faults = tuple(reasons)
    if any(fault is DeterministicFault.NOT_DISCLOSED for fault in faults):
        return RecoverySelection(
            RecoveryKind.SCOPED_SEARCH, DeterministicFault.NOT_DISCLOSED.value
        )
    if any(fault is DeterministicFault.DOCUMENT_SCOPE_MISMATCH for fault in faults):
        return RecoverySelection(
            RecoveryKind.SCOPED_SEARCH,
            DeterministicFault.DOCUMENT_SCOPE_MISMATCH.value,
        )
    if source_clause_id is not None and any(
        fault is DeterministicFault.CONTENT_HASH_MISMATCH for fault in faults
    ):
        return RecoverySelection(
            RecoveryKind.GET_CLAUSE,
            DeterministicFault.CONTENT_HASH_MISMATCH.value,
        )
    if source_clause_id is not None and any(
        reason is SemanticReason.EXCEPTION_MISSING for reason in faults
    ):
        return RecoverySelection(
            RecoveryKind.EXPAND_REFERENCES,
            SemanticReason.EXCEPTION_MISSING.value,
        )
    return None


async def execute_recovery(
    request: RecoveryRequest,
    *,
    claim_text: str,
    client: McpEvidenceClient,
    corpus: LocalCorpus,
    existing_evidence: tuple[Evidence, ...],
    existing_calls: tuple[ToolCallSummary, ...],
    attempts_used: int,
) -> RecoveryOutcome:
    """Perform one bounded recovery chain without resetting run accounting."""
    if not 0 <= attempts_used <= _L2_ATTEMPT_BUDGET:
        return _invalid_outcome(
            request, existing_evidence, existing_calls, attempts_used
        )
    remaining = _L2_ATTEMPT_BUDGET - attempts_used
    if request.remaining_attempts != remaining or remaining == 0:
        return _invalid_outcome(
            request, existing_evidence, existing_calls, attempts_used
        )

    source = _verified_source(request, corpus)
    if request.kind is not RecoveryKind.SCOPED_SEARCH and source is None:
        return _invalid_outcome(
            request, existing_evidence, existing_calls, attempts_used
        )

    calls: list[ToolCallSummary] = list(existing_calls)
    evidence: list[Evidence] = list(existing_evidence)
    if request.kind is RecoveryKind.GET_CLAUSE:
        assert source is not None
        decoded, attempts = await _invoke(
            client,
            tool=ToolName.GET_CLAUSE,
            step_id="recovery_get_clause",
            arguments=_get_clause_args(request, source),
            corpus_manifest_id=request.corpus_manifest_id,
            corpus=corpus,
            attempts_used=attempts_used,
        )
        calls.append(decoded.summary)
        if decoded.evidence is not None:
            evidence.append(decoded.evidence)
        return RecoveryOutcome(_dedupe_evidence(evidence), tuple(calls), attempts)

    if request.kind is RecoveryKind.SCOPED_SEARCH:
        decoded, attempts = await _invoke(
            client,
            tool=ToolName.SEARCH_CLAUSES,
            step_id="recovery_scoped_search",
            arguments={
                "query": claim_text,
                "corpus_manifest_id": request.corpus_manifest_id,
                "document_ids": list(request.allowed_document_ids),
                "normative_levels": [],
                "limit": 1,
            },
            corpus_manifest_id=request.corpus_manifest_id,
            corpus=corpus,
            attempts_used=attempts_used,
        )
        calls.append(decoded.summary)
        target = _first_verified_clause(request, corpus, decoded.ids)
        if target is None or attempts >= _L2_ATTEMPT_BUDGET:
            return RecoveryOutcome(_dedupe_evidence(evidence), tuple(calls), attempts)
        fetched, attempts = await _invoke(
            client,
            tool=ToolName.GET_CLAUSE,
            step_id="recovery_get_clause",
            arguments=_get_clause_args(request, target),
            corpus_manifest_id=request.corpus_manifest_id,
            corpus=corpus,
            attempts_used=attempts,
        )
        calls.append(fetched.summary)
        if fetched.evidence is not None:
            evidence.append(fetched.evidence)
        return RecoveryOutcome(_dedupe_evidence(evidence), tuple(calls), attempts)

    assert source is not None
    expanded, attempts = await _invoke(
        client,
        tool=ToolName.EXPAND_REFERENCES,
        step_id="recovery_expand_references",
        arguments={
            "corpus_manifest_id": request.corpus_manifest_id,
            "document_id": source.document_id,
            "clause_ids": [source.unit_id],
        },
        corpus_manifest_id=request.corpus_manifest_id,
        corpus=corpus,
        attempts_used=attempts_used,
    )
    calls.append(expanded.summary)
    target = _first_verified_clause(request, corpus, expanded.ids)
    if target is None or attempts >= _L2_ATTEMPT_BUDGET:
        return RecoveryOutcome(_dedupe_evidence(evidence), tuple(calls), attempts)
    fetched, attempts = await _invoke(
        client,
        tool=ToolName.GET_CLAUSE,
        step_id="recovery_get_clause",
        arguments=_get_clause_args(request, target),
        corpus_manifest_id=request.corpus_manifest_id,
        corpus=corpus,
        attempts_used=attempts,
    )
    calls.append(fetched.summary)
    if fetched.evidence is not None:
        evidence.append(fetched.evidence)
    return RecoveryOutcome(_dedupe_evidence(evidence), tuple(calls), attempts)


@dataclass(frozen=True, slots=True)
class _Invocation:
    ids: tuple[str, ...]
    evidence: Evidence | None
    summary: ToolCallSummary


async def _invoke(
    client: McpEvidenceClient,
    *,
    tool: ToolName,
    step_id: str,
    arguments: dict[str, Any],
    corpus_manifest_id: str,
    corpus: LocalCorpus,
    attempts_used: int,
) -> tuple[_Invocation, int]:
    if attempts_used >= _L2_ATTEMPT_BUDGET:
        return (
            _Invocation(
                (),
                None,
                _summary(
                    step_id,
                    tool,
                    arguments,
                    error_code="tool_call_budget_exceeded",
                ),
            ),
            attempts_used,
        )
    started = time.monotonic()
    attempts = attempts_used + 1
    result = await client.call_tool(tool.value, arguments)
    error_code = _error_code(result)
    if error_code is not None:
        return (
            _Invocation(
                (),
                None,
                _summary(
                    step_id,
                    tool,
                    arguments,
                    duration_ms=_duration(started),
                    error_code=error_code,
                ),
            ),
            attempts,
        )
    try:
        decoded = _decode_success(
            tool, result, arguments, corpus_manifest_id, corpus
        )
    except EvidenceCollectionError as error:
        return (
            _Invocation(
                (),
                None,
                _summary(
                    step_id,
                    tool,
                    arguments,
                    duration_ms=_duration(started),
                    error_code=error.code,
                ),
            ),
            attempts,
        )
    return (
        _Invocation(
            decoded.ids,
            decoded.evidence,
            _summary(
                step_id,
                tool,
                arguments,
                duration_ms=_duration(started),
                result_count=decoded.count,
            ),
        ),
        attempts,
    )


def _verified_source(
    request: RecoveryRequest, corpus: LocalCorpus
) -> IndexUnit | None:
    if request.source_clause_id is None:
        return None
    unit = corpus.resolve(request.source_clause_id)
    if unit is None or unit.document_id not in request.allowed_document_ids:
        return None
    return unit


def _first_verified_clause(
    request: RecoveryRequest, corpus: LocalCorpus, ids: Iterable[str]
) -> IndexUnit | None:
    for clause_id in ids:
        unit = corpus.resolve(clause_id)
        if unit is not None and unit.document_id in request.allowed_document_ids:
            return unit
    return None


def _get_clause_args(request: RecoveryRequest, unit: IndexUnit) -> dict[str, Any]:
    # Callers only obtain ``clause_id`` through ``_verified_source`` or
    # ``_first_verified_clause``; a model-provided identifier never reaches MCP.
    return {
        "corpus_manifest_id": request.corpus_manifest_id,
        "document_id": unit.document_id,
        "clause_id": unit.unit_id,
    }


def _summary(
    step_id: str,
    tool: ToolName,
    arguments: dict[str, Any],
    *,
    duration_ms: int = 0,
    result_count: int = 0,
    error_code: str | None = None,
) -> ToolCallSummary:
    return ToolCallSummary(
        step_id=step_id,
        tool=tool,
        argument_keys=tuple(sorted(arguments)),
        result_count=result_count,
        duration_ms=duration_ms,
        retry_count=0,
        error_code=error_code,
    )


def _duration(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 0)


def _invalid_outcome(
    request: RecoveryRequest,
    evidence: tuple[Evidence, ...],
    calls: tuple[ToolCallSummary, ...],
    attempts_used: int,
) -> RecoveryOutcome:
    return RecoveryOutcome(
        evidence=_dedupe_evidence(list(evidence)),
        calls=(
            *calls,
            _summary(
                "recovery_refused",
                ToolName.GET_CLAUSE,
                {"corpus_manifest_id": request.corpus_manifest_id},
                error_code="invalid_recovery_clause",
            ),
        ),
        attempts_used=attempts_used,
    )


__all__ = [
    "RecoveryKind",
    "RecoveryOutcome",
    "RecoveryRequest",
    "RecoverySelection",
    "execute_recovery",
    "select_recovery",
]
