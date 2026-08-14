"""Closed HTTP contracts for asynchronous L1 runs."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from specpilot.contracts.manifests import Sha256

Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_192)
]
ApiIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChatRequest(_ClosedModel):
    question: Question
    request_id: UUID
    evaluation_root_id: ApiIdentifier
    task_level: Literal["L1", "L2"] = "L1"
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256


class ChatAccepted(_ClosedModel):
    run_id: UUID
    status: Literal["queued"] = "queued"


class ResumeRequest(_ClosedModel):
    question: Question
    resume_key: ApiIdentifier


class ResumeAccepted(_ClosedModel):
    run_id: UUID
    attempt: Annotated[int, Field(ge=2)]
    status: Literal["queued"] = "queued"


class DemoSessionCreated(_ClosedModel):
    status: Literal["created"] = "created"


class HealthView(_ClosedModel):
    status: Literal["ok", "degraded"]
    postgres: Literal["ok", "down"]
    mcp: Literal["ok", "down"]


__all__ = [
    "ChatAccepted",
    "ChatRequest",
    "DemoSessionCreated",
    "HealthView",
    "ResumeAccepted",
    "ResumeRequest",
]
