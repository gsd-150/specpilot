from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from specpilot.agents.contracts import ToolCallSummary, ToolPlan, validate_tool_plan

FIXTURE_CORPUS_ID = "a" * 64


def _search_step(step_id: str = "search") -> dict[str, object]:
    return {
        "step_id": step_id,
        "tool": "search_clauses",
        "args": {
            "query": "retry",
            "corpus_manifest_id": FIXTURE_CORPUS_ID,
            "document_ids": ["synthetic-fixture-spec"],
            "normative_levels": ["MUST"],
            "limit": 5,
        },
        "depends_on": [],
    }


def _result_clause_step(*, take: int, step_id: str = "read") -> dict[str, object]:
    return {
        "step_id": step_id,
        "tool": "get_clause",
        "args": {
            "corpus_manifest_id": FIXTURE_CORPUS_ID,
            "document_id": "synthetic-fixture-spec",
            "clauses": {"kind": "step_result", "step_id": "search", "take": take},
        },
        "depends_on": ["search"],
    }


def test_plan_rejects_forward_dependency() -> None:
    """Removing ordering validation would allow an unavailable result reference."""
    with pytest.raises(ValueError, match="prior step"):
        ToolPlan.model_validate(
            {
                "plan_id": "p1",
                "steps": [
                    {
                        "step_id": "a",
                        "tool": "get_clause",
                        "args": {
                            "corpus_manifest_id": FIXTURE_CORPUS_ID,
                            "document_id": "synthetic-fixture-spec",
                            "clauses": {
                                "kind": "step_result",
                                "step_id": "b",
                                "take": 1,
                            },
                        },
                        "depends_on": ["b"],
                    },
                    _search_step("b"),
                ],
            }
        )


def test_plan_rejects_result_reference_missing_from_dependencies() -> None:
    """Removing dependency binding would let a step read an undeclared result."""
    invalid_step = _result_clause_step(take=1)
    invalid_step["depends_on"] = []

    with pytest.raises(ValueError, match="depends_on"):
        ToolPlan.model_validate(
            {"plan_id": "p1", "steps": [_search_step(), invalid_step]}
        )


def test_plan_rejects_more_than_six_expanded_calls() -> None:
    """Removing cost enforcement would let a four-step plan exceed its call cap."""
    with pytest.raises(ValueError, match="six calls"):
        validate_tool_plan(
            {
                "plan_id": "p1",
                "steps": [
                    _search_step(),
                    _result_clause_step(take=3, step_id="read-one"),
                    {
                        **_result_clause_step(take=2, step_id="read-two"),
                        "depends_on": ["search"],
                    },
                    {
                        "step_id": "toc",
                        "tool": "get_toc",
                        "args": {
                            "corpus_manifest_id": FIXTURE_CORPUS_ID,
                            "document_id": "synthetic-fixture-spec",
                            "limit": 12,
                        },
                        "depends_on": [],
                    },
                ],
            }
        )


def test_plan_json_parsing_returns_frozen_typed_plan() -> None:
    """Removing discriminated parsing would accept an untyped mutable plan."""
    plan = ToolPlan.model_validate_json(
        json.dumps(
            {
                "plan_id": "p1",
                "steps": [_search_step(), _result_clause_step(take=2)],
            }
        )
    )

    assert plan.steps[1].tool == "get_clause"
    assert validate_tool_plan(plan) is plan
    with pytest.raises(ValidationError, match="frozen"):
        plan.plan_id = "other"


def test_plan_rejects_duplicate_step_ids() -> None:
    """Removing uniqueness validation would make dependency targets ambiguous."""
    with pytest.raises(ValueError, match="unique"):
        ToolPlan.model_validate(
            {"plan_id": "p1", "steps": [_search_step("same"), _search_step("same")]}
        )


def test_call_summary_keeps_only_sanitized_metadata() -> None:
    """Removing the frozen allowlist would let raw tool results enter a trace."""
    summary = ToolCallSummary(
        step_id="search",
        tool="search_clauses",
        argument_keys=("query", "limit"),
        result_count=2,
        duration_ms=11,
        retry_count=0,
    )

    assert summary.model_dump() == {
        "step_id": "search",
        "tool": "search_clauses",
        "argument_keys": ("query", "limit"),
        "result_count": 2,
        "duration_ms": 11,
        "retry_count": 0,
        "error_code": None,
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ToolCallSummary.model_validate(
            {**summary.model_dump(), "result_text": "secret"}
        )
