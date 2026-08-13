from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from specpilot.answer.evidence import build_evidence_from_unit
from specpilot.contracts.verdict import SemanticReason
from specpilot.corpus.indexable import IndexUnit
from specpilot.retrieval.local import LocalCorpus
from specpilot.verifier.deterministic import DeterministicFault
from specpilot.verifier.recovery import (
    RecoveryKind,
    RecoveryRequest,
    execute_recovery,
    select_recovery,
)

pytestmark = pytest.mark.anyio

MANIFEST_ID = "a" * 64
DOCUMENT_ID = "RFC9110"
CLAUSE_ID = "b" * 64
OTHER_CLAUSE_ID = "c" * 64
CLAIM_ID = "d" * 64
CLAIM_TEXT = "A sender must emit the field."
TEXT = "A sender MUST emit the field."


def _unit(unit_id: str = CLAUSE_ID) -> IndexUnit:
    return IndexUnit(
        unit_id=unit_id,
        kind="clause",
        document_id=DOCUMENT_ID,
        document_version="RFC9110-2022",
        section_number="5.6",
        section_path="HTTP Semantics > 5.6",
        ordinal=17,
        text=TEXT,
        indexed=TEXT,
    )


def _corpus() -> LocalCorpus:
    unit = _unit()
    return LocalCorpus(
        _units={unit.unit_id: unit},
        _toc=(),
        _source_hashes=((DOCUMENT_ID, "e" * 64),),
    )


def _success(payload: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        isError=False,
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
    )


def _clause_result(unit: IndexUnit) -> CallToolResult:
    return _success(
        {
            "corpus_manifest_id": MANIFEST_ID,
            "document_id": unit.document_id,
            "clause_id": unit.unit_id,
            "section_number": unit.section_number,
            "section_path": unit.section_path,
            "content_hash": hashlib.sha256(unit.text.encode()).hexdigest(),
            "text": unit.text,
        }
    )


@dataclass
class ScriptedClient:
    results: list[CallToolResult]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        assert arguments is not None
        self.calls.append((name, arguments))
        return self.results.pop(0)


def test_select_recovery_has_a_closed_reason_mapping() -> None:
    """Removing closed selection would make a model-controlled retry possible."""
    assert (
        select_recovery(
            (DeterministicFault.NOT_DISCLOSED,), source_clause_id=None
        ).kind
        is RecoveryKind.SCOPED_SEARCH
    )
    assert (
        select_recovery(
            (DeterministicFault.CONTENT_HASH_MISMATCH,), source_clause_id=CLAUSE_ID
        ).kind
        is RecoveryKind.GET_CLAUSE
    )
    assert (
        select_recovery(
            (DeterministicFault.DOCUMENT_SCOPE_MISMATCH,),
            source_clause_id=CLAUSE_ID,
        ).kind
        is RecoveryKind.SCOPED_SEARCH
    )
    assert (
        select_recovery(
            (SemanticReason.EXCEPTION_MISSING,), source_clause_id=CLAUSE_ID
        ).kind
        is RecoveryKind.EXPAND_REFERENCES
    )


def test_select_recovery_is_once_per_run_and_requires_budget() -> None:
    """Removing either guard would turn one recovery into unbounded retries."""
    assert (
        select_recovery(
            (DeterministicFault.NOT_DISCLOSED,),
            source_clause_id=None,
            recovery_attempted=True,
        )
        is None
    )


@pytest.mark.parametrize(
    ("kind", "reason_code", "source_clause_id"),
    [
        (RecoveryKind.SCOPED_SEARCH, DeterministicFault.CONTENT_HASH_MISMATCH, None),
        (RecoveryKind.GET_CLAUSE, DeterministicFault.NOT_DISCLOSED, CLAUSE_ID),
        (RecoveryKind.EXPAND_REFERENCES, SemanticReason.UNSUPPORTED, CLAUSE_ID),
        (RecoveryKind.GET_CLAUSE, DeterministicFault.CONTENT_HASH_MISMATCH, None),
    ],
)
def test_illegal_recovery_kind_reason_or_source_is_rejected_before_mcp(
    kind: RecoveryKind,
    reason_code: DeterministicFault | SemanticReason,
    source_clause_id: str | None,
) -> None:
    """Removing closed request validation would make recovery caller-controlled."""
    client = ScriptedClient([])

    with pytest.raises(ValidationError, match="recovery"):
        RecoveryRequest(
            kind=kind,
            claim_id=CLAIM_ID,
            reason_code=reason_code,
            source_clause_id=source_clause_id,
            corpus_manifest_id=MANIFEST_ID,
            allowed_document_ids=(DOCUMENT_ID,),
            remaining_attempts=2,
        )

    assert client.calls == []
    assert (
        select_recovery(
            (DeterministicFault.NOT_DISCLOSED,),
            source_clause_id=None,
            remaining_attempts=0,
        )
        is None
    )


async def test_get_clause_recovery_keeps_only_bound_scope_and_evidence() -> None:
    """Removing request binding could send a recovery to a different corpus."""
    existing = build_evidence_from_unit(_unit(), corpus_manifest_id=MANIFEST_ID)
    request = RecoveryRequest(
        kind=RecoveryKind.GET_CLAUSE,
        claim_id=CLAIM_ID,
        reason_code=DeterministicFault.CONTENT_HASH_MISMATCH,
        source_clause_id=CLAUSE_ID,
        corpus_manifest_id=MANIFEST_ID,
        allowed_document_ids=frozenset({DOCUMENT_ID}),
        remaining_attempts=2,
    )
    client = ScriptedClient([_clause_result(_unit())])

    outcome = await execute_recovery(
        request,
        claim_text=CLAIM_TEXT,
        client=client,
        corpus=_corpus(),
        existing_evidence=(existing,),
        existing_calls=(),
        attempts_used=6,
    )

    assert client.calls == [
        (
            "get_clause",
            {
                "corpus_manifest_id": MANIFEST_ID,
                "document_id": DOCUMENT_ID,
                "clause_id": CLAUSE_ID,
            },
        )
    ]
    assert request.model_dump(mode="json") == {
        "kind": "get_clause",
        "claim_id": CLAIM_ID,
        "reason_code": "content_hash_mismatch",
        "source_clause_id": CLAUSE_ID,
        "corpus_manifest_id": MANIFEST_ID,
        "allowed_document_ids": [DOCUMENT_ID],
        "remaining_attempts": 2,
    }
    assert CLAIM_TEXT not in request.model_dump_json()
    assert outcome.evidence == (existing,)
    assert len(outcome.calls) == 1
    assert outcome.attempts_used == 7


async def test_invented_clause_id_fails_closed_before_get_clause() -> None:
    """Removing corpus verification would let a model identifier reach MCP."""
    request = RecoveryRequest(
        kind=RecoveryKind.GET_CLAUSE,
        claim_id=CLAIM_ID,
        reason_code=DeterministicFault.CONTENT_HASH_MISMATCH,
        source_clause_id=OTHER_CLAUSE_ID,
        corpus_manifest_id=MANIFEST_ID,
        allowed_document_ids=frozenset({DOCUMENT_ID}),
        remaining_attempts=2,
    )
    client = ScriptedClient([])

    outcome = await execute_recovery(
        request,
        claim_text=CLAIM_TEXT,
        client=client,
        corpus=_corpus(),
        existing_evidence=(),
        existing_calls=(),
        attempts_used=6,
    )

    assert client.calls == []
    assert outcome.evidence == ()
    assert outcome.calls[0].error_code == "invalid_recovery_clause"
    assert outcome.attempts_used == 6


async def test_scoped_search_charges_search_and_follow_up_get_clause() -> None:
    """Removing individual charges would allow search recovery to exceed eight."""
    unit = _unit()
    client = ScriptedClient(
        [
            _success(
                {
                    "hits": [
                        {
                            "corpus_manifest_id": MANIFEST_ID,
                            "document_id": DOCUMENT_ID,
                            "clause_id": CLAUSE_ID,
                            "section_number": unit.section_number,
                            "section_path": unit.section_path,
                            "content_hash": hashlib.sha256(
                                unit.text.encode()
                            ).hexdigest(),
                            "score": 1.0,
                        }
                    ]
                }
            ),
            _clause_result(unit),
        ]
    )
    request = RecoveryRequest(
        kind=RecoveryKind.SCOPED_SEARCH,
        claim_id=CLAIM_ID,
        reason_code=DeterministicFault.NOT_DISCLOSED,
        source_clause_id=None,
        corpus_manifest_id=MANIFEST_ID,
        allowed_document_ids=frozenset({DOCUMENT_ID}),
        remaining_attempts=2,
    )

    outcome = await execute_recovery(
        request,
        claim_text=CLAIM_TEXT,
        client=client,
        corpus=_corpus(),
        existing_evidence=(),
        existing_calls=(),
        attempts_used=6,
    )

    assert [name for name, _ in client.calls] == ["search_clauses", "get_clause"]
    assert len(outcome.evidence) == 1
    assert len(outcome.calls) == 2
    assert outcome.attempts_used == 8
