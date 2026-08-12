from __future__ import annotations

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    L1PlanPayload,
    TaskLevel,
    ToolSchema,
)
from tests.unit.egress.test_policy_projection import (
    FixtureTokenCounter,
    authorized_manifest,
    fixture_enforcer,
    online_route,
    version_metadata,
)


def _tool_catalog() -> tuple[ToolSchema, ...]:
    return (
        ToolSchema(
            name="search_clauses",
            description="Search clause identifiers by query.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        ),
        ToolSchema(
            name="get_clause",
            description="Read one clause by its identifier.",
            input_schema={
                "type": "object",
                "properties": {"clause_id": {"type": "string"}},
            },
        ),
        ToolSchema(
            name="get_toc",
            description="Read the bounded table of contents.",
            input_schema={"type": "object", "properties": {}},
        ),
        ToolSchema(
            name="expand_references",
            description="Expand one-hop clause references.",
            input_schema={
                "type": "object",
                "properties": {"clause_id": {"type": "string"}},
            },
        ),
        ToolSchema(
            name="lookup_term",
            description="Look up a defined term.",
            input_schema={"type": "object", "properties": {"term": {"type": "string"}}},
        ),
    )


def planning_request(*, query: str) -> EgressRequest:
    route = online_route()
    source = authorized_manifest(route=route)
    version = version_metadata(source_manifest_id=source.manifest_id)
    return EgressRequest(
        evaluation_root_id="case-1",
        run_id="planning-run",
        task_level=TaskLevel.L1,
        version=version,
        stage=EgressStage.PLANNING,
        route=route,
        model_id="fixture-model-v1",
        source_manifest=source,
        payload=L1PlanPayload(
            query=query,
            version=version,
            tool_catalog_version="mcp-v1",
            tool_catalog_hash="a" * 64,
            tools=_tool_catalog(),
        ),
    )


def test_planning_payload_discloses_query_and_no_source_text() -> None:
    request = planning_request(query="When may a sender retry?")
    reservation = fixture_enforcer().prepare(request, FixtureTokenCounter())

    assert request.stage is EgressStage.PLANNING
    assert request.payload.kind == "l1_plan"
    assert reservation.disclosures == ()
    assert request.payload.max_steps == 4
    assert request.payload.max_tool_calls == 6


def test_planning_payload_rejects_source_disclosure_fields() -> None:
    fields = planning_request(query="When may a sender retry?").payload.model_dump()
    fields["evidence_excerpts"] = []

    with pytest.raises(ValidationError) as caught:
        L1PlanPayload.model_validate(fields)

    assert caught.value.errors()[0]["type"] == "extra_forbidden"
