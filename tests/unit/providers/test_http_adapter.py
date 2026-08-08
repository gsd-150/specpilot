"""The real provider adapter, exercised without a network.

Every case here drives `httpx.MockTransport`, so the suite proves the adapter's
contract -- what it sends, what it refuses to keep, how it names failures --
without a key and without reaching anyone. What it deliberately cannot prove is
the thing §4.6.1 actually asks for: that a named model slug exists on a real
route. Only `provider route-smoke --live` can answer that, and its own output
says so.
"""

from __future__ import annotations

import json

import httpx
import pytest

from specpilot.providers.base import ProviderError
from specpilot.providers.http import (
    HttpChatAdapter,
    ProviderCredentialMissing,
    ProviderEndpoint,
    resolve_credential,
)
from tests.unit.egress.test_policy_projection import l1_payload

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


ENDPOINT = ProviderEndpoint(
    provider_id="provider-a",
    model_id="some-model-v1",
    base_url="https://api.invalid/v1",
    api_key_env="SPECPILOT_TEST_KEY",
)


def ok_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "chatcmpl-abc",
        "model": "some-model-v1",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "an answer"},
            }
        ],
        "usage": {"prompt_tokens": 41, "completion_tokens": 7},
    }
    body.update(overrides)
    return body


def adapter_returning(
    body: object,
    status: int = 200,
    *,
    capture: list[httpx.Request] | None = None,
    probe_tools: bool = False,
) -> HttpChatAdapter:
    def handle(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    return HttpChatAdapter(
        ENDPOINT,
        api_key="sk-not-a-real-key",
        transport=httpx.MockTransport(handle),
        probe_tools=probe_tools,
    )


async def test_a_successful_call_returns_only_allowlisted_response_facts() -> None:
    adapter = adapter_returning(ok_body())

    response = await adapter.send(l1_payload())

    assert response.provider_id == "provider-a"
    assert response.model_id == "some-model-v1"
    assert response.content == "an answer"
    assert response.metadata.prompt_tokens == 41
    assert response.metadata.completion_tokens == 7
    assert response.metadata.finish_reason == "stop"


async def test_the_request_carries_the_key_and_the_payload_and_nothing_else() -> None:
    captured: list[httpx.Request] = []
    adapter = adapter_returning(ok_body(), capture=captured)

    await adapter.send(l1_payload())

    request = captured[0]
    assert request.headers["authorization"] == "Bearer sk-not-a-real-key"
    body = json.loads(request.content)
    assert body["model"] == "some-model-v1"
    assert {message["role"] for message in body["messages"]} <= {"system", "user"}
    assert "tools" not in body, "no tool schema unless the adapter is probing"


async def test_the_key_never_appears_in_a_repr() -> None:
    """A key reaching a traceback frame or a debug log is a key that leaked."""
    adapter = adapter_returning(ok_body())

    rendered = f"{adapter!r} {adapter.endpoint!r}"

    assert "sk-not-a-real-key" not in rendered


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "provider_unauthorized"),
        (403, "provider_unauthorized"),
        (404, "provider_model_not_found"),
        (429, "provider_rate_limited"),
        (500, "provider_unavailable"),
        (503, "provider_unavailable"),
        (418, "provider_http_error"),
    ],
)
async def test_every_http_failure_becomes_a_stable_public_code(
    status: int, code: str
) -> None:
    leaky = {"error": {"message": "sk-not-a-real-key leaked"}}
    adapter = adapter_returning(leaky, status)

    with pytest.raises(ProviderError) as caught:
        await adapter.send(l1_payload())

    assert caught.value.public_error_code == code
    assert "sk-not-a-real-key" not in str(caught.value), "no provider text is carried"


async def test_a_timeout_is_named_rather_than_raised_raw() -> None:
    adapter = adapter_returning(httpx.ConnectTimeout("slow"))

    with pytest.raises(ProviderError) as caught:
        await adapter.send(l1_payload())

    assert caught.value.public_error_code == "provider_timeout"


@pytest.mark.parametrize(
    "body",
    [
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"choices": [{"finish_reason": "stop", "message": {}}]},
        ok_body(usage={"prompt_tokens": 1}),
        "not an object",
    ],
)
async def test_a_response_missing_required_facts_is_refused(body: object) -> None:
    """A reply the contract cannot describe is a failure, not a partial success."""
    adapter = adapter_returning(body)

    with pytest.raises(ProviderError) as caught:
        await adapter.send(l1_payload())

    assert caught.value.public_error_code == "provider_malformed_response"


async def test_a_model_slug_the_route_swapped_underneath_is_refused() -> None:
    """The reply must come from the model the reservation was priced against."""
    adapter = adapter_returning(ok_body(model="some-other-model"))

    with pytest.raises(ProviderError) as caught:
        await adapter.send(l1_payload())

    assert caught.value.public_error_code == "provider_model_mismatch"


async def test_probing_sends_one_tool_and_counts_what_came_back() -> None:
    captured: list[httpx.Request] = []
    tool_reply = ok_body(
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "specpilot_probe", "arguments": "{}"},
                        }
                    ],
                },
            }
        ]
    )
    adapter = adapter_returning(tool_reply, capture=captured, probe_tools=True)

    response = await adapter.send(l1_payload())

    body = json.loads(captured[0].content)
    assert [tool["function"]["name"] for tool in body["tools"]] == ["specpilot_probe"]
    assert response.metadata.tool_call_count == 1
    assert response.metadata.finish_reason == "tool_calls"
    assert response.content == "", "a tool call carries no answer text"


async def test_the_token_counter_never_undercounts_the_provider() -> None:
    """The reservation is priced before the call, so the estimate must be a bound.

    Byte-level BPE emits at most one token per UTF-8 byte, so the byte count is
    a real upper bound on any such tokenizer rather than a guess at this one.
    """
    adapter = adapter_returning(ok_body())
    counter = adapter.token_counter

    assert counter.provider_id == "provider-a"
    assert counter.model_id == "some-model-v1"
    for text in ("hello", "Content-Length: 5", "一个中文条款", "x" * 400):
        assert counter.count_tokens(text) == len(text.encode("utf-8"))


def test_a_missing_credential_fails_at_construction_not_at_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovering an absent key mid-run would strand a reserved budget."""
    monkeypatch.delenv("SPECPILOT_TEST_KEY", raising=False)

    with pytest.raises(ProviderCredentialMissing) as caught:
        resolve_credential(ENDPOINT)

    assert "SPECPILOT_TEST_KEY" in str(caught.value)
    assert "sk-" not in str(caught.value)


def test_a_blank_credential_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECPILOT_TEST_KEY", "   ")

    with pytest.raises(ProviderCredentialMissing):
        resolve_credential(ENDPOINT)


def test_a_present_credential_is_returned_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECPILOT_TEST_KEY", "  sk-value\n")

    assert resolve_credential(ENDPOINT) == "sk-value"
