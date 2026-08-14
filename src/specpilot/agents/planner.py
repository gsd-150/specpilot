"""Ledger-bound model planning over a source-free, typed tool catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from specpilot.agents.contracts import (
    ExpandReferencesArgs,
    GetClauseArgs,
    GetTocArgs,
    LookupTermArgs,
    SearchClausesArgs,
    ToolPlan,
    validate_tool_plan,
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
    task_level: TaskLevel = TaskLevel.L1
    reconstruction_generation: int = 0


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
        if context.reconstruction_generation < 0:
            raise ValueError("invalid_reconstruction_generation")
        version = VersionMetadata(
            source_manifest_id=context.source_manifest.manifest_id,
            corpus_manifest_id=context.corpus_manifest_id,
            document_id=context.source_manifest.document_id,
            document_version=context.source_manifest.document_version,
        )
        max_calls = _max_tool_calls(context.task_level)
        tools = _tool_catalog(max_calls)
        request = EgressRequest(
            evaluation_root_id=context.evaluation_root_id,
            run_id=context.run_id,
            task_level=context.task_level,
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
                max_tool_calls=max_calls,
            ),
        )
        receipt = await self._transport.send(
            request,
            idempotency_key=_generation_key(
                context.idempotency_key, context.reconstruction_generation
            ),
        )
        try:
            plan = validate_tool_plan(
                ToolPlan.model_validate_json(receipt.response.content),
                max_call_cost=max_calls,
            )
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


def _tool_catalog(max_tool_calls: Literal[6, 8]) -> tuple[ToolSchema, ...]:
    return tuple(
        ToolSchema(
            name=name,
            description=f"{description} Maximum MCP calls: {max_tool_calls}.",
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


def _max_tool_calls(task_level: TaskLevel) -> Literal[6, 8]:
    return 6 if task_level is TaskLevel.L1 else 8


def _generation_key(key: str, generation: int) -> str:
    """Make only post-loss reconstructions new egress attempts.

    The stable run/stage key remains the root of every generation.  A normal
    retry keeps generation zero and is refused by transport replay; after a
    lost model result the resumed state requests an explicitly new generation
    that is charged to the existing root ledger.
    """
    base, separator, suffix = key.rpartition("-g")
    if not separator or not suffix.isascii() or not suffix.isdecimal():
        base = key
    return f"{base}-g{generation}"


__all__ = ["InvalidToolPlan", "Planner", "PlannerContext", "PlannerResult"]
