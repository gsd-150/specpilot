from __future__ import annotations

import hashlib

from specpilot.contracts.egress import EgressPayload, JudgePayload
from specpilot.providers.base import (
    ProviderError,
    ProviderResponse,
    ResponseMetadata,
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
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self._fail_with = fail_with
        self.calls: list[EgressPayload] = []

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
        content = _deterministic_content(projected_payload)
        return ProviderResponse(
            provider_id=self.provider_id,
            model_id=self.model_id,
            content=content,
            metadata=ResponseMetadata(
                prompt_tokens=len(content.split()),
                completion_tokens=len(content.split()),
                finish_reason="stop",
                duration_ms=0,
            ),
        )


def _deterministic_content(payload: EgressPayload) -> str:
    """Derive a stable answer from the payload so fixture runs are reproducible."""
    excerpts = (
        payload.gold_excerpts
        if isinstance(payload, JudgePayload)
        else payload.evidence_excerpts
    )
    material = "|".join([payload.kind, *(item.quote_hash for item in excerpts)])
    seed = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"fixture answer for {payload.kind} [{seed[:16]}]"


__all__ = ["FakeProvider"]
