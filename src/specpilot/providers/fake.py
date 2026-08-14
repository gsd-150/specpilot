from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar

from specpilot.contracts.egress import (
    EgressPayload,
    JudgePayload,
    L1OnlinePayload,
    L1PlanPayload,
    L2AtomicClaimPayload,
    L2DesignPayload,
)
from specpilot.providers.base import (
    ProviderError,
    ProviderResponse,
    ResponseMetadata,
)

_ACTIVE_DEMO_RUN: ContextVar[str | None] = ContextVar(
    "specpilot_active_demo_run", default=None
)


class _WhitespaceTokenCounter:
    """Deterministic counter for the fixture route. Never used for a real model."""

    def __init__(self, provider_id: str, model_id: str) -> None:
        self.provider_id = provider_id
        self.model_id = model_id

    def count_tokens(self, text: str) -> int:
        return max(len(text.split()), 1)


class FakeProvider:
    """Deterministic fixture provider that records every call it receives.

    Two jobs. It drives the offline demo and fixture smoke without a network,
    and it is the instrument the fail-closed tests use: a violation is only
    proven to be no-send if this adapter's call list stayed empty.
    """

    def __init__(
        self,
        provider_id: str = "provider-a",
        model_id: str = "fixture-model-v1",
        *,
        fail_with: str | None = None,
        reply: str | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self._fail_with = fail_with
        self.reply = reply
        self.calls: list[EgressPayload] = []
        self._demo_scripts: dict[str, str] = {}
        self._demo_script_calls: dict[tuple[str, str], int] = {}

    @property
    def token_counter(self) -> _WhitespaceTokenCounter:
        return _WhitespaceTokenCounter(self.provider_id, self.model_id)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def send(self, projected_payload: EgressPayload) -> ProviderResponse:
        self.calls.append(projected_payload)
        if self._fail_with is not None:
            raise ProviderError(self._fail_with)
        run_id = _ACTIVE_DEMO_RUN.get()
        script_version = self._demo_scripts.get(run_id) if run_id is not None else None
        script_call = self._next_script_call(run_id, projected_payload, script_version)
        content = self.reply
        if content is None:
            content = _scripted_content(projected_payload, script_version, script_call)
        return ProviderResponse(
            provider_id=self.provider_id,
            model_id=self.model_id,
            content=content,
            metadata=ResponseMetadata(
                prompt_tokens=len(content.split()),
                completion_tokens=len(content.split()),
                finish_reason="stop",
                duration_ms=0,
                # The real adapter reports `len(encoded)` of the HTTP body. This
                # one builds no body, so it reports the size of what it was
                # actually handed — not the same bytes, but a real measurement
                # of a real object rather than the default 0. Left unset, every
                # fixture and demo run recorded a request size of zero and the
                # ledger column that holds it was never exercised at all.
                request_bytes=len(
                    projected_payload.model_dump_json().encode("utf-8")
                ),
            ),
        )

    def register_demo_script(self, run_id: str, script_version: str) -> None:
        """Bind a server-selected private fixture script to one ephemeral run."""
        if not run_id or not script_version.startswith("fixture-demo/"):
            raise ValueError("invalid_demo_script")
        existing = self._demo_scripts.setdefault(run_id, script_version)
        if existing != script_version:
            raise ValueError("demo_script_conflict")

    def _next_script_call(
        self,
        run_id: str | None,
        payload: EgressPayload,
        script_version: str | None,
    ) -> int:
        if run_id is None or script_version is None:
            return 0
        key = (run_id, payload.kind)
        call = self._demo_script_calls.get(key, 0) + 1
        self._demo_script_calls[key] = call
        return call

    async def send_for_run(
        self, projected_payload: EgressPayload, *, run_id: str
    ) -> ProviderResponse:
        token = _ACTIVE_DEMO_RUN.set(run_id)
        try:
            return await self.send(projected_payload)
        finally:
            _ACTIVE_DEMO_RUN.reset(token)


def _deterministic_content(payload: EgressPayload) -> str:
    """Derive a stable answer from the payload so fixture runs are reproducible."""
    if isinstance(payload, L1PlanPayload):
        return json.dumps(
            {
                "plan_id": "fixture-plan",
                "steps": [
                    {
                        "step_id": "search",
                        "tool": "search_clauses",
                        "args": {
                            "query": payload.query,
                            "corpus_manifest_id": payload.version.corpus_manifest_id,
                            "document_ids": [payload.version.document_id],
                            "normative_levels": [],
                            "limit": 5,
                        },
                        "depends_on": [],
                    },
                    {
                        "step_id": "read",
                        "tool": "get_clause",
                        "args": {
                            "corpus_manifest_id": payload.version.corpus_manifest_id,
                            "document_id": payload.version.document_id,
                            "clauses": {
                                "kind": "step_result",
                                "step_id": "search",
                                "take": 3,
                            },
                        },
                        "depends_on": ["search"],
                    },
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    if isinstance(payload, L1OnlinePayload):
        citations = (
            [{"evidence_id": payload.evidence_excerpts[0].content_hash}]
            if payload.evidence_excerpts
            else []
        )
        return json.dumps(
            {
                "sufficient": bool(citations),
                "answer": "The deterministic fixture supports the answer."
                if citations
                else None,
                "citations": citations,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    if isinstance(payload, L2DesignPayload):
        evidence_ids = tuple(item.content_hash for item in payload.evidence_excerpts)
        return json.dumps(
            {
                "candidates": [
                    {
                        "claim": "The design satisfies the cited requirement.",
                        "proposed_verdict": (
                            "compliant" if evidence_ids else "insufficient_evidence"
                        ),
                        "evidence_ids": list(evidence_ids[:1]),
                        "rationale": "The deterministic fixture is not verified.",
                    }
                ]
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    if isinstance(payload, L2AtomicClaimPayload):
        return json.dumps(
            {
                "supports_verdict": True,
                "evidence": [
                    {"evidence_id": item.content_hash, "supports": True}
                    for item in payload.evidence_excerpts
                ],
                "reason": "supported",
                "rationale": "The deterministic fixture supports the proposed verdict.",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    excerpts = (
        payload.gold_excerpts
        if isinstance(payload, JudgePayload)
        else payload.evidence_excerpts
    )
    material = "|".join([payload.kind, *(item.quote_hash for item in excerpts)])
    seed = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"fixture answer for {payload.kind} [{seed[:16]}]"


def _scripted_content(
    payload: EgressPayload, script_version: str | None, script_call: int
) -> str:
    if (
        script_version == "fixture-demo/evidence-refused/v1"
        and isinstance(payload, L1OnlinePayload)
    ):
        return json.dumps(
            {"sufficient": False, "answer": None, "citations": []},
            separators=(",", ":"),
            sort_keys=True,
        )
    if (
        script_version == "fixture-demo/verifier-recovered/v1"
        and isinstance(payload, L2DesignPayload)
    ):
        return json.dumps(
            {
                "candidates": [
                    {
                        "claim": "The fixture claim is checked after recovery.",
                        "proposed_verdict": "compliant",
                        "evidence_ids": [payload.evidence_excerpts[0].content_hash],
                        "rationale": "Synthetic output remains private.",
                    }
                ]
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    if (
        script_version == "fixture-demo/verifier-recovered/v1"
        and isinstance(payload, L2AtomicClaimPayload)
        and script_call == 1
    ):
        return json.dumps(
            {
                "supports_verdict": False,
                "evidence": [
                    {"evidence_id": item.content_hash, "supports": False}
                    for item in payload.evidence_excerpts
                ],
                "reason": "exception_missing",
                "rationale": "Synthetic recovery trigger remains private.",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    return _deterministic_content(payload)


__all__ = ["FakeProvider"]
