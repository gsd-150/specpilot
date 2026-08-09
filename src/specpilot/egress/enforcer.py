from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from specpilot.contracts.egress import (
    CapSnapshot,
    CapVector,
    ClaimUsage,
    CorpusDocumentUsage,
    CorpusUsage,
    DisclosureFact,
    EgressRequest,
    EgressStage,
    JudgePayload,
    L1OnlinePayload,
    L2AtomicClaimPayload,
    L2DesignPayload,
    ReservationOutcome,
    ReservationRequest,
    RouteUsage,
    RunUsage,
    SourceManifestResolver,
    StageUsage,
    TaskLevel,
    TokenCounter,
    UsageSnapshot,
)
from specpilot.contracts.manifests import (
    ProviderUse,
    RfcSourceManifest,
    SourceManifest,
)
from specpilot.egress.policy import (
    EgressPolicy,
    MissingDocumentCap,
    disclosure_id,
)


class EgressPolicyViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class TokenAccountingUnavailable(EgressPolicyViolation):
    def __init__(
        self,
        message: str = "model-compatible token accounting is unavailable",
        *,
        code: str = "token_accounting_unavailable",
    ) -> None:
        super().__init__(code, message)


@dataclass(frozen=True)
class _DerivedReservation:
    cap_snapshot: CapSnapshot
    disclosures: tuple[DisclosureFact, ...]
    transmitted_tokens: int
    transmitted_bytes: int
    toc_delta: int
    atomic_claim_id: str | None


class EgressPolicyEnforcer:
    def __init__(
        self,
        policy: EgressPolicy | None = None,
        *,
        manifests: SourceManifestResolver,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy or EgressPolicy.load()
        self._manifests = manifests
        self._clock = clock or (lambda: datetime.now(UTC))

    def prepare(
        self,
        request: EgressRequest,
        counter: TokenCounter,
    ) -> ReservationRequest:
        derived = self._derive(request, counter)
        return ReservationRequest(
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            task_level=request.task_level,
            version=request.version,
            stage=request.stage,
            route=request.route,
            model_id=request.model_id,
            source_manifest=request.source_manifest,
            projected_payload=request.payload,
            disclosures=derived.disclosures,
        )

    def apply_reservation(
        self,
        existing_usage: UsageSnapshot | None,
        existing_corpus_usage: CorpusUsage | None,
        request: ReservationRequest,
        counter: TokenCounter,
    ) -> ReservationOutcome:
        """Move both budget scopes at once.

        The two are passed separately because they have different lifetimes: the
        usage snapshot is keyed by ``evaluation_root_id`` and starts empty for
        each case, while corpus usage is keyed by ``corpus_manifest_id`` and
        carries across every case run against that frozen corpus.
        """
        if set(request.__dict__) != set(type(request).model_fields):
            _reject(
                "reservation_primitive_invalid",
                "reservation contains non-contract primitive fields",
            )
        canonical_request = EgressRequest(
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            task_level=request.task_level,
            version=request.version,
            stage=request.stage,
            route=request.route,
            model_id=request.model_id,
            source_manifest=request.source_manifest,
            payload=request.projected_payload,
        )
        derived = self._derive(canonical_request, counter)
        if request.disclosures != derived.disclosures:
            _reject(
                "reservation_accounting_mismatch",
                "disclosure facts do not match canonical reservation primitives",
            )
        policy_hash = self._policy.policy_hash
        return ReservationOutcome(
            usage=_apply_usage(
                existing_usage,
                request,
                derived,
                policy_hash=policy_hash,
            ),
            corpus_usage=_apply_corpus_usage(
                existing_corpus_usage,
                request,
                derived,
                policy_hash=policy_hash,
            ),
        )

    def _derive(
        self,
        request: EgressRequest,
        counter: TokenCounter,
    ) -> _DerivedReservation:
        payload = request.payload
        inferred_level = _payload_task_level(payload)
        if inferred_level is not None and inferred_level != request.task_level:
            _reject("task_level_mismatch", "task level does not match payload")
        task_level = request.task_level
        allowed = self._policy.stage_payload_allowlist.get(request.stage.value)
        if allowed is None or payload.kind not in allowed:
            _reject("stage_payload_mismatch", "stage does not allow payload kind")
        expected_use = (
            ProviderUse.OFFLINE_JUDGE
            if isinstance(payload, JudgePayload)
            else ProviderUse.ONLINE_MAIN
        )
        if request.route.use != expected_use:
            _reject("stage_route_mismatch", "payload does not match route use")
        _validate_version_binding(request)
        stored_source = self._stored_source_manifest(request)
        authorization_time = self._authorization_time()
        if not stored_source.authorizes(request.route, at=authorization_time):
            _reject("route_unauthorized", "source manifest does not authorize route")
        _verify_counter(counter, request)

        try:
            snapshot = self._policy.snapshot(
                task_level=task_level.value,
                payload_kind=payload.kind,
                document_id=request.version.document_id,
            )
        except MissingDocumentCap:
            _reject(
                "corpus_document_cap_missing",
                "policy prices no outbound share for this document",
            )
        projected_text = _projected_text(payload)
        if projected_text is not None and snapshot.projected_text_tokens is not None:
            projected_count = _count(counter, projected_text)
            if projected_count > snapshot.projected_text_tokens:
                _reject("projected_text_tokens_exceeded", "projected text is too large")

        excerpts = (
            payload.gold_excerpts
            if isinstance(payload, JudgePayload)
            else payload.evidence_excerpts
        )
        disclosures: list[DisclosureFact] = []
        transmitted_tokens = 0
        transmitted_bytes = 0
        for excerpt in excerpts:
            token_count = _count(counter, excerpt.quote)
            byte_count = len(excerpt.quote.encode("utf-8"))
            if token_count > snapshot.excerpt.tokens:
                _reject("excerpt_tokens_exceeded", "excerpt token cap exceeded")
            if byte_count > snapshot.excerpt.bytes:
                _reject("excerpt_bytes_exceeded", "excerpt byte cap exceeded")
            fact = DisclosureFact(
                disclosure_id=disclosure_id(
                    excerpt.corpus_manifest_id,
                    excerpt.content_hash,
                    excerpt.quote_hash,
                    excerpt.span,
                ),
                corpus_manifest_id=excerpt.corpus_manifest_id,
                content_hash=excerpt.content_hash,
                quote_hash=excerpt.quote_hash,
                span=excerpt.span,
                token_count=token_count,
                byte_count=byte_count,
            )
            disclosures.append(fact)
            transmitted_tokens += token_count
            transmitted_bytes += byte_count

        toc_nodes = () if isinstance(payload, JudgePayload) else payload.toc_nodes
        if len(toc_nodes) > snapshot.toc_per_call:
            _reject("toc_call_exceeded", "TOC per-call cap exceeded")
        claim_id = (
            payload.atomic_claim_id
            if isinstance(payload, L2AtomicClaimPayload)
            else None
        )
        return _DerivedReservation(
            cap_snapshot=snapshot,
            disclosures=tuple(disclosures),
            transmitted_tokens=transmitted_tokens,
            transmitted_bytes=transmitted_bytes,
            toc_delta=len(toc_nodes),
            atomic_claim_id=claim_id,
        )

    def _stored_source_manifest(
        self, request: EgressRequest
    ) -> SourceManifest | RfcSourceManifest:
        """Resolve the manifest from the store; never authorize on the request copy.

        A caller can build a self-consistent ``SourceManifest`` that says
        ``authorized=True`` and was never written to the store, so content
        addressing alone does not establish that a compliance decision exists.
        """
        try:
            stored = self._manifests.read_source(request.version.source_manifest_id)
        except Exception as error:
            raise EgressPolicyViolation(
                "source_manifest_unresolvable",
                "authorized source manifest could not be read from the store",
            ) from error
        if stored != request.source_manifest:
            _reject(
                "source_manifest_untrusted",
                "request source manifest is not the stored manifest for that ID",
            )
        return stored

    def _authorization_time(self) -> datetime:
        try:
            checked_at = self._clock()
        except Exception as error:
            raise EgressPolicyViolation(
                "authorization_clock_invalid",
                "trusted authorization clock failed",
            ) from error
        if (
            not isinstance(checked_at, datetime)
            or checked_at.tzinfo is None
            or checked_at.utcoffset() is None
        ):
            _reject(
                "authorization_clock_invalid",
                "trusted authorization clock must return an aware datetime",
            )
        return checked_at.astimezone(UTC)


def apply_reservation(
    existing_usage: UsageSnapshot | None,
    existing_corpus_usage: CorpusUsage | None,
    request: ReservationRequest,
    policy: EgressPolicy,
    counter: TokenCounter,
    manifests: SourceManifestResolver,
    *,
    clock: Callable[[], datetime] | None = None,
) -> ReservationOutcome:
    """Apply using server-owned policy, manifest store, counter, and clock."""
    return EgressPolicyEnforcer(
        policy,
        manifests=manifests,
        clock=clock,
    ).apply_reservation(existing_usage, existing_corpus_usage, request, counter)


def _apply_corpus_usage(
    existing: CorpusUsage | None,
    request: ReservationRequest,
    derived: _DerivedReservation,
    *,
    policy_hash: str,
) -> CorpusUsage:
    corpus_manifest_id = request.version.corpus_manifest_id
    if existing is None:
        existing = CorpusUsage(
            corpus_manifest_id=corpus_manifest_id,
            policy_hash=policy_hash,
        )
    if existing.corpus_manifest_id != corpus_manifest_id:
        _reject("corpus_usage_mismatch", "reservation belongs to another corpus")
    if existing.policy_hash != policy_hash:
        _reject("policy_snapshot_mismatch", "policy changed within corpus ledger")

    known = set(existing.disclosure_ids)
    unique_tokens = existing.unique_tokens
    unique_bytes = existing.unique_bytes
    for fact in derived.disclosures:
        if fact.disclosure_id in known:
            continue
        known.add(fact.disclosure_id)
        unique_tokens += fact.token_count
        unique_bytes += fact.byte_count
    disclosure_ids = _ordered_union(
        existing.disclosure_ids,
        tuple(fact.disclosure_id for fact in derived.disclosures),
    )
    _check_unique_cap(
        disclosure_ids,
        unique_tokens,
        unique_bytes,
        derived.cap_snapshot.corpus_unique,
        "corpus_unique",
    )
    return CorpusUsage(
        corpus_manifest_id=corpus_manifest_id,
        policy_hash=policy_hash,
        disclosure_ids=disclosure_ids,
        unique_tokens=unique_tokens,
        unique_bytes=unique_bytes,
        document_usage=_apply_document_usage(existing, request, derived),
    )


def _apply_document_usage(
    existing: CorpusUsage,
    request: ReservationRequest,
    derived: _DerivedReservation,
) -> tuple[CorpusDocumentUsage, ...]:
    """Charge this request's disclosures to the document its version names.

    ``version.document_id`` is safe as the account key because
    ``_validate_version_binding`` has already required it to equal the
    ``document_id`` of the manifest the enforcer resolved from its own store.
    A caller therefore cannot bill one document's excerpts to another's budget
    without failing an earlier check.
    """
    document_id = request.version.document_id
    accounts = {item.document_id: item for item in existing.document_usage}
    current = accounts.get(document_id) or CorpusDocumentUsage(document_id=document_id)

    known = set(current.disclosure_ids)
    unique_tokens = current.unique_tokens
    unique_bytes = current.unique_bytes
    for fact in derived.disclosures:
        if fact.disclosure_id in known:
            continue
        known.add(fact.disclosure_id)
        unique_tokens += fact.token_count
        unique_bytes += fact.byte_count
    disclosure_ids = _ordered_union(
        current.disclosure_ids,
        tuple(fact.disclosure_id for fact in derived.disclosures),
    )
    _check_unique_cap(
        disclosure_ids,
        unique_tokens,
        unique_bytes,
        derived.cap_snapshot.corpus_document_unique,
        "corpus_document_unique",
    )
    accounts[document_id] = CorpusDocumentUsage(
        document_id=document_id,
        disclosure_ids=disclosure_ids,
        unique_tokens=unique_tokens,
        unique_bytes=unique_bytes,
    )
    return tuple(accounts[key] for key in sorted(accounts))


def _apply_usage(
    existing_usage: UsageSnapshot | None,
    request: ReservationRequest,
    derived: _DerivedReservation,
    *,
    policy_hash: str,
) -> UsageSnapshot:
    if existing_usage is None:
        existing_usage = UsageSnapshot(
            evaluation_root_id=request.evaluation_root_id,
            task_level=request.task_level,
            policy_hash=policy_hash,
        )
    if existing_usage.evaluation_root_id != request.evaluation_root_id:
        _reject("evaluation_root_mismatch", "reservation belongs to another root")
    if existing_usage.task_level != request.task_level:
        _reject("task_level_mismatch", "reservation task level changed within root")
    if existing_usage.policy_hash != policy_hash:
        _reject("policy_snapshot_mismatch", "policy changed within evaluation root")

    existing_by_id = {fact.disclosure_id: fact for fact in existing_usage.disclosures}
    request_by_id: dict[str, DisclosureFact] = {}
    for fact in derived.disclosures:
        known = existing_by_id.get(fact.disclosure_id)
        within_request = request_by_id.get(fact.disclosure_id)
        if (known is not None and known != fact) or (
            within_request is not None and within_request != fact
        ):
            _reject(
                "disclosure_fact_mismatch",
                "one disclosure id has inconsistent metadata or sizes",
            )
        request_by_id[fact.disclosure_id] = fact

    merged_facts = list(existing_usage.disclosures)
    for fact in request_by_id.values():
        if fact.disclosure_id not in existing_by_id:
            merged_facts.append(fact)
    merged_by_id = {fact.disclosure_id: fact for fact in merged_facts}

    root_ids = tuple(fact.disclosure_id for fact in merged_facts)
    root_tokens, root_bytes = _unique_sizes(root_ids, merged_by_id)
    _check_unique_cap(
        root_ids,
        root_tokens,
        root_bytes,
        derived.cap_snapshot.root_unique,
        "root_unique",
    )
    root_transmitted_tokens = (
        existing_usage.root_transmitted_tokens + derived.transmitted_tokens
    )
    root_transmitted_bytes = (
        existing_usage.root_transmitted_bytes + derived.transmitted_bytes
    )
    _check_transmitted_cap(
        root_transmitted_tokens,
        root_transmitted_bytes,
        derived.cap_snapshot.root_transmitted,
        "root_transmitted",
    )

    route_usage = _updated_route_usage(
        existing_usage.route_usage,
        request,
        derived,
        merged_by_id,
    )
    stage_usage = _updated_stage_usage(
        existing_usage.stage_usage,
        request,
        derived,
    )

    run_usage = existing_usage.run_usage
    judge_ids = existing_usage.judge_disclosure_ids
    judge_unique_tokens = existing_usage.judge_unique_tokens
    judge_unique_bytes = existing_usage.judge_unique_bytes
    judge_transmitted_tokens = existing_usage.judge_transmitted_tokens
    judge_transmitted_bytes = existing_usage.judge_transmitted_bytes
    if request.stage == EgressStage.JUDGE:
        judge_ids = _ordered_union(
            judge_ids,
            tuple(request_by_id),
        )
        judge_unique_tokens, judge_unique_bytes = _unique_sizes(
            judge_ids,
            merged_by_id,
        )
        _check_unique_cap(
            judge_ids,
            judge_unique_tokens,
            judge_unique_bytes,
            derived.cap_snapshot.judge_unique,
            "judge_unique",
        )
        judge_transmitted_tokens += derived.transmitted_tokens
        judge_transmitted_bytes += derived.transmitted_bytes
        _check_transmitted_cap(
            judge_transmitted_tokens,
            judge_transmitted_bytes,
            derived.cap_snapshot.judge_transmitted,
            "judge_transmitted",
        )
    else:
        run_usage = _updated_online_run_usage(
            run_usage,
            request,
            derived,
            merged_by_id,
        )

    return UsageSnapshot(
        evaluation_root_id=existing_usage.evaluation_root_id,
        task_level=existing_usage.task_level,
        policy_hash=existing_usage.policy_hash,
        disclosures=tuple(merged_facts),
        route_usage=route_usage,
        stage_usage=stage_usage,
        run_usage=run_usage,
        judge_disclosure_ids=judge_ids,
        judge_unique_tokens=judge_unique_tokens,
        judge_unique_bytes=judge_unique_bytes,
        judge_transmitted_tokens=judge_transmitted_tokens,
        judge_transmitted_bytes=judge_transmitted_bytes,
        root_unique_tokens=root_tokens,
        root_unique_bytes=root_bytes,
        root_transmitted_tokens=root_transmitted_tokens,
        root_transmitted_bytes=root_transmitted_bytes,
    )


def _updated_route_usage(
    usages: tuple[RouteUsage, ...],
    request: ReservationRequest,
    derived: _DerivedReservation,
    facts: dict[str, DisclosureFact],
) -> tuple[RouteUsage, ...]:
    request_ids = tuple(fact.disclosure_id for fact in derived.disclosures)
    updated = list(usages)
    for index, usage in enumerate(updated):
        if usage.route == request.route:
            ids = _ordered_union(usage.disclosure_ids, request_ids)
            tokens, byte_count = _unique_sizes(ids, facts)
            updated[index] = usage.model_copy(
                update={
                    "disclosure_ids": ids,
                    "unique_tokens": tokens,
                    "unique_bytes": byte_count,
                }
            )
            break
    else:
        ids = _ordered_union((), request_ids)
        tokens, byte_count = _unique_sizes(ids, facts)
        updated.append(
            RouteUsage(
                route=request.route,
                disclosure_ids=ids,
                unique_tokens=tokens,
                unique_bytes=byte_count,
            )
        )
    return tuple(updated)


def _updated_stage_usage(
    usages: tuple[StageUsage, ...],
    request: ReservationRequest,
    derived: _DerivedReservation,
) -> tuple[StageUsage, ...]:
    updated = list(usages)
    for index, usage in enumerate(updated):
        if usage.stage == request.stage:
            updated[index] = usage.model_copy(
                update={
                    "transmitted_tokens": (
                        usage.transmitted_tokens + derived.transmitted_tokens
                    ),
                    "transmitted_bytes": (
                        usage.transmitted_bytes + derived.transmitted_bytes
                    ),
                    "transmissions": usage.transmissions + 1,
                }
            )
            break
    else:
        updated.append(
            StageUsage(
                stage=request.stage,
                transmitted_tokens=derived.transmitted_tokens,
                transmitted_bytes=derived.transmitted_bytes,
                transmissions=1,
            )
        )
    return tuple(updated)


def _updated_online_run_usage(
    usages: tuple[RunUsage, ...],
    request: ReservationRequest,
    derived: _DerivedReservation,
    facts: dict[str, DisclosureFact],
) -> tuple[RunUsage, ...]:
    updated = list(usages)
    for index, usage in enumerate(updated):
        if usage.run_id == request.run_id:
            updated[index] = _apply_to_run(usage, request, derived, facts)
            break
    else:
        updated.append(
            _apply_to_run(RunUsage(run_id=request.run_id), request, derived, facts)
        )
    return tuple(updated)


def _apply_to_run(
    usage: RunUsage,
    request: ReservationRequest,
    derived: _DerivedReservation,
    facts: dict[str, DisclosureFact],
) -> RunUsage:
    request_ids = tuple(fact.disclosure_id for fact in derived.disclosures)
    claims = usage.claim_usage
    if derived.atomic_claim_id is not None:
        claims = _updated_claim_usage(
            claims,
            derived.atomic_claim_id,
            request_ids,
            facts,
            derived,
        )
        if len(claims) > derived.cap_snapshot.max_claims_per_run:
            _reject("claim_count_exceeded", "too many atomic claims in one run")

    disclosure_ids = _ordered_union(usage.disclosure_ids, request_ids)
    unique_tokens, unique_bytes = _unique_sizes(disclosure_ids, facts)
    online_cap = derived.cap_snapshot.online_unique
    transmitted_cap = derived.cap_snapshot.online_transmitted
    if online_cap is None or transmitted_cap is None:
        _reject("stage_payload_mismatch", "online request lacks online caps")
    _check_unique_cap(
        disclosure_ids,
        unique_tokens,
        unique_bytes,
        online_cap,
        "online_unique",
    )
    transmitted_tokens = usage.transmitted_tokens + derived.transmitted_tokens
    transmitted_bytes = usage.transmitted_bytes + derived.transmitted_bytes
    _check_transmitted_cap(
        transmitted_tokens,
        transmitted_bytes,
        transmitted_cap,
        "online_transmitted",
    )
    toc_nodes = usage.toc_nodes + derived.toc_delta
    if toc_nodes > derived.cap_snapshot.toc_per_run:
        _reject("toc_run_exceeded", "TOC cumulative run cap exceeded")
    return RunUsage(
        run_id=usage.run_id,
        disclosure_ids=disclosure_ids,
        unique_tokens=unique_tokens,
        unique_bytes=unique_bytes,
        transmitted_tokens=transmitted_tokens,
        transmitted_bytes=transmitted_bytes,
        toc_nodes=toc_nodes,
        claim_usage=claims,
    )


def _updated_claim_usage(
    usages: tuple[ClaimUsage, ...],
    claim_id: str,
    request_ids: tuple[str, ...],
    facts: dict[str, DisclosureFact],
    derived: _DerivedReservation,
) -> tuple[ClaimUsage, ...]:
    cap = derived.cap_snapshot.claim_unique
    if cap is None:
        _reject("claim_payload_mismatch", "atomic claim request lacks claim caps")
    updated = list(usages)
    for index, usage in enumerate(updated):
        if usage.atomic_claim_id == claim_id:
            ids = _ordered_union(usage.disclosure_ids, request_ids)
            tokens, byte_count = _unique_sizes(ids, facts)
            _check_unique_cap(ids, tokens, byte_count, cap, "claim_unique")
            updated[index] = usage.model_copy(
                update={
                    "disclosure_ids": ids,
                    "unique_tokens": tokens,
                    "unique_bytes": byte_count,
                }
            )
            break
    else:
        ids = _ordered_union((), request_ids)
        tokens, byte_count = _unique_sizes(ids, facts)
        _check_unique_cap(ids, tokens, byte_count, cap, "claim_unique")
        updated.append(
            ClaimUsage(
                atomic_claim_id=claim_id,
                disclosure_ids=ids,
                unique_tokens=tokens,
                unique_bytes=byte_count,
            )
        )
    return tuple(updated)


def _ordered_union(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    result = list(first)
    seen = set(first)
    for item in second:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return tuple(result)


def _unique_sizes(
    ids: tuple[str, ...],
    facts: dict[str, DisclosureFact],
) -> tuple[int, int]:
    return (
        sum(facts[item].token_count for item in ids),
        sum(facts[item].byte_count for item in ids),
    )


def _check_unique_cap(
    ids: tuple[str, ...],
    tokens: int,
    byte_count: int,
    cap: CapVector,
    prefix: str,
) -> None:
    if len(ids) > cap.excerpts:
        _reject(f"{prefix}_excerpts_exceeded", "unique excerpt cap exceeded")
    if tokens > cap.tokens:
        _reject(f"{prefix}_tokens_exceeded", "unique token cap exceeded")
    if byte_count > cap.bytes:
        _reject(f"{prefix}_bytes_exceeded", "unique byte cap exceeded")


def _check_transmitted_cap(
    tokens: int,
    byte_count: int,
    cap: CapVector,
    prefix: str,
) -> None:
    if tokens > cap.tokens:
        _reject(f"{prefix}_tokens_exceeded", "transmitted token cap exceeded")
    if byte_count > cap.bytes:
        _reject(f"{prefix}_bytes_exceeded", "transmitted byte cap exceeded")


def _payload_task_level(
    payload: L1OnlinePayload | L2DesignPayload | L2AtomicClaimPayload | JudgePayload,
) -> TaskLevel | None:
    if isinstance(payload, L1OnlinePayload):
        return TaskLevel.L1
    if isinstance(payload, JudgePayload):
        return None
    return TaskLevel.L2


def _validate_version_binding(request: EgressRequest) -> None:
    version = request.version
    source = request.source_manifest
    if version.source_manifest_id != source.manifest_id:
        _reject(
            "source_manifest_mismatch",
            "version metadata does not identify the authorized source manifest",
        )
    if version.document_id != source.document_id:
        _reject("document_id_mismatch", "document id does not match source manifest")
    if version.document_version != source.document_version:
        _reject(
            "document_version_mismatch",
            "document version does not match source manifest",
        )
    payload = request.payload
    if not isinstance(payload, JudgePayload) and payload.version != version:
        _reject(
            "payload_version_mismatch",
            "online payload version metadata does not match request metadata",
        )
    excerpts = (
        payload.gold_excerpts
        if isinstance(payload, JudgePayload)
        else payload.evidence_excerpts
    )
    if any(
        item.corpus_manifest_id != version.corpus_manifest_id for item in excerpts
    ):
        _reject(
            "corpus_manifest_mismatch",
            "all excerpts must belong to the request corpus manifest",
        )


def _projected_text(
    payload: L1OnlinePayload | L2DesignPayload | L2AtomicClaimPayload | JudgePayload,
) -> str | None:
    if isinstance(payload, L1OnlinePayload):
        return payload.query
    if isinstance(payload, L2DesignPayload):
        return payload.design_description
    if isinstance(payload, L2AtomicClaimPayload):
        return payload.atomic_claim
    return None


def _verify_counter(counter: TokenCounter, request: EgressRequest) -> None:
    if counter is None:
        raise TokenAccountingUnavailable()
    try:
        provider_id = counter.provider_id
        model_id = counter.model_id
        count = counter.count_tokens
    except (AttributeError, TypeError) as error:
        raise TokenAccountingUnavailable() from error
    if not callable(count):
        raise TokenAccountingUnavailable()
    if provider_id != request.route.provider_id or model_id != request.model_id:
        raise TokenAccountingUnavailable(
            "token counter does not match provider/model route",
            code="token_counter_incompatible",
        )


def _count(counter: TokenCounter, text: str) -> int:
    try:
        count = counter.count_tokens(text)
    except Exception as error:
        raise TokenAccountingUnavailable() from error
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise TokenAccountingUnavailable()
    return count


def _reject(code: str, message: str) -> NoReturn:
    raise EgressPolicyViolation(code, message)
