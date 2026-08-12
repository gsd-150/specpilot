"""Ledger-bound model planning over a source-free, typed tool catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from specpilot.agents.contracts import (
    ExpandReferencesArgs,
    GetClauseArgs,
    GetTocArgs,
    LookupTermArgs,
    SearchClausesArgs,
    ToolPlan,
)
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    L1PlanPayload,
    TaskLevel,
    ToolSchema,
    VersionMetadata,
)
from specpilot.contracts.manifests import (
    ProviderRouteBinding,
    RfcSourceManifest,
    SourceManifest,
)
from specpilot.egress.ledger import RequestSize
from specpilot.providers.transport import PolicyBoundTransport

_CATALOG_VERSION = "mcp-v1"
_TOOL_MODELS = (
    ("search_clauses", "Search bounded local clause metadata.", SearchClausesArgs),
    ("get_clause", "Read selected local clauses.", GetClauseArgs),
    ("get_toc", "Read a bounded local table of contents.", GetTocArgs),
    (
        "expand_references",
        "Expand one bounded hop of local clause references.",
        ExpandReferencesArgs,
    ),
    ("lookup_term", "Locate local definition clauses for a term.", LookupTermArgs),
)


class InvalidToolPlan(Exception):
    """The provider returned content that is not one bounded ToolPlan."""

    __slots__ = ("replayed", "request_size", "reservation_id")

    def __init__(
        self,
        *,
        reservation_id: str,
        replayed: bool,
        request_size: RequestSize,
    ) -> None:
        self.reservation_id = reservation_id
        self.replayed = replayed
        self.request_size = request_size
        super().__init__("invalid_tool_plan")


@dataclass(frozen=True, slots=True)
class PlannerContext:
    source_manifest: SourceManifest | RfcSourceManifest
    corpus_manifest_id: str
    evaluation_root_id: str
    run_id: str
    model_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PlannerResult:
    """A valid plan plus the real sanitized planning receipt metadata."""

    plan: ToolPlan
    reservation_id: str
    replayed: bool
    request_size: RequestSize


class Planner:
    def __init__(self, transport: PolicyBoundTransport) -> None:
        self._transport = transport

    async def plan(self, question: str, context: PlannerContext) -> PlannerResult:
        version = VersionMetadata(
            source_manifest_id=context.source_manifest.manifest_id,
            corpus_manifest_id=context.corpus_manifest_id,
            document_id=context.source_manifest.document_id,
            document_version=context.source_manifest.document_version,
        )
        tools = _tool_catalog()
        request = EgressRequest(
            evaluation_root_id=context.evaluation_root_id,
            run_id=context.run_id,
            task_level=TaskLevel.L1,
            version=version,
            stage=EgressStage.PLANNING,
            route=_authorized_route(context.source_manifest),
            model_id=context.model_id,
            source_manifest=context.source_manifest,
            payload=L1PlanPayload(
                query=question,
                version=version,
                tool_catalog_version=_CATALOG_VERSION,
                tool_catalog_hash=_tool_catalog_hash(tools),
                tools=tools,
            ),
        )
        receipt = await self._transport.send(
            request, idempotency_key=context.idempotency_key
        )
        try:
            plan = ToolPlan.model_validate_json(receipt.response.content)
        except (ValidationError, ValueError):
            plan = None
        if plan is None:
            # Construct outside the parser's exception handler so the durable
            # orchestration error cannot retain provider response content in
            # either cause or implicit context.
            error = InvalidToolPlan(
                reservation_id=receipt.reservation_id,
                replayed=receipt.replayed,
                request_size=receipt.request_size,
            )
            raise error
        return PlannerResult(
            plan=plan,
            reservation_id=receipt.reservation_id,
            replayed=receipt.replayed,
            request_size=receipt.request_size,
        )


def _authorized_route(
    source_manifest: SourceManifest | RfcSourceManifest,
) -> ProviderRouteBinding:
    route = source_manifest.provider_route_binding
    if route is None:
        raise ValueError("source manifest carries no authorized provider route")
    return route


def _tool_catalog() -> tuple[ToolSchema, ...]:
    return tuple(
        ToolSchema(
            name=name,
            description=description,
            input_schema=model.model_json_schema(),
        )
        for name, description, model in _TOOL_MODELS
    )


def _tool_catalog_hash(tools: tuple[ToolSchema, ...]) -> str:
    encoded = json.dumps(
        [tool.model_dump(mode="json") for tool in tools],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["InvalidToolPlan", "Planner", "PlannerContext", "PlannerResult"]
