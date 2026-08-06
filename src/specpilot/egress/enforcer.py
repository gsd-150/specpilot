from __future__ import annotations

from typing import NoReturn

from specpilot.contracts.egress import (
    CapVector,
    ClaimUsage,
    DisclosureFact,
    EgressRequest,
    EgressStage,
    JudgePayload,
    L1OnlinePayload,
    L2AtomicClaimPayload,
    L2DesignPayload,
    ReservationRequest,
    RouteUsage,
    RunUsage,
    StageUsage,
    TaskLevel,
    TokenCounter,
    UsageSnapshot,
)
from specpilot.contracts.manifests import ProviderUse
from specpilot.egress.policy import (
    EgressPolicy,
    cap_snapshot_hash,
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


class EgressPolicyEnforcer:
    def __init__(self, policy: EgressPolicy | None = None) -> None:
        self._policy = policy or EgressPolicy.load()

    def prepare(
        self,
        request: EgressRequest,
        counter: TokenCounter,
    ) -> ReservationRequest:
        payload = request.payload
        inferred_level = _payload_task_level(payload)
        if inferred_level is not None and inferred_level != request.task_level:
            _reject("task_level_mismatch", "task level does not match payload")
        task_level = request.task_level
        expected_use = (
            ProviderUse.OFFLINE_JUDGE
            if isinstance(payload, JudgePayload)
            else ProviderUse.ONLINE_MAIN
        )
        if request.route.use != expected_use:
            _reject("stage_route_mismatch", "payload does not match route use")
        allowed = self._policy.stage_payload_allowlist.get(request.stage.value)
        if allowed is None or payload.kind not in allowed:
            _reject("stage_payload_mismatch", "stage does not allow payload kind")
        if not request.source_manifest.authorizes(
            request.route,
            at=request.requested_at,
        ):
            _reject("route_unauthorized", "source manifest does not authorize route")
        _verify_counter(counter, request)

        snapshot = self._policy.snapshot(
            task_level=task_level.value,
            payload_kind=payload.kind,
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
        return ReservationRequest(
            policy_version=self._policy.schema_version,
            policy_hash=self._policy.policy_hash,
            cap_snapshot=snapshot,
            cap_snapshot_hash=cap_snapshot_hash(snapshot),
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            task_level=task_level,
            stage=request.stage,
            route=request.route,
            model_id=request.model_id,
            atomic_claim_id=claim_id,
            projected_payload=payload,
            disclosures=tuple(disclosures),
            transmitted_tokens=transmitted_tokens,
            transmitted_bytes=transmitted_bytes,
            toc_delta=len(toc_nodes),
        )


def apply_reservation(
    existing_usage: UsageSnapshot | None,
    request: ReservationRequest,
) -> UsageSnapshot:
    """Purely validate and apply one prepared reservation to immutable usage."""
    _verify_prepared_request(request)
    if existing_usage is None:
        existing_usage = UsageSnapshot(
            evaluation_root_id=request.evaluation_root_id,
            task_level=request.task_level,
            policy_hash=request.policy_hash,
        )
    if existing_usage.evaluation_root_id != request.evaluation_root_id:
        _reject("evaluation_root_mismatch", "reservation belongs to another root")
    if existing_usage.task_level != request.task_level:
        _reject("task_level_mismatch", "reservation task level changed within root")
    if existing_usage.policy_hash != request.policy_hash:
        _reject("policy_snapshot_mismatch", "policy changed within evaluation root")

    existing_by_id = {fact.disclosure_id: fact for fact in existing_usage.disclosures}
    request_by_id: dict[str, DisclosureFact] = {}
    for fact in request.disclosures:
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
        request.cap_snapshot.root_unique,
        "root_unique",
    )
    root_transmitted_tokens = (
        existing_usage.root_transmitted_tokens + request.transmitted_tokens
    )
    root_transmitted_bytes = (
        existing_usage.root_transmitted_bytes + request.transmitted_bytes
    )
    _check_transmitted_cap(
        root_transmitted_tokens,
        root_transmitted_bytes,
        request.cap_snapshot.root_transmitted,
        "root_transmitted",
    )

    route_usage = _updated_route_usage(
        existing_usage.route_usage,
        request,
        merged_by_id,
    )
    stage_usage = _updated_stage_usage(
        existing_usage.stage_usage,
        request,
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
            request.cap_snapshot.judge_unique,
            "judge_unique",
        )
        judge_transmitted_tokens += request.transmitted_tokens
        judge_transmitted_bytes += request.transmitted_bytes
        _check_transmitted_cap(
            judge_transmitted_tokens,
            judge_transmitted_bytes,
            request.cap_snapshot.judge_transmitted,
            "judge_transmitted",
        )
    else:
        run_usage = _updated_online_run_usage(
            run_usage,
            request,
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


def _verify_prepared_request(request: ReservationRequest) -> None:
    if cap_snapshot_hash(request.cap_snapshot) != request.cap_snapshot_hash:
        _reject("cap_snapshot_hash_mismatch", "cap snapshot hash is invalid")
    is_judge_payload = isinstance(request.projected_payload, JudgePayload)
    if is_judge_payload != (request.stage == EgressStage.JUDGE):
        _reject("stage_payload_mismatch", "stage and projected payload disagree")
    expected_use = (
        ProviderUse.OFFLINE_JUDGE if is_judge_payload else ProviderUse.ONLINE_MAIN
    )
    if request.route.use != expected_use:
        _reject("stage_route_mismatch", "stage and route use disagree")
    if request.atomic_claim_id is not None and not isinstance(
        request.projected_payload,
        L2AtomicClaimPayload,
    ):
        _reject("claim_payload_mismatch", "claim id requires atomic claim payload")
    if isinstance(request.projected_payload, L2AtomicClaimPayload) and (
        request.atomic_claim_id != request.projected_payload.atomic_claim_id
    ):
        _reject("claim_payload_mismatch", "claim id does not match payload")
    expected_level = _payload_task_level(request.projected_payload)
    if expected_level is not None and request.task_level != expected_level:
        _reject("task_level_mismatch", "task level does not match payload")
    if isinstance(request.projected_payload, JudgePayload):
        payload_excerpts = request.projected_payload.gold_excerpts
        payload_toc_count = 0
    else:
        payload_excerpts = request.projected_payload.evidence_excerpts
        payload_toc_count = len(request.projected_payload.toc_nodes)
    if request.toc_delta != payload_toc_count:
        _reject("reservation_accounting_mismatch", "TOC delta is inconsistent")
    if len(payload_excerpts) != len(request.disclosures):
        _reject(
            "reservation_accounting_mismatch",
            "disclosure facts do not match projected excerpts",
        )
    for excerpt, fact in zip(payload_excerpts, request.disclosures, strict=True):
        expected_id = disclosure_id(
            excerpt.corpus_manifest_id,
            excerpt.content_hash,
            excerpt.quote_hash,
            excerpt.span,
        )
        if (
            fact.disclosure_id != expected_id
            or fact.corpus_manifest_id != excerpt.corpus_manifest_id
            or fact.content_hash != excerpt.content_hash
            or fact.quote_hash != excerpt.quote_hash
            or fact.span != excerpt.span
            or fact.byte_count != len(excerpt.quote.encode("utf-8"))
        ):
            _reject(
                "reservation_accounting_mismatch",
                "disclosure fact is inconsistent with projected excerpt",
            )
    if request.transmitted_tokens != sum(
        fact.token_count for fact in request.disclosures
    ) or request.transmitted_bytes != sum(
        fact.byte_count for fact in request.disclosures
    ):
        _reject(
            "reservation_accounting_mismatch",
            "transmitted totals do not match disclosure facts",
        )


def _updated_route_usage(
    usages: tuple[RouteUsage, ...],
    request: ReservationRequest,
    facts: dict[str, DisclosureFact],
) -> tuple[RouteUsage, ...]:
    request_ids = tuple(fact.disclosure_id for fact in request.disclosures)
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
) -> tuple[StageUsage, ...]:
    updated = list(usages)
    for index, usage in enumerate(updated):
        if usage.stage == request.stage:
            updated[index] = usage.model_copy(
                update={
                    "transmitted_tokens": (
                        usage.transmitted_tokens + request.transmitted_tokens
                    ),
                    "transmitted_bytes": (
                        usage.transmitted_bytes + request.transmitted_bytes
                    ),
                    "transmissions": usage.transmissions + 1,
                }
            )
            break
    else:
        updated.append(
            StageUsage(
                stage=request.stage,
                transmitted_tokens=request.transmitted_tokens,
                transmitted_bytes=request.transmitted_bytes,
                transmissions=1,
            )
        )
    return tuple(updated)


def _updated_online_run_usage(
    usages: tuple[RunUsage, ...],
    request: ReservationRequest,
    facts: dict[str, DisclosureFact],
) -> tuple[RunUsage, ...]:
    updated = list(usages)
    for index, usage in enumerate(updated):
        if usage.run_id == request.run_id:
            updated[index] = _apply_to_run(usage, request, facts)
            break
    else:
        updated.append(_apply_to_run(RunUsage(run_id=request.run_id), request, facts))
    return tuple(updated)


def _apply_to_run(
    usage: RunUsage,
    request: ReservationRequest,
    facts: dict[str, DisclosureFact],
) -> RunUsage:
    request_ids = tuple(fact.disclosure_id for fact in request.disclosures)
    claims = usage.claim_usage
    if request.atomic_claim_id is not None:
        claims = _updated_claim_usage(
            claims,
            request.atomic_claim_id,
            request_ids,
            facts,
            request,
        )
        if len(claims) > request.cap_snapshot.max_claims_per_run:
            _reject("claim_count_exceeded", "too many atomic claims in one run")

    disclosure_ids = _ordered_union(usage.disclosure_ids, request_ids)
    unique_tokens, unique_bytes = _unique_sizes(disclosure_ids, facts)
    online_cap = request.cap_snapshot.online_unique
    transmitted_cap = request.cap_snapshot.online_transmitted
    if online_cap is None or transmitted_cap is None:
        _reject("stage_payload_mismatch", "online request lacks online caps")
    _check_unique_cap(
        disclosure_ids,
        unique_tokens,
        unique_bytes,
        online_cap,
        "online_unique",
    )
    transmitted_tokens = usage.transmitted_tokens + request.transmitted_tokens
    transmitted_bytes = usage.transmitted_bytes + request.transmitted_bytes
    _check_transmitted_cap(
        transmitted_tokens,
        transmitted_bytes,
        transmitted_cap,
        "online_transmitted",
    )
    toc_nodes = usage.toc_nodes + request.toc_delta
    if toc_nodes > request.cap_snapshot.toc_per_run:
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
    request: ReservationRequest,
) -> tuple[ClaimUsage, ...]:
    cap = request.cap_snapshot.claim_unique
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
