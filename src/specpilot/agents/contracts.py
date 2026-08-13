from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from specpilot.contracts.manifests import Identifier, Sha256, _FrozenModel

ToolQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
Term = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class ToolName(StrEnum):
    SEARCH_CLAUSES = "search_clauses"
    GET_CLAUSE = "get_clause"
    GET_TOC = "get_toc"
    EXPAND_REFERENCES = "expand_references"
    LOOKUP_TERM = "lookup_term"


class DirectClauseIds(_FrozenModel):
    kind: Literal["direct"] = "direct"
    clause_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=3)]


class StepResultRef(_FrozenModel):
    kind: Literal["step_result"] = "step_result"
    step_id: Identifier
    take: Annotated[int, Field(ge=1, le=3)]


type ClauseIds = Annotated[DirectClauseIds | StepResultRef, Field(discriminator="kind")]


class SearchClausesArgs(_FrozenModel):
    query: ToolQuery
    corpus_manifest_id: Sha256
    document_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=12)]
    normative_levels: Annotated[tuple[Identifier, ...], Field(max_length=5)] = ()
    limit: Annotated[int, Field(ge=1, le=20)]


class GetClauseArgs(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    clauses: ClauseIds


class GetTocArgs(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    limit: Annotated[int, Field(ge=1, le=12)]


class ExpandReferencesArgs(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    clauses: ClauseIds


class LookupTermArgs(_FrozenModel):
    corpus_manifest_id: Sha256
    document_id: Identifier
    term: Term


class SearchClausesStep(_FrozenModel):
    step_id: Identifier
    tool: Literal[ToolName.SEARCH_CLAUSES]
    args: SearchClausesArgs
    depends_on: tuple[Identifier, ...] = ()


class GetClauseStep(_FrozenModel):
    step_id: Identifier
    tool: Literal[ToolName.GET_CLAUSE]
    args: GetClauseArgs
    depends_on: tuple[Identifier, ...] = ()


class GetTocStep(_FrozenModel):
    step_id: Identifier
    tool: Literal[ToolName.GET_TOC]
    args: GetTocArgs
    depends_on: tuple[Identifier, ...] = ()


class ExpandReferencesStep(_FrozenModel):
    step_id: Identifier
    tool: Literal[ToolName.EXPAND_REFERENCES]
    args: ExpandReferencesArgs
    depends_on: tuple[Identifier, ...] = ()


class LookupTermStep(_FrozenModel):
    step_id: Identifier
    tool: Literal[ToolName.LOOKUP_TERM]
    args: LookupTermArgs
    depends_on: tuple[Identifier, ...] = ()


type ToolStep = Annotated[
    SearchClausesStep
    | GetClauseStep
    | GetTocStep
    | ExpandReferencesStep
    | LookupTermStep,
    Field(discriminator="tool"),
]

def _call_cost(step: ToolStep) -> int:
    if isinstance(step, (GetClauseStep, ExpandReferencesStep)):
        if isinstance(step.args.clauses, DirectClauseIds):
            return len(step.args.clauses.clause_ids)
        return step.args.clauses.take
    return 1


class ToolPlan(_FrozenModel):
    plan_id: Identifier
    steps: Annotated[tuple[ToolStep, ...], Field(min_length=1, max_length=4)]

    @property
    def base_call_cost(self) -> int:
        return sum(_call_cost(step) for step in self.steps)

    @model_validator(mode="after")
    def _validate_dependencies_and_cost(self) -> Self:
        step_indexes = {step.step_id: index for index, step in enumerate(self.steps)}
        if len(step_indexes) != len(self.steps):
            raise ValueError("step IDs must be unique")

        for index, step in enumerate(self.steps):
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("step dependencies must be unique")
            for dependency_id in step.depends_on:
                dependency_index = step_indexes.get(dependency_id)
                if dependency_index is None or dependency_index >= index:
                    raise ValueError("dependencies must name a prior step")

            if isinstance(step, (GetClauseStep, ExpandReferencesStep)) and isinstance(
                step.args.clauses, StepResultRef
            ):
                reference = step.args.clauses
                if reference.step_id not in step.depends_on:
                    raise ValueError("result reference must be listed in depends_on")
                reference_index = step_indexes.get(reference.step_id)
                if reference_index is None or reference_index >= index:
                    raise ValueError("result reference must name a prior step")

        return self


def validate_tool_plan(
    plan: ToolPlan | Mapping[str, object], *, max_call_cost: Literal[6, 8]
) -> ToolPlan:
    """Return a model-validated plan before local execution begins."""
    bounded = plan if isinstance(plan, ToolPlan) else ToolPlan.model_validate(plan)
    if bounded.base_call_cost > max_call_cost:
        word = "six" if max_call_cost == 6 else "eight"
        raise ValueError(f"tool plan may not exceed {word} calls")
    return bounded


class ToolCallSummary(_FrozenModel):
    step_id: Identifier
    tool: ToolName
    argument_keys: tuple[Identifier, ...]
    result_count: int
    duration_ms: int
    retry_count: int
    error_code: Identifier | None = None
