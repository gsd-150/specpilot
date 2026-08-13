from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from specpilot.agents.contracts import ToolPlan
from specpilot.agents.evidence import EvidenceAgent, EvidenceCollectionError
from specpilot.corpus.indexable import IndexUnit

pytestmark = pytest.mark.anyio

CORPUS_ID = "c" * 64
DOCUMENT_ID = "synthetic-fixture-spec"
CLAUSE_ID = "clause-1"
TEXT = "A sender may retry once."


def success(payload: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        isError=False,
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
    )


def failure(code: str) -> CallToolResult:
    detail = {
        "code": code,
        "field": "query",
        "correction": "Retry the bounded local search once.",
    }
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=json.dumps(detail))],
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


@dataclass(frozen=True)
class UnitResolver:
    unit: IndexUnit

    def get_clause(self, clause_id: str) -> IndexUnit:
        if clause_id != self.unit.unit_id:
            raise KeyError(clause_id)
        return self.unit


def unit() -> IndexUnit:
    return IndexUnit(
        unit_id=CLAUSE_ID,
        kind="clause",
        document_id=DOCUMENT_ID,
        document_version="1",
        section_number="1.1",
        section_path="Retry",
        ordinal=1,
        text=TEXT,
        indexed=TEXT,
    )


async def test_timeout_retries_once_and_retains_no_result_text() -> None:
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    client = ScriptedClient(
        [
            failure("tool_timeout"),
            success(
                {
                    "corpus_manifest_id": CORPUS_ID,
                    "document_id": DOCUMENT_ID,
                    "clause_id": CLAUSE_ID,
                    "section_number": "1.1",
                    "section_path": "Retry",
                    "content_hash": digest,
                    "text": TEXT,
                }
            ),
        ]
    )
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "read",
                    "tool": "get_clause",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {"kind": "direct", "clause_ids": [CLAUSE_ID]},
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan, CORPUS_ID
    )

    assert [name for name, _ in client.calls] == ["get_clause", "get_clause"]
    assert len(result.evidence) == 1
    assert result.evidence[0].disclosed.clause_id == CLAUSE_ID
    assert result.calls[0].retry_count == 1
    assert result.calls[0].error_code is None
    assert TEXT not in result.calls[0].model_dump_json()


async def test_l2_budget_allows_exactly_two_attempts_after_six_are_persisted() -> None:
    """Removing carried attempt state would reset an L2 run at recovery time."""
    client = ScriptedClient(
        [success({"clause_ids": []}), success({"clause_ids": []})]
    )
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "expand",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": [CLAUSE_ID, "clause-2"],
                        },
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan,
        CORPUS_ID,
        attempt_budget=8,
        attempts_used=6,
    )

    assert len(client.calls) == 2
    assert result.attempts_used == 8
    assert [call.error_code for call in result.calls] == [None, None]


async def test_plan_that_costs_more_than_remaining_budget_makes_no_mcp_call() -> None:
    """Removing the preflight would spend a partial plan after a process resume."""
    client = ScriptedClient([])
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "expand",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": [CLAUSE_ID, "clause-2", "clause-3"],
                        },
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    with pytest.raises(EvidenceCollectionError, match="tool_call_budget_exceeded"):
        await EvidenceAgent(client, UnitResolver(unit())).collect(
            plan,
            CORPUS_ID,
            attempt_budget=8,
            attempts_used=6,
        )

    assert client.calls == []


async def test_exhausted_l2_budget_cannot_be_reset_to_make_another_call() -> None:
    """Removing carried attempts would let an exhausted run start again at zero."""
    client = ScriptedClient([])
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "expand",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {"kind": "direct", "clause_ids": [CLAUSE_ID]},
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    with pytest.raises(EvidenceCollectionError, match="tool_call_budget_exceeded"):
        await EvidenceAgent(client, UnitResolver(unit())).collect(
            plan,
            CORPUS_ID,
            attempt_budget=8,
            attempts_used=8,
        )

    assert client.calls == []


async def test_timeout_retry_consumes_the_carried_l2_attempt_budget() -> None:
    """Removing retry charging would allow a ninth L2 MCP call."""
    client = ScriptedClient([failure("tool_timeout"), success({"clause_ids": []})])
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "expand",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {"kind": "direct", "clause_ids": [CLAUSE_ID]},
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan,
        CORPUS_ID,
        attempt_budget=8,
        attempts_used=6,
    )

    assert len(client.calls) == 2
    assert result.attempts_used == 8
    assert result.calls[0].retry_count == 1


async def test_non_timeout_error_is_not_retried() -> None:
    client = ScriptedClient([failure("backend_unavailable")])
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "search",
                    "tool": "search_clauses",
                    "args": {
                        "query": "retry",
                        "corpus_manifest_id": CORPUS_ID,
                        "document_ids": [DOCUMENT_ID],
                        "normative_levels": [],
                        "limit": 3,
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan, CORPUS_ID
    )

    assert len(client.calls) == 1
    assert result.calls[0].error_code == "backend_unavailable"
    assert result.calls[0].retry_count == 0


async def test_dependencies_resolve_prior_opaque_ids_in_plan_order() -> None:
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    client = ScriptedClient(
        [
            success(
                {
                    "hits": [
                        {
                            "corpus_manifest_id": CORPUS_ID,
                            "document_id": DOCUMENT_ID,
                            "clause_id": CLAUSE_ID,
                            "section_number": "1.1",
                            "section_path": "Retry",
                            "content_hash": digest,
                            "score": 1.0,
                        }
                    ]
                }
            ),
            success(
                {
                    "corpus_manifest_id": CORPUS_ID,
                    "document_id": DOCUMENT_ID,
                    "clause_id": CLAUSE_ID,
                    "section_number": "1.1",
                    "section_path": "Retry",
                    "content_hash": digest,
                    "text": TEXT,
                }
            ),
        ]
    )
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "search",
                    "tool": "search_clauses",
                    "args": {
                        "query": "retry",
                        "corpus_manifest_id": CORPUS_ID,
                        "document_ids": [DOCUMENT_ID],
                        "normative_levels": [],
                        "limit": 3,
                    },
                    "depends_on": [],
                },
                {
                    "step_id": "read",
                    "tool": "get_clause",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "step_result",
                            "step_id": "search",
                            "take": 1,
                        },
                    },
                    "depends_on": ["search"],
                },
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan, CORPUS_ID
    )

    assert [name for name, _ in client.calls] == ["search_clauses", "get_clause"]
    assert client.calls[1][1]["clause_id"] == CLAUSE_ID
    assert [call.step_id for call in result.calls] == ["search", "read"]
    assert [item.disclosed.clause_id for item in result.evidence] == [CLAUSE_ID]


async def test_sixth_timeout_cannot_create_a_seventh_attempt() -> None:
    client = ScriptedClient(
        [
            *[success({"clause_ids": []}) for _ in range(5)],
            failure("tool_timeout"),
        ]
    )
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "first",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": ["clause-1", "clause-2", "clause-3"],
                        },
                    },
                    "depends_on": [],
                },
                {
                    "step_id": "second",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": ["clause-4", "clause-5", "clause-6"],
                        },
                    },
                    "depends_on": [],
                },
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan, CORPUS_ID
    )

    assert len(client.calls) == 6
    assert result.calls[-1].error_code == "tool_timeout"
    assert result.calls[-1].retry_count == 0


async def test_retry_consumes_budget_before_remaining_planned_calls() -> None:
    client = ScriptedClient(
        [
            failure("tool_timeout"),
            *[success({"clause_ids": []}) for _ in range(5)],
        ]
    )
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "first",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": ["clause-1", "clause-2", "clause-3"],
                        },
                    },
                    "depends_on": [],
                },
                {
                    "step_id": "second",
                    "tool": "expand_references",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": ["clause-4", "clause-5", "clause-6"],
                        },
                    },
                    "depends_on": [],
                },
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan, CORPUS_ID
    )

    assert len(client.calls) == 6
    assert result.calls[-1].result_count == 0
    assert result.calls[-1].error_code == "tool_call_budget_exceeded"


async def test_runtime_corpus_scope_mismatch_is_no_call() -> None:
    client = ScriptedClient([])
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "search",
                    "tool": "search_clauses",
                    "args": {
                        "query": "retry",
                        "corpus_manifest_id": "d" * 64,
                        "document_ids": [DOCUMENT_ID],
                        "normative_levels": [],
                        "limit": 3,
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    with pytest.raises(EvidenceCollectionError) as caught:
        await EvidenceAgent(client, UnitResolver(unit())).collect(plan, CORPUS_ID)

    assert caught.value.code == "invalid_corpus_scope"
    assert client.calls == []


async def test_direct_clause_ids_are_deduplicated_before_calls() -> None:
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    client = ScriptedClient(
        [
            success(
                {
                    "corpus_manifest_id": CORPUS_ID,
                    "document_id": DOCUMENT_ID,
                    "clause_id": CLAUSE_ID,
                    "section_number": "1.1",
                    "section_path": "Retry",
                    "content_hash": digest,
                    "text": TEXT,
                }
            )
        ]
    )
    plan = ToolPlan.model_validate(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "read",
                    "tool": "get_clause",
                    "args": {
                        "corpus_manifest_id": CORPUS_ID,
                        "document_id": DOCUMENT_ID,
                        "clauses": {
                            "kind": "direct",
                            "clause_ids": [CLAUSE_ID, CLAUSE_ID],
                        },
                    },
                    "depends_on": [],
                }
            ],
        }
    )

    result = await EvidenceAgent(client, UnitResolver(unit())).collect(
        plan, CORPUS_ID
    )

    assert len(client.calls) == 1
    assert len(result.calls) == 1
    assert len(result.evidence) == 1
