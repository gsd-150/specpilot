from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import (
    EgressStage,
    L1OnlinePayload,
    TaskLevel,
    TocNode,
)
from specpilot.contracts.manifests import ProviderRouteBinding, ProviderUse
from specpilot.egress.enforcer import (
    EgressPolicyEnforcer,
    EgressPolicyViolation,
    UsageSnapshot,
    apply_reservation,
)
from specpilot.egress.policy import EgressPolicy
from tests.unit.egress.test_disclosure_caps import (
    distinct_excerpt,
    l2_claim_payload,
    prepare_for_payload,
    prepare_judge,
    sized_quote,
)
from tests.unit.egress.test_policy_projection import (
    FixtureTokenCounter,
    egress_request,
    l1_payload,
)


def maximum_l2_state(tmp_path: Path) -> UsageSnapshot:
    quote = sized_quote(tokens=512, byte_count=8192)
    state: UsageSnapshot | None = None
    stages = (
        EgressStage.EVIDENCE,
        EgressStage.COMPLIANCE,
        EgressStage.VERIFIER,
        EgressStage.VERIFIER,
    )
    for claim_index in range(3):
        claim_excerpts = tuple(
            distinct_excerpt(claim_index * 4 + excerpt_index + 1, quote)
            for excerpt_index in range(4)
        )
        payload = l2_claim_payload(
            f"claim-{claim_index}",
            claim_excerpts,
            toc_count=2,
        )
        for call_index, stage in enumerate(stages):
            reservation = prepare_for_payload(
                tmp_path / f"online-{claim_index}-{call_index}",
                payload,
                stage=stage,
            )
            state = apply_reservation(state, reservation)

    judge = prepare_judge(
        tmp_path / "judge",
        tuple(distinct_excerpt(100 + index, quote) for index in range(5)),
        level=TaskLevel.L2,
    )
    state = apply_reservation(state, judge)
    state = apply_reservation(state, judge)
    return state


def test_exact_maximum_l2_evaluation_envelope_is_accepted(tmp_path: Path) -> None:
    state = maximum_l2_state(tmp_path)

    assert len(state.disclosures) == 17
    assert state.root_unique_tokens == 8_704
    assert state.root_unique_bytes == 139_264
    assert state.root_transmitted_tokens == 29_696
    assert state.root_transmitted_bytes == 475_136
    assert state.run_usage[0].transmitted_tokens == 24_576
    assert state.run_usage[0].transmitted_bytes == 393_216
    assert state.run_usage[0].toc_nodes == 24
    assert state.judge_transmitted_tokens == 5_120
    assert state.judge_transmitted_bytes == 81_920
    assert {(item.stage, item.transmissions) for item in state.stage_usage} == {
        (EgressStage.EVIDENCE, 3),
        (EgressStage.COMPLIANCE, 3),
        (EgressStage.VERIFIER, 6),
        (EgressStage.JUDGE, 2),
    }


def test_maximum_envelope_rejects_an_extra_excerpt_or_toc_node(
    tmp_path: Path,
) -> None:
    state = maximum_l2_state(tmp_path / "base")
    extra_excerpt = prepare_for_payload(
        tmp_path / "extra-excerpt",
        l2_claim_payload(
            "claim-0",
            (distinct_excerpt(250, "extra"),),
        ),
    )
    with pytest.raises(EgressPolicyViolation) as excerpt_error:
        apply_reservation(state, extra_excerpt)
    assert excerpt_error.value.code == "root_unique_excerpts_exceeded"

    extra_toc = prepare_for_payload(
        tmp_path / "extra-toc",
        l2_claim_payload("claim-0", (), toc_count=1),
    )
    with pytest.raises(EgressPolicyViolation) as toc_error:
        apply_reservation(state, extra_toc)
    assert toc_error.value.code == "toc_run_exceeded"


@pytest.mark.parametrize(
    ("quote", "code"),
    [
        (sized_quote(tokens=513), "excerpt_tokens_exceeded"),
        (sized_quote(tokens=512, byte_count=8193), "excerpt_bytes_exceeded"),
    ],
)
def test_maximum_component_rejects_one_extra_token_or_byte(
    tmp_path: Path,
    quote: str,
    code: str,
) -> None:
    payload = l2_claim_payload("claim-0", (distinct_excerpt(1, quote),))

    with pytest.raises(EgressPolicyViolation) as caught:
        prepare_for_payload(tmp_path, payload)

    assert caught.value.code == code


def test_payload_rejects_one_extra_field_with_stable_validation_code() -> None:
    fields = l1_payload().model_dump()
    fields["source_path"] = "/private/corpus/full.docx"

    with pytest.raises(ValidationError) as caught:
        L1OnlinePayload.model_validate(fields)

    assert caught.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("field", "increment"),
    [("transmitted_tokens", 1), ("transmitted_bytes", 1)],
)
def test_prepared_reservation_rejects_altered_transmission_delta(
    tmp_path: Path,
    field: str,
    increment: int,
) -> None:
    reservation = EgressPolicyEnforcer().prepare(
        egress_request(tmp_path),
        FixtureTokenCounter(),
    )
    altered = reservation.model_copy(
        update={field: getattr(reservation, field) + increment}
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_reservation(None, altered)

    assert caught.value.code == "reservation_accounting_mismatch"


def test_cross_provider_resend_is_one_root_unique_and_two_transmissions(
    tmp_path: Path,
) -> None:
    first_request = egress_request(tmp_path / "first")
    first = EgressPolicyEnforcer().prepare(first_request, FixtureTokenCounter())
    second_route = ProviderRouteBinding(
        provider_id="provider-b",
        endpoint_purpose="evidence-review",
        use=ProviderUse.ONLINE_MAIN,
    )

    class ProviderBCounter(FixtureTokenCounter):
        provider_id = "provider-b"

    second_request = egress_request(tmp_path / "second", route=second_route)
    second = EgressPolicyEnforcer().prepare(second_request, ProviderBCounter())

    state = apply_reservation(None, first)
    state = apply_reservation(state, second)

    assert len(state.disclosures) == 1
    assert len(state.route_usage) == 2
    assert all(len(route.disclosure_ids) == 1 for route in state.route_usage)
    assert all(route.unique_tokens == 2 for route in state.route_usage)
    assert all(
        route.unique_bytes == len(b"bounded evidence")
        for route in state.route_usage
    )
    assert state.root_unique_tokens == 2
    assert state.root_transmitted_tokens == 4


def test_policy_loading_and_hashing_are_semantic_and_deterministic(
    tmp_path: Path,
) -> None:
    policy = EgressPolicy.load()
    reordered = tmp_path / "reordered.yaml"
    source = policy.model_dump_json(indent=2)
    reordered.write_text(source, encoding="utf-8")

    reloaded = EgressPolicy.load(reordered)

    assert reloaded == policy
    assert reloaded.policy_hash == policy.policy_hash
    assert policy.l2_root_transmitted.tokens == 29_696
    assert policy.l2_root_transmitted.bytes == 475_136


def test_toc_call_rejects_thirteenth_node_before_reservation() -> None:
    with pytest.raises(ValidationError):
        l1_payload(
            toc_nodes=tuple(
                TocNode(node_id=f"node-{index}", title=f"Node {index}")
                for index in range(13)
            )
        )
