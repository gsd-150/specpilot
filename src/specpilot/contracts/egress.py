from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from specpilot.contracts.manifests import (
    Identifier,
    ProviderRouteBinding,
    RfcSourceManifest,
    Sha256,
    SourceManifest,
)

BoundedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=65_536),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskLevel(StrEnum):
    L1 = "L1"
    L2 = "L2"


class EgressStage(StrEnum):
    EVIDENCE = "evidence"
    COMPLIANCE = "compliance"
    VERIFIER = "verifier"
    JUDGE = "judge"


class VersionMetadata(_FrozenModel):
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256
    document_id: Identifier
    document_version: Identifier


class TocNode(_FrozenModel):
    node_id: Identifier
    title: ShortText


class NormalizedExcerptSpan(_FrozenModel):
    paragraph_start: Annotated[int, Field(ge=0)]
    paragraph_end: Annotated[int, Field(ge=0)]
    token_start: Annotated[int, Field(ge=0)]
    token_end: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _require_ordered_coordinates(self) -> NormalizedExcerptSpan:
        if self.paragraph_end < self.paragraph_start:
            raise ValueError("paragraph_end must not precede paragraph_start")
        if self.token_end <= self.token_start:
            raise ValueError("token_end must follow token_start")
        return self


class EvidenceExcerpt(_FrozenModel):
    corpus_manifest_id: Sha256
    content_hash: Sha256
    quote: BoundedText
    quote_hash: Sha256
    span: NormalizedExcerptSpan

    @model_validator(mode="after")
    def _verify_exact_quote_hash(self) -> EvidenceExcerpt:
        expected = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.quote_hash != expected:
            raise ValueError("quote_hash does not match the exact quote")
        return self


class L1OnlinePayload(_FrozenModel):
    kind: Literal["l1_query"] = "l1_query"
    query: ShortText
    version: VersionMetadata
    toc_nodes: Annotated[tuple[TocNode, ...], Field(max_length=12)] = ()
    evidence_excerpts: tuple[EvidenceExcerpt, ...] = ()


class L2DesignPayload(_FrozenModel):
    kind: Literal["l2_design"] = "l2_design"
    design_description: BoundedText
    version: VersionMetadata
    toc_nodes: Annotated[tuple[TocNode, ...], Field(max_length=12)] = ()
    evidence_excerpts: tuple[EvidenceExcerpt, ...] = ()


class L2AtomicClaimPayload(_FrozenModel):
    kind: Literal["l2_atomic_claim"] = "l2_atomic_claim"
    atomic_claim_id: Identifier
    atomic_claim: ShortText
    version: VersionMetadata
    toc_nodes: Annotated[tuple[TocNode, ...], Field(max_length=12)] = ()
    evidence_excerpts: tuple[EvidenceExcerpt, ...] = ()


class ScoringPoint(_FrozenModel):
    point_id: Identifier
    text: ShortText


class JudgePayload(_FrozenModel):
    kind: Literal["judge"] = "judge"
    final_answer: BoundedText
    scoring_points: Annotated[tuple[ScoringPoint, ...], Field(max_length=128)]
    gold_excerpts: tuple[EvidenceExcerpt, ...] = ()


type EgressPayload = Annotated[
    L1OnlinePayload | L2DesignPayload | L2AtomicClaimPayload | JudgePayload,
    Field(discriminator="kind"),
]


class EgressRequest(_FrozenModel):
    evaluation_root_id: Identifier
    run_id: Identifier
    task_level: TaskLevel
    version: VersionMetadata
    stage: EgressStage
    route: ProviderRouteBinding
    model_id: Identifier
    source_manifest: SourceManifest
    payload: EgressPayload


class TokenCounter(Protocol):
    provider_id: str
    model_id: str

    def count_tokens(self, text: str) -> int: ...


class SourceManifestResolver(Protocol):
    """Reads a stored source manifest by ID; never trusts a caller-supplied copy.

    The return type spans every manifest version on purpose. Egress
    authorization is a property of the recorded compliance decision, not of the
    source document's file format, so the enforcer must not be able to tell a
    DOCX corpus from an RFC one.
    """

    def read_source(self, manifest_id: str) -> SourceManifest | RfcSourceManifest: ...


class DisclosureFact(_FrozenModel):
    disclosure_id: Sha256
    corpus_manifest_id: Sha256
    content_hash: Sha256
    quote_hash: Sha256
    span: NormalizedExcerptSpan
    token_count: Annotated[int, Field(gt=0)]
    byte_count: Annotated[int, Field(gt=0)]


class CapVector(_FrozenModel):
    excerpts: Annotated[int, Field(ge=0)]
    tokens: Annotated[int, Field(ge=0)]
    bytes: Annotated[int, Field(ge=0)]


class CapSnapshot(_FrozenModel):
    excerpt: CapVector
    online_unique: CapVector | None
    online_transmitted: CapVector | None
    claim_unique: CapVector | None
    judge_unique: CapVector
    judge_transmitted: CapVector
    root_unique: CapVector
    root_transmitted: CapVector
    corpus_unique: CapVector
    corpus_document_unique: CapVector
    toc_per_call: Annotated[int, Field(gt=0)]
    toc_per_run: Annotated[int, Field(gt=0)]
    max_claims_per_run: Annotated[int, Field(gt=0)]
    projected_text_tokens: Annotated[int, Field(gt=0)] | None


class ReservationRequest(_FrozenModel):
    schema_version: Literal["egress-reservation/v1"] = "egress-reservation/v1"
    evaluation_root_id: Identifier
    run_id: Identifier
    task_level: TaskLevel
    version: VersionMetadata
    stage: EgressStage
    route: ProviderRouteBinding
    model_id: Identifier
    source_manifest: SourceManifest
    projected_payload: EgressPayload
    disclosures: tuple[DisclosureFact, ...]


class StageUsage(_FrozenModel):
    stage: EgressStage
    transmitted_tokens: Annotated[int, Field(ge=0)] = 0
    transmitted_bytes: Annotated[int, Field(ge=0)] = 0
    transmissions: Annotated[int, Field(ge=0)] = 0


class RouteUsage(_FrozenModel):
    route: ProviderRouteBinding
    disclosure_ids: tuple[Sha256, ...] = ()
    unique_tokens: Annotated[int, Field(ge=0)] = 0
    unique_bytes: Annotated[int, Field(ge=0)] = 0


class ClaimUsage(_FrozenModel):
    atomic_claim_id: Identifier
    disclosure_ids: tuple[Sha256, ...] = ()
    unique_tokens: Annotated[int, Field(ge=0)] = 0
    unique_bytes: Annotated[int, Field(ge=0)] = 0


class RunUsage(_FrozenModel):
    run_id: Identifier
    disclosure_ids: tuple[Sha256, ...] = ()
    unique_tokens: Annotated[int, Field(ge=0)] = 0
    unique_bytes: Annotated[int, Field(ge=0)] = 0
    transmitted_tokens: Annotated[int, Field(ge=0)] = 0
    transmitted_bytes: Annotated[int, Field(ge=0)] = 0
    toc_nodes: Annotated[int, Field(ge=0)] = 0
    claim_usage: tuple[ClaimUsage, ...] = ()


class CorpusDocumentUsage(_FrozenModel):
    """One document's share of the corpus ledger.

    Kept inside ``CorpusUsage`` rather than in its own row so the single corpus
    lock still serializes every reservation. Splitting documents into separate
    rows would buy concurrency this evaluation does not need and would cost the
    fixed lock order that prevents deadlock.
    """

    document_id: Identifier
    disclosure_ids: tuple[Sha256, ...] = ()
    unique_tokens: Annotated[int, Field(ge=0)] = 0
    unique_bytes: Annotated[int, Field(ge=0)] = 0


class CorpusUsage(_FrozenModel):
    """Unique source text ever disclosed from one frozen corpus, across all cases.

    This is the only scope above ``evaluation_root_id``. It answers "how much of
    this specification has left the machine in total", which is the question the
    product plan's outbound-limit premise actually rests on. It counts unique
    disclosures only: re-sending the same excerpt is a transmitted-usage concern,
    not a new disclosure of source text.

    ``document_usage`` is the same question asked per document, because the
    licence condition behind the cap is written per document: pooling two
    specifications cannot show that either one stayed under its own share.
    """

    schema_version: Literal["egress-corpus-usage/v2"] = "egress-corpus-usage/v2"
    corpus_manifest_id: Sha256
    policy_hash: Sha256
    disclosure_ids: tuple[Sha256, ...] = ()
    unique_tokens: Annotated[int, Field(ge=0)] = 0
    unique_bytes: Annotated[int, Field(ge=0)] = 0
    document_usage: tuple[CorpusDocumentUsage, ...] = ()


class UsageSnapshot(_FrozenModel):
    schema_version: Literal["egress-usage/v1"] = "egress-usage/v1"
    evaluation_root_id: Identifier
    task_level: TaskLevel
    policy_hash: Sha256
    disclosures: tuple[DisclosureFact, ...] = ()
    route_usage: tuple[RouteUsage, ...] = ()
    stage_usage: tuple[StageUsage, ...] = ()
    run_usage: tuple[RunUsage, ...] = ()
    judge_disclosure_ids: tuple[Sha256, ...] = ()
    judge_unique_tokens: Annotated[int, Field(ge=0)] = 0
    judge_unique_bytes: Annotated[int, Field(ge=0)] = 0
    judge_transmitted_tokens: Annotated[int, Field(ge=0)] = 0
    judge_transmitted_bytes: Annotated[int, Field(ge=0)] = 0
    root_unique_tokens: Annotated[int, Field(ge=0)] = 0
    root_unique_bytes: Annotated[int, Field(ge=0)] = 0
    root_transmitted_tokens: Annotated[int, Field(ge=0)] = 0
    root_transmitted_bytes: Annotated[int, Field(ge=0)] = 0


class ReservationOutcome(_FrozenModel):
    """Both budget scopes a reservation moves, so neither can be applied alone.

    The ledger persists these as two rows with different keys, but they must be
    read, checked, and written inside one transaction.
    """

    usage: UsageSnapshot
    corpus_usage: CorpusUsage
