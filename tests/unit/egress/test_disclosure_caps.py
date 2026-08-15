from __future__ import annotations

import hashlib

import pytest

from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    EvidenceExcerpt,
    JudgePayload,
    L2AtomicClaimPayload,
    L2DesignPayload,
    NormalizedExcerptSpan,
    ScoringPoint,
    TaskLevel,
)
from specpilot.contracts.manifests import ProviderRouteBinding, ProviderUse
from specpilot.egress.enforcer import (
    EgressPolicyViolation,
    ReservationOutcome,
)
from specpilot.egress.enforcer import (
    apply_reservation as apply_with_trusted_inputs,
)
from specpilot.egress.policy import disclosure_id
from tests.unit.egress.test_policy_projection import (
    CORPUS_MANIFEST_ID,
    NOW,
    FixtureTokenCounter,
    authorized_manifest,
    egress_request,
    excerpt,
    fixture_enforcer,
    fixture_policy,
    fixture_store,
    l1_payload,
    online_route,
    version_metadata,
)


def sized_quote(*, tokens: int, byte_count: int | None = None) -> str:
    if byte_count is None:
        return " ".join("x" for _ in range(tokens))
    if byte_count < (tokens * 2 - 1):
        raise ValueError("byte_count cannot fit the requested whitespace tokens")
    word_bytes = byte_count - (tokens - 1)
    first_size = word_bytes - (tokens - 1)
    return " ".join(["x" * first_size, *("x" for _ in range(tokens - 1))])


def apply_reservation(
    previous: ReservationOutcome | None,
    reservation,
    counter=None,
) -> ReservationOutcome:
    return apply_with_trusted_inputs(
        previous.usage if previous else None,
        previous.corpus_usage if previous else None,
        reservation,
        fixture_policy(),
        counter or FixtureTokenCounter(),
        fixture_store(),
        clock=lambda: NOW,
    )


def distinct_excerpt(index: int, quote: str = "bounded evidence") -> EvidenceExcerpt:
    return EvidenceExcerpt(
        corpus_manifest_id=CORPUS_MANIFEST_ID,
        content_hash=f"{(index + 1) % 16:x}" * 64,
        quote=quote,
        quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        span=NormalizedExcerptSpan(
            paragraph_start=index,
            paragraph_end=index,
            token_start=index * 600,
            token_end=index * 600 + len(quote.split()),
        ),
    )


def l2_claim_payload(
    claim_id: str,
    excerpts: tuple[EvidenceExcerpt, ...],
    *,
    toc_count: int = 0,
) -> L2AtomicClaimPayload:
    from specpilot.contracts.egress import TocNode

    return L2AtomicClaimPayload(
        atomic_claim_id=claim_id,
        atomic_claim=f"Atomic claim {claim_id}",
        version=version_metadata(),
        toc_nodes=tuple(
            TocNode(node_id=f"{claim_id}-toc-{i}", title=f"Section {i}")
            for i in range(toc_count)
        ),
        evidence_excerpts=excerpts,
    )


def prepare_for_payload(
    payload: L2AtomicClaimPayload | L2DesignPayload,
    *,
    stage: EgressStage = EgressStage.EVIDENCE,
    run_id: str = "run-1",
):
    request = egress_request(payload=l1_payload())
    request = request.model_copy(
        update={
            "payload": payload.model_copy(update={"version": request.version}),
            "task_level": TaskLevel.L2,
            "stage": stage,
            "run_id": run_id,
        }
    )
    return fixture_enforcer().prepare(request, FixtureTokenCounter())


def judge_route() -> ProviderRouteBinding:
    return ProviderRouteBinding(
        provider_id="provider-a",
        endpoint_purpose="offline-evaluation",
        use=ProviderUse.OFFLINE_JUDGE,
    )


def prepare_judge(
    excerpts: tuple[EvidenceExcerpt, ...],
    *,
    level: TaskLevel = TaskLevel.L2,
):
    route = judge_route()
    payload = JudgePayload(
        query="The question being scored.",
        final_answer="Final bounded answer",
        scoring_points=(ScoringPoint(point_id="p1", text="Correctness"),),
        gold_excerpts=excerpts,
    )
    source = authorized_manifest(route=route)
    request_version = version_metadata(source_manifest_id=source.manifest_id)
    request = EgressRequest(
        evaluation_root_id="case-1",
        run_id="judge-run",
        task_level=level,
        version=request_version,
        stage=EgressStage.JUDGE,
        route=route,
        model_id="fixture-model-v1",
        source_manifest=source,
        payload=payload,
    )
    return fixture_enforcer().prepare(request, FixtureTokenCounter())


def test_disclosure_identity_includes_normalized_span() -> None:
    first = excerpt()
    second = first.model_copy(
        update={
            "span": NormalizedExcerptSpan(
                paragraph_start=4,
                paragraph_end=4,
                token_start=11,
                token_end=13,
            )
        }
    )

    first_id = disclosure_id(
        first.corpus_manifest_id,
        first.content_hash,
        first.quote_hash,
        first.span,
    )
    second_id = disclosure_id(
        second.corpus_manifest_id,
        second.content_hash,
        second.quote_hash,
        second.span,
    )

    assert first_id != second_id
    assert first_id == disclosure_id(
        first.corpus_manifest_id,
        first.content_hash,
        first.quote_hash,
        first.span,
    )


@pytest.mark.parametrize(
    ("quote", "code"),
    [
        # One dimension, not two. The runtime counter is a byte upper
        # bound, so a token count and a byte count are the same
        # measurement and there is no 'one token over' that is not also
        # one byte over. Kept as a single case rather than a duplicate
        # pair that would look like two independent guards.
        (sized_quote(tokens=512, byte_count=8193), "excerpt_bytes_exceeded"),
    ],
)
def test_excerpt_rejects_one_token_or_byte_beyond_individual_cap(
    quote: str,
    code: str,
) -> None:
    payload = l1_payload(evidence_excerpts=(distinct_excerpt(1, quote),))

    with pytest.raises(EgressPolicyViolation) as caught:
        fixture_enforcer().prepare(
            egress_request(payload=payload),
            FixtureTokenCounter(),
        )

    assert caught.value.code == code


def test_duplicate_is_unique_once_but_every_retry_is_transmitted() -> None:
    reservation = fixture_enforcer().prepare(
        egress_request(),
        FixtureTokenCounter(),
    )

    first = apply_reservation(None, reservation)
    second = apply_reservation(first, reservation)

    assert len(second.usage.disclosures) == 1
    assert second.usage.root_unique_tokens == 2
    assert second.usage.root_transmitted_tokens == 4
    assert second.usage.root_transmitted_bytes == 2 * len(b"bounded evidence")
    assert second.usage.stage_usage[0].transmissions == 2
    assert second.usage.route_usage[0].disclosure_ids == (
        reservation.disclosures[0].disclosure_id,
    )


def test_same_disclosure_id_with_inconsistent_size_fails_closed() -> None:
    reservation = fixture_enforcer().prepare(
        egress_request(),
        FixtureTokenCounter(),
    )
    state = apply_reservation(None, reservation)
    altered_fact = reservation.disclosures[0].model_copy(update={"token_count": 3})
    inconsistent = reservation.model_copy(update={"disclosures": (altered_fact,)})

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_reservation(state, inconsistent)

    assert caught.value.code == "reservation_accounting_mismatch"


def test_l1_online_unique_and_transmitted_caps_are_exact() -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    payload = l1_payload(
        evidence_excerpts=tuple(distinct_excerpt(i + 1, quote) for i in range(5))
    )
    reservation = fixture_enforcer().prepare(
        egress_request(payload=payload),
        FixtureTokenCounter(),
    )
    state: ReservationOutcome | None = None
    for _ in range(4):
        state = apply_reservation(state, reservation)

    assert state is not None
    assert state.usage.root_unique_tokens == 2560
    assert state.usage.root_transmitted_tokens == 10240

    with pytest.raises(EgressPolicyViolation) as transmitted:
        apply_reservation(state, reservation)
    assert transmitted.value.code == "online_transmitted_bytes_exceeded"

    extra = fixture_enforcer().prepare(
        egress_request(
            payload=l1_payload(
                evidence_excerpts=(distinct_excerpt(10, "one token"),)
            ),
        ),
        FixtureTokenCounter(),
    )
    unique_state = apply_reservation(None, reservation)
    with pytest.raises(EgressPolicyViolation) as unique:
        apply_reservation(unique_state, extra)
    assert unique.value.code == "online_unique_excerpts_exceeded"


def test_l2_run_and_per_claim_unique_caps_and_claim_count_are_exact() -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    state: ReservationOutcome | None = None
    for claim_index in range(3):
        payload = l2_claim_payload(
            f"claim-{claim_index}",
            tuple(
                distinct_excerpt(claim_index * 4 + i + 1, quote) for i in range(4)
            ),
        )
        state = apply_reservation(
            state,
            prepare_for_payload(payload),
        )

    assert state is not None
    run = state.usage.run_usage[0]
    assert len(run.disclosure_ids) == 12
    assert len(run.claim_usage) == 3
    assert all(len(claim.disclosure_ids) == 4 for claim in run.claim_usage)
    assert all(claim.unique_tokens == 2048 for claim in run.claim_usage)
    assert all(claim.unique_bytes == 32768 for claim in run.claim_usage)

    fifth_for_claim = prepare_for_payload(
        l2_claim_payload("claim-0", (distinct_excerpt(20, quote),)),
    )
    with pytest.raises(EgressPolicyViolation) as per_claim:
        apply_reservation(state, fifth_for_claim)
    assert per_claim.value.code == "claim_unique_excerpts_exceeded"

    fourth_claim = prepare_for_payload(
        l2_claim_payload("claim-3", (distinct_excerpt(1, quote),)),
    )
    with pytest.raises(EgressPolicyViolation) as claim_count:
        apply_reservation(state, fourth_claim)
    assert claim_count.value.code == "claim_count_exceeded"


def test_judge_unique_and_transmitted_caps_are_exact() -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    reservation = prepare_judge(
        tuple(distinct_excerpt(100 + i, quote) for i in range(5)),
    )

    state = apply_reservation(None, reservation)
    state = apply_reservation(state, reservation)

    assert state.usage.judge_unique_tokens == 2560
    assert state.usage.judge_transmitted_tokens == 5120

    with pytest.raises(EgressPolicyViolation) as transmitted:
        apply_reservation(state, reservation)
    assert transmitted.value.code == "judge_transmitted_bytes_exceeded"

    extra = prepare_judge(
        (distinct_excerpt(200, "one token"),),
    )
    with pytest.raises(EgressPolicyViolation) as unique:
        apply_reservation(state, extra)
    assert unique.value.code == "judge_unique_excerpts_exceeded"


def test_toc_is_bounded_cumulatively_per_run() -> None:
    first = prepare_for_payload(
        l2_claim_payload("claim-1", (), toc_count=12),
    )
    second = prepare_for_payload(
        l2_claim_payload("claim-1", (), toc_count=12),
    )
    overflow = prepare_for_payload(
        l2_claim_payload("claim-1", (), toc_count=1),
    )

    state = apply_reservation(None, first)
    state = apply_reservation(state, second)
    assert state.usage.run_usage[0].toc_nodes == 24

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_reservation(state, overflow)
    assert caught.value.code == "toc_run_exceeded"


def test_judge_payload_cannot_be_sent_to_online_stage_or_route() -> None:
    reservation = prepare_judge(())
    invalid = reservation.model_copy(
        update={"stage": EgressStage.EVIDENCE, "route": online_route()}
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_reservation(None, invalid)

    assert caught.value.code == "stage_payload_mismatch"
