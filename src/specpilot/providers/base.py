from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from specpilot.contracts.egress import EgressPayload, TokenCounter
from specpilot.contracts.manifests import Identifier


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderError(Exception):
    """A provider call that failed in a way the caller may safely describe.

    ``public_error_code`` is the only part that reaches the ledger or a log.
    Provider messages can quote the request back, so they are not carried here.
    """

    def __init__(self, public_error_code: str) -> None:
        self.public_error_code = public_error_code
        super().__init__(public_error_code)


class ResponseMetadata(_FrozenModel):
    """The allowlist of response facts that may be recorded.

    Anything not on this list -- headers, request echoes, provider log ids,
    reasoning traces -- stays out of the ledger and out of ordinary logs.
    """

    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    finish_reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    duration_ms: Annotated[int, Field(ge=0)]
    # How many tool calls came back, not what they were. The count answers
    # "can this route emit a structured call at all", which is what §4.6.1
    # asks; the arguments are model output and stay off this list along with
    # everything else the provider says.
    tool_call_count: Annotated[int, Field(ge=0)] = 0
    # The serialized request size, which is the only figure `prompt_tokens` can
    # be compared with like for like. The excerpt projection prices what the
    # caps govern -- source text -- while `prompt_tokens` covers the whole
    # prompt including the system message and any tool schema. Recording the
    # request size is what makes "tokens never exceed bytes" checkable against
    # a live route instead of asserted from the tokenizer's construction.
    request_bytes: Annotated[int, Field(ge=0)] = 0


class ProviderResponse(_FrozenModel):
    """One provider reply.

    ``content`` is the model's answer. It is returned to the caller and may
    later enter the local response cache, but it is never written to the ledger
    and never logged: for L1 and L2 it can quote the source text back.
    """

    schema_version: Literal["provider-response/v1"] = "provider-response/v1"
    provider_id: Identifier
    model_id: Identifier
    content: str
    metadata: ResponseMetadata


class _ProviderAdapter(Protocol):
    """Private send surface. Never injected or re-exported as a raw client.

    An adapter owns its own token counter because the counter must agree with
    the model that will actually tokenize the payload; a counter borrowed from
    elsewhere is how projected sizes silently stop matching real ones.
    """

    provider_id: str
    model_id: str

    @property
    def token_counter(self) -> TokenCounter: ...

    async def send(self, projected_payload: EgressPayload) -> ProviderResponse: ...


__all__ = [
    "ProviderError",
    "ProviderResponse",
    "ResponseMetadata",
]
