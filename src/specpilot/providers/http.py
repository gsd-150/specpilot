"""The OpenAI-compatible provider adapter: the only code here that talks to one.

Both planned routes speak the same chat-completions dialect, so one adapter
covers them and the difference between them is configuration -- a base URL, a
model slug, and which environment variable holds the key.

Three rules shape everything below, and each of them is a rule the rest of the
system already relies on:

*Nothing the provider says is carried.* Error bodies quote the request back and
sometimes the key with it, so a failure becomes one stable code from a closed
set and the body is dropped unread. `ResponseMetadata` is an allowlist for the
same reason: headers, request ids, and reasoning traces have no field to land in.

*The key exists at construction or the adapter does not.* Finding out mid-run
would strand a reserved budget against a call that can never happen, and the
reservation is the expensive half.

*The token counter is an upper bound, not an estimate.* The enforcer prices a
reservation before the call, so a counter that guesses low would let a payload
past a cap it actually exceeds. Byte-level BPE -- what these models use -- emits
at most one token per UTF-8 byte, so the byte count bounds the real count for
any of them. It is deliberately loose: the first live call reports the
provider's own `prompt_tokens` beside this projection, and that measurement is
what a tighter counter would have to be built on.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from specpilot.contracts.answer import REPLY_INSTRUCTIONS
from specpilot.contracts.egress import (
    EgressPayload,
    JudgePayload,
    L1OnlinePayload,
    L2AtomicClaimPayload,
    L2DesignPayload,
)
from specpilot.providers.base import ProviderError, ProviderResponse, ResponseMetadata

# Sent only when the adapter is probing. It is a constant authored here, not
# derived from the corpus, so it discloses nothing the enforcer governs; its
# only job is to find out whether a route can emit a tool call at all.
PROBE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "specpilot_probe",
        "description": "Report that this route can emit a structured tool call.",
        "parameters": {
            "type": "object",
            "properties": {
                "reachable": {
                    "type": "boolean",
                    "description": "Always true.",
                }
            },
            "required": ["reachable"],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT = (
    "You answer strictly from the evidence excerpts supplied in the message. "
    "If they do not support an answer, say so."
)

_STATUS_CODES = {
    401: "provider_unauthorized",
    403: "provider_unauthorized",
    404: "provider_model_not_found",
    429: "provider_rate_limited",
}


class ProviderCredentialMissing(RuntimeError):
    """No key in the environment for this route.

    Carries the variable name so the operator knows what to set, and never the
    value of anything.
    """


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    """Everything about a route except its secret."""

    provider_id: str
    model_id: str
    base_url: str
    api_key_env: str
    timeout_seconds: float = 60.0


def resolve_credential(endpoint: ProviderEndpoint) -> str:
    """Read the route's key from the environment, or refuse.

    Whitespace-only is absent: an exported-but-empty variable is the common
    shape of a misconfigured shell, and treating it as a key would turn a setup
    mistake into an authentication failure against a live endpoint.
    """
    raw = os.environ.get(endpoint.api_key_env, "")
    if not raw.strip():
        raise ProviderCredentialMissing(
            f"set {endpoint.api_key_env} in the environment for "
            f"{endpoint.provider_id}; it is never read from a file"
        )
    return raw.strip()


@dataclass(frozen=True, slots=True)
class ByteUpperBoundCounter:
    """A token count no byte-level BPE tokenizer can exceed."""

    provider_id: str
    model_id: str

    def count_tokens(self, text: str) -> int:
        return len(text.encode("utf-8"))


class HttpChatAdapter:
    """One route. Private by construction: nothing hands this out."""

    __slots__ = ("_client", "_key", "_probe_tools", "endpoint")

    def __init__(
        self,
        endpoint: ProviderEndpoint,
        *,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        probe_tools: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self._key = api_key
        self._probe_tools = probe_tools
        self._client = httpx.AsyncClient(
            base_url=endpoint.base_url,
            timeout=endpoint.timeout_seconds,
            transport=transport,
        )

    def __repr__(self) -> str:
        """Names the route and never the secret."""
        return (
            f"HttpChatAdapter(provider_id={self.endpoint.provider_id!r}, "
            f"model_id={self.endpoint.model_id!r})"
        )

    @property
    def provider_id(self) -> str:
        return self.endpoint.provider_id

    @property
    def model_id(self) -> str:
        return self.endpoint.model_id

    @property
    def token_counter(self) -> ByteUpperBoundCounter:
        return ByteUpperBoundCounter(self.endpoint.provider_id, self.endpoint.model_id)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, projected_payload: EgressPayload) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": self.endpoint.model_id,
            "messages": _render_messages(projected_payload),
            "temperature": 0,
        }
        if self._probe_tools:
            body["tools"] = [PROBE_TOOL]

        # Serialized here rather than left to httpx, so the byte count recorded
        # is the byte count sent.
        encoded = json.dumps(body).encode("utf-8")

        try:
            response = await self._client.post(
                "/chat/completions",
                content=encoded,
                headers={
                    "authorization": f"Bearer {self._key}",
                    "content-type": "application/json",
                },
            )
        except httpx.TimeoutException as error:
            raise ProviderError("provider_timeout") from error
        except httpx.HTTPError as error:
            raise ProviderError("provider_unreachable") from error

        if response.status_code >= 400:
            raise ProviderError(_failure_code(response.status_code))
        return self._read(response, request_bytes=len(encoded))

    def _read(
        self, response: httpx.Response, *, request_bytes: int
    ) -> ProviderResponse:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderError("provider_malformed_response") from error
        if not isinstance(body, dict):
            raise ProviderError("provider_malformed_response")

        if body.get("model") not in (None, self.endpoint.model_id):
            # A route that answers as a different model has not priced the same
            # reservation the enforcer approved.
            raise ProviderError("provider_model_mismatch")

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("provider_malformed_response")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("provider_malformed_response")
        message = choice.get("message")
        finish_reason = choice.get("finish_reason")
        if not isinstance(message, dict) or not isinstance(finish_reason, str):
            raise ProviderError("provider_malformed_response")

        tool_calls = message.get("tool_calls")
        tool_call_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        content = message.get("content")
        if content is None and tool_call_count:
            content = ""
        if not isinstance(content, str):
            raise ProviderError("provider_malformed_response")

        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError("provider_malformed_response")
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
            raise ProviderError("provider_malformed_response")

        return ProviderResponse(
            provider_id=self.endpoint.provider_id,
            model_id=self.endpoint.model_id,
            content=content,
            metadata=ResponseMetadata(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                finish_reason=finish_reason,
                duration_ms=0,
                tool_call_count=tool_call_count,
                request_bytes=request_bytes,
            ),
        )


def _failure_code(status: int) -> str:
    if status in _STATUS_CODES:
        return _STATUS_CODES[status]
    if status >= 500:
        return "provider_unavailable"
    return "provider_http_error"


def _render_messages(payload: EgressPayload) -> list[dict[str, str]]:
    """Turn the projected payload into chat messages.

    Only fields the enforcer already allowed appear here. Excerpts are rendered
    by their locator and quote; the local objects behind them -- candidate
    pools, full clauses, the complete table of contents -- have no path into
    this function because they are not on the payload in the first place.
    """
    return [
        {"role": "system", "content": _system_prompt(payload)},
        {"role": "user", "content": _render_user(payload)},
    ]


# IETF TLP 5.0 §3 requires an excerpt to name its source. The condition is on
# the bytes that leave, not on the objects behind them: before this line existed
# a quote went out attached to a truncated content hash and nothing else, which
# satisfied the enforcer and not the licence. `JudgePayload` carries no version
# metadata by design — its gold excerpts are scoring inputs, and the answer under
# review already names what it cites — so it renders no attribution line.
_ATTRIBUTION = (
    "Source: IETF {document_id} ({document_version}). "
    "The excerpts below are unmodified quotations from that RFC."
)


def _system_prompt(payload: EgressPayload) -> str:
    """The judge scores; everything else answers under the citation contract."""
    if isinstance(payload, JudgePayload):
        return _SYSTEM_PROMPT
    return f"{_SYSTEM_PROMPT}\n\n{REPLY_INSTRUCTIONS}"


def _render_user(payload: EgressPayload) -> str:
    lines: list[str] = []
    if not isinstance(payload, JudgePayload):
        lines.append(
            _ATTRIBUTION.format(
                document_id=payload.version.document_id,
                document_version=payload.version.document_version,
            )
        )
    if isinstance(payload, L1OnlinePayload):
        lines.append(f"Question: {payload.query}")
    elif isinstance(payload, L2DesignPayload):
        lines.append(f"Design description: {payload.design_description}")
    elif isinstance(payload, L2AtomicClaimPayload):
        lines.append(f"Claim ({payload.atomic_claim_id}): {payload.atomic_claim}")
    elif isinstance(payload, JudgePayload):
        lines.append(f"Final answer under review: {payload.final_answer}")
        for point in payload.scoring_points:
            lines.append(f"Scoring point {point.point_id}: {point.text}")

    if not isinstance(payload, JudgePayload):
        for node in payload.toc_nodes:
            lines.append(f"Section {node.node_id}: {node.title}")

    excerpts = (
        payload.gold_excerpts
        if isinstance(payload, JudgePayload)
        else payload.evidence_excerpts
    )
    for excerpt in excerpts:
        # The whole identifier, not a prefix. This is the handle the model is
        # asked to cite back, and a truncated one is a handle it cannot use:
        # the parser requires the full hash, so a twelve-character label made
        # every citation malformed by construction.
        lines.append(f"Evidence {excerpt.content_hash}: {excerpt.quote}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """The two planned routes, named where a reader can check them."""

    endpoint: ProviderEndpoint
    description: str = field(default="")


MAIN_ROUTE = RouteConfig(
    endpoint=ProviderEndpoint(
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        api_key_env="SPECPILOT_MAIN_API_KEY",
    ),
    description="online main chain",
)

JUDGE_ROUTE = RouteConfig(
    endpoint=ProviderEndpoint(
        provider_id="chatanywhere",
        model_id="glm-5.2",
        base_url="https://api.chatanywhere.tech/v1",
        api_key_env="SPECPILOT_JUDGE_API_KEY",
    ),
    description="offline judge",
)

LIVE_ROUTES = {"main": MAIN_ROUTE, "judge": JUDGE_ROUTE}


__all__ = [
    "JUDGE_ROUTE",
    "LIVE_ROUTES",
    "MAIN_ROUTE",
    "PROBE_TOOL",
    "ByteUpperBoundCounter",
    "HttpChatAdapter",
    "ProviderCredentialMissing",
    "ProviderEndpoint",
    "RouteConfig",
    "resolve_credential",
]
