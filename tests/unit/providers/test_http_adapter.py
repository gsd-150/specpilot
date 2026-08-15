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
import re

import httpx
import pytest

from specpilot.answer.reply import parse_reply
from specpilot.contracts.egress import L2AtomicClaimPayload, L2DesignPayload
from specpilot.contracts.verdict import ComplianceBatch, SemanticDecision
from specpilot.providers.base import ProviderError
from specpilot.providers.fake import FakeProvider
from specpilot.providers.http import (
    HttpChatAdapter,
    ProviderCredentialMissing,
    ProviderEndpoint,
    _system_prompt,
    resolve_credential,
)
from tests.unit.egress.test_planning_projection import planning_request
from tests.unit.egress.test_policy_projection import (
    excerpt,
    l1_payload,
    version_metadata,
)

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


async def test_planning_payload_sends_source_free_catalog_json() -> None:
    captured: list[httpx.Request] = []
    adapter = adapter_returning(ok_body(), capture=captured)
    payload = planning_request(query="When may a sender retry?").payload

    response = await adapter.send(payload)

    messages = json.loads(captured[0].content)["messages"]
    system = next(
        message["content"] for message in messages if message["role"] == "system"
    )
    user = next(message["content"] for message in messages if message["role"] == "user")
    rendered = json.loads(user)
    contract = json.loads(system)
    schema = contract["response_schema"]
    assert rendered == payload.model_dump(mode="json")
    assert payload.query not in system
    assert schema["required"] == ["plan_id", "steps"]
    assert schema["properties"]["steps"]["minItems"] == 1
    assert schema["properties"]["steps"]["maxItems"] == 4
    assert "StepResultRef" in schema["$defs"]
    assert schema["$defs"]["StepResultRef"]["properties"]["take"]["maximum"] == 3
    search_step = schema["$defs"]["SearchClausesStep"]
    assert search_step["required"] == ["step_id", "tool", "args"]
    assert set(search_step["properties"]) == {
        "step_id",
        "tool",
        "args",
        "depends_on",
    }
    assert search_step["properties"]["tool"]["const"] == "search_clauses"
    assert schema["$defs"]["GetClauseArgs"]["required"] == [
        "corpus_manifest_id",
        "document_id",
        "clauses",
    ]
    assert response.metadata.request_bytes == len(captured[0].content)
    assert "excerpt" not in user.lower()
    assert "candidate" not in user.lower()
    assert "disclosure" not in user.lower()


async def test_planning_never_uses_native_provider_tool_calls() -> None:
    captured: list[httpx.Request] = []
    adapter = adapter_returning(ok_body(), capture=captured, probe_tools=True)

    await adapter.send(planning_request(query="When may a sender retry?").payload)

    body = json.loads(captured[0].content)
    assert "tools" not in body


def test_planning_payload_uses_the_formal_json_only_contract() -> None:
    payload = planning_request(query="When may a sender retry?").payload

    system = _system_prompt(payload)

    contract = json.loads(system)
    assert contract["instruction"].endswith("rather than assuming.")
    assert contract["response_schema"]["title"] == "ToolPlan"
    assert "citations" not in system.lower()


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


async def test_the_response_records_how_many_bytes_the_request_was() -> None:
    """The only figure that can be compared with `prompt_tokens` like for like.

    The excerpt projection prices what the cap governs -- source text -- while
    `prompt_tokens` covers the whole prompt including the system message and any
    tool schema. Putting those two beside each other reads as a calibration and
    is not one. The request byte count is the number the upper bound is actually
    a bound on.
    """
    adapter = adapter_returning(ok_body())

    response = await adapter.send(l1_payload())

    assert response.metadata.request_bytes > 0
    assert response.metadata.request_bytes >= response.metadata.prompt_tokens, (
        "a byte-level BPE cannot emit more tokens than the request has bytes"
    )


async def test_probing_adds_its_tool_schema_to_the_counted_bytes() -> None:
    """The bound has to cover everything sent, not just the rendered payload."""
    plain = await adapter_returning(ok_body()).send(l1_payload())
    probing = await adapter_returning(ok_body(), probe_tools=True).send(l1_payload())

    assert probing.metadata.request_bytes > plain.metadata.request_bytes


async def test_the_excerpts_go_out_naming_the_rfc_they_came_from() -> None:
    """IETF TLP 5.0 §3 requires an excerpt to name its source.

    The condition is on the bytes that leave, not on the objects behind them.
    Before this, a quote went out attached to a truncated content hash and
    nothing else — which satisfied the enforcer and not the licence.
    """
    captured: list[httpx.Request] = []
    adapter = adapter_returning(ok_body(), capture=captured)
    payload = l1_payload()

    await adapter.send(payload)

    sent = json.loads(captured[0].content)
    user = next(m["content"] for m in sent["messages"] if m["role"] == "user")
    assert user.startswith("Source: IETF ")
    assert payload.version.document_id in user
    assert payload.version.document_version in user
    assert "unmodified quotations" in user


async def test_the_reply_contract_is_actually_sent() -> None:
    """It was written and never wired, so the first live call came back prose.

    The instructions lived beside the parser, which the provider adapter cannot
    import without inverting the layering — the kind of gap that only a real
    call finds, because every test supplied its own canned reply.
    """
    captured: list[httpx.Request] = []
    adapter = adapter_returning(ok_body(), capture=captured)

    await adapter.send(l1_payload())

    system = next(
        m["content"]
        for m in json.loads(captured[0].content)["messages"]
        if m["role"] == "system"
    )
    assert '"sufficient"' in system
    assert '"citations"' in system
    assert "Do not cite anything you were not shown" in system


async def test_the_identifier_shown_is_the_identifier_the_parser_takes() -> None:
    """The join no test crossed, which is why the first answerable call failed.

    The payload labelled each excerpt with a twelve-character prefix of a
    content hash; the contract asked for a `clause_id`; the parser required 64
    hex characters. Three components, each self-consistent, and a model that
    could not cite anything — it was being asked for an identifier it had never
    been shown.

    So this test plays the model: it copies back exactly what the bytes offered
    and nothing else. Anything less exact — reading the id off the payload
    object rather than the rendered text — would test the objects again and miss
    the same gap a second time.
    """
    captured: list[httpx.Request] = []
    adapter = adapter_returning(ok_body(), capture=captured)
    payload = l1_payload()

    await adapter.send(payload)

    user = next(
        m["content"]
        for m in json.loads(captured[0].content)["messages"]
        if m["role"] == "user"
    )
    shown = re.findall(r"^Evidence ([0-9a-f]+):", user, flags=re.MULTILINE)
    assert len(shown) == len(payload.evidence_excerpts)

    parsed = parse_reply(
        json.dumps(
            {"sufficient": True, "answer": "It must.", "citations": shown}
        )
    )

    assert parsed.parse_fault is None
    assert [c.evidence_id for c in parsed.citations] == shown


def l2_design_payload() -> L2DesignPayload:
    return L2DesignPayload(
        design_description="A sender always emits the field.",
        version=version_metadata(),
        evidence_excerpts=(excerpt(),),
    )


def l2_atomic_claim_payload() -> L2AtomicClaimPayload:
    return L2AtomicClaimPayload(
        atomic_claim_id="claim-1",
        atomic_claim="A sender always emits the field.",
        version=version_metadata(),
        evidence_excerpts=(excerpt(),),
    )


async def test_compliance_fixture_cites_rendered_evidence_handles_only() -> None:
    captured: list[httpx.Request] = []
    payload = l2_design_payload()
    adapter = adapter_returning(ok_body(), capture=captured)

    await adapter.send(payload)

    user = next(
        message["content"]
        for message in json.loads(captured[0].content)["messages"]
        if message["role"] == "user"
    )
    system = next(
        message["content"]
        for message in json.loads(captured[0].content)["messages"]
        if message["role"] == "system"
    )
    shown = re.findall(r"^Evidence ([0-9a-f]{64}):", user, flags=re.MULTILINE)
    reply = await FakeProvider().send(payload)

    parsed = ComplianceBatch.model_validate_json(reply.content)

    assert json.loads(system)["response_schema"]["title"] == "ComplianceBatch"
    assert [candidate.evidence_ids for candidate in parsed.candidates] == [tuple(shown)]


async def test_semantic_fixture_decides_over_rendered_evidence_handles() -> None:
    captured: list[httpx.Request] = []
    payload = l2_atomic_claim_payload()
    adapter = adapter_returning(ok_body(), capture=captured)

    await adapter.send(payload)

    user = next(
        message["content"]
        for message in json.loads(captured[0].content)["messages"]
        if message["role"] == "user"
    )
    system = next(
        message["content"]
        for message in json.loads(captured[0].content)["messages"]
        if message["role"] == "system"
    )
    shown = re.findall(r"^Evidence ([0-9a-f]{64}):", user, flags=re.MULTILINE)
    reply = await FakeProvider().send(payload)

    parsed = SemanticDecision.model_validate_json(reply.content)

    assert json.loads(system)["response_schema"]["title"] == "SemanticDecision"
    assert [decision.evidence_id for decision in parsed.evidence] == shown


async def test_undisclosed_compliance_id_survives_fake_reply_unchanged() -> None:
    payload = l2_design_payload()
    undisclosed = "f" * 64
    provider = FakeProvider(
        reply=json.dumps(
            {
                "candidates": [
                    {
                        "claim": "A sender always emits the field.",
                        "proposed_verdict": "compliant",
                        "evidence_ids": [undisclosed],
                        "rationale": "Untrusted candidate output.",
                    }
                ]
            }
        )
    )

    reply = await provider.send(payload)
    parsed = ComplianceBatch.model_validate_json(reply.content)

    assert parsed.candidates[0].evidence_ids == (undisclosed,)
    assert undisclosed not in {item.content_hash for item in payload.evidence_excerpts}


def test_compliance_instructions_state_the_insufficient_evidence_rule() -> None:
    """The cross-join guard for the closed candidate contract.

    The contract refuses evidence ids on an insufficient candidate; the
    instructions must tell the model the same rule, or the model returns the
    natural-but-invalid shape (evidence examined, verdict insufficient) and
    the first live run fails exactly as it did.
    """
    from specpilot.contracts.verdict import ComplianceBatch
    from specpilot.providers.http import COMPLIANCE_REPLY_INSTRUCTIONS

    assert "insufficient_evidence" in COMPLIANCE_REPLY_INSTRUCTIONS
    assert "empty list" in COMPLIANCE_REPLY_INSTRUCTIONS
    batch = ComplianceBatch.model_validate(
        {
            "candidates": [
                {
                    "claim": "undetermined claim",
                    "proposed_verdict": "insufficient_evidence",
                    "rationale": "the excerpts do not settle it",
                    "evidence_ids": [],
                }
            ]
        }
    )
    assert len(batch.candidates) == 1


def test_planning_instructions_state_the_call_budget_rule() -> None:
    """The cross-join guard for the bounded tool budget.

    A step's take multiplies its call cost and the total must fit the budget,
    but the model cannot infer that from a bare schema; the first live planner
    returned a four-step plan costing 10 against a budget of 8. The
    instruction now states the rule, and this test pins it beside the
    validator that enforces it.
    """
    from specpilot.agents.contracts import validate_tool_plan
    from specpilot.agents.planner import _tool_catalog
    from specpilot.providers.http import _PLANNING_SYSTEM_PROMPT

    assert "call budget" in _PLANNING_SYSTEM_PROMPT
    assert "costs its take" in _PLANNING_SYSTEM_PROMPT
    assert any(
        "Maximum MCP calls" in tool.description for tool in _tool_catalog(8)
    )
    with __import__("pytest").raises(ValueError):
        tool_plan = __import__(
            "specpilot.agents.contracts", fromlist=["ToolPlan"]
        ).ToolPlan
        validate_tool_plan(  # pragma: no cover - shape checked by other tests
            tool_plan.model_validate(
                {
                    "plan_id": "over-budget",
                    "steps": [
                        {
                            "step_id": "s1",
                            "tool": "search_clauses",
                            "args": {
                                "query": "q",
                                "corpus_manifest_id": "a" * 64,
                                "document_ids": ["ietf-rfc-9110"],
                                "limit": 10,
                            },
                        },
                        {
                            "step_id": "s2",
                            "tool": "get_clause",
                            "depends_on": ["s1"],
                            "args": {
                                "corpus_manifest_id": "a" * 64,
                                "document_id": "ietf-rfc-9110",
                                "clauses": {
                                    "kind": "step_result",
                                    "step_id": "s1",
                                    "take": 8,
                                },
                            },
                        },
                    ],
                }
            ),
            max_call_cost=8,
        )
