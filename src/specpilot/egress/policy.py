from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from specpilot.contracts.egress import CapSnapshot, CapVector, NormalizedExcerptSpan

_POLICY_PACKAGE = "specpilot.egress.policies"
_DEFAULT_POLICY_NAME = "default-v1.json"


class MissingDocumentCap(LookupError):
    """The policy prices no share of this document, so no share may be disclosed.

    Raised rather than defaulted on purpose. Each per-document cap is one fifth
    of that document's measured size; a document nobody measured has no such
    number, and inventing one would be inventing the licence argument with it.
    """

    def __init__(self, document_id: str) -> None:
        super().__init__(f"policy prices no outbound share for {document_id!r}")
        self.document_id = document_id


class _PolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EgressPolicy(_PolicyModel):
    schema_version: str
    stage_payload_allowlist: dict[str, tuple[str, ...]]
    excerpt: CapVector
    l1_online_unique: CapVector
    l1_online_transmitted: CapVector
    l2_online_unique: CapVector
    l2_claim_unique: CapVector
    l2_online_transmitted: CapVector
    judge_unique: CapVector
    judge_transmitted: CapVector
    l1_root_unique: CapVector
    l1_root_transmitted: CapVector
    l2_root_unique: CapVector
    l2_root_transmitted: CapVector
    corpus_unique: CapVector
    corpus_document_unique: dict[str, CapVector]
    toc_per_call: int
    toc_per_run: int
    l1_query_tokens: int
    l2_design_tokens: int
    max_l2_claims_per_run: int

    @classmethod
    def load(cls, path: Path | None = None) -> EgressPolicy:
        """Load the packaged default policy, or an explicit JSON policy file."""
        source = path.read_text(encoding="utf-8") if path else _default_policy_text()
        try:
            raw = json.loads(source)
        except json.JSONDecodeError as error:
            raise ValueError(
                "egress policy must be JSON; comments and YAML syntax are not read"
            ) from error
        if not isinstance(raw, dict):
            raise ValueError("egress policy root must be a mapping")
        return cls.model_validate(raw)

    @property
    def policy_hash(self) -> str:
        return _canonical_hash(self.model_dump(mode="json"))

    def snapshot(
        self,
        *,
        task_level: str,
        payload_kind: str,
        document_id: str,
    ) -> CapSnapshot:
        is_l1 = task_level == "L1"
        is_judge = payload_kind == "judge"
        projected_text_tokens: int | None
        if payload_kind == "l1_query":
            projected_text_tokens = self.l1_query_tokens
        elif payload_kind in {"l2_design", "l2_atomic_claim"}:
            projected_text_tokens = self.l2_design_tokens
        else:
            projected_text_tokens = None
        document_cap = self.corpus_document_unique.get(document_id)
        if document_cap is None:
            raise MissingDocumentCap(document_id)
        return CapSnapshot(
            excerpt=self.excerpt,
            online_unique=None
            if is_judge
            else (self.l1_online_unique if is_l1 else self.l2_online_unique),
            online_transmitted=None
            if is_judge
            else (
                self.l1_online_transmitted
                if is_l1
                else self.l2_online_transmitted
            ),
            claim_unique=self.l2_claim_unique
            if payload_kind == "l2_atomic_claim"
            else None,
            judge_unique=self.judge_unique,
            judge_transmitted=self.judge_transmitted,
            root_unique=self.l1_root_unique if is_l1 else self.l2_root_unique,
            root_transmitted=(
                self.l1_root_transmitted if is_l1 else self.l2_root_transmitted
            ),
            corpus_unique=self.corpus_unique,
            corpus_document_unique=document_cap,
            toc_per_call=self.toc_per_call,
            toc_per_run=self.toc_per_run,
            max_claims_per_run=self.max_l2_claims_per_run,
            projected_text_tokens=projected_text_tokens,
        )


def _default_policy_text() -> str:
    """Read the policy that ships inside the installed package, not the repo tree."""
    resource = resources.files(_POLICY_PACKAGE).joinpath(_DEFAULT_POLICY_NAME)
    return resource.read_text(encoding="utf-8")


def disclosure_id(
    corpus_manifest_id: str,
    content_hash: str,
    quote_hash: str,
    normalized_excerpt_span: NormalizedExcerptSpan,
) -> str:
    canonical_tuple = [
        corpus_manifest_id,
        content_hash,
        quote_hash,
        normalized_excerpt_span.model_dump(mode="json"),
    ]
    return hashlib.sha256(
        json.dumps(
            canonical_tuple,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def cap_snapshot_hash(snapshot: CapSnapshot) -> str:
    return _canonical_hash(snapshot.model_dump(mode="json"))
