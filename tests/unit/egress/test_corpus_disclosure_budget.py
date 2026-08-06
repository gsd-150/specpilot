from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.contracts.egress import CorpusUsage, ReservationOutcome
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.egress.enforcer import apply_reservation as apply_with_trusted_inputs
from specpilot.egress.policy import EgressPolicy
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_policy_projection import (
    NOW,
    FixtureTokenCounter,
    egress_request,
    fixture_enforcer,
    fixture_store,
    l1_payload,
)


def prepare_in_case(case_id: str, *excerpts):
    """One Evidence call in its own evaluation root, i.e. its own test case."""
    request = egress_request(payload=l1_payload(evidence_excerpts=excerpts))
    return fixture_enforcer().prepare(
        request.model_copy(update={"evaluation_root_id": case_id}),
        FixtureTokenCounter(),
    )


def apply_across_cases(
    corpus_usage: CorpusUsage | None,
    reservation,
    policy: EgressPolicy | None = None,
) -> ReservationOutcome:
    """Start a fresh per-case usage row but carry the corpus ledger forward.

    This is what the PostgreSQL ledger will do: the usage row is keyed by
    ``evaluation_root_id`` and starts empty for each case, while the corpus row
    is keyed by ``corpus_manifest_id`` and spans every case.
    """
    return apply_with_trusted_inputs(
        None,
        corpus_usage,
        reservation,
        policy or EgressPolicy.load(),
        FixtureTokenCounter(),
        fixture_store(),
        clock=lambda: NOW,
    )


def narrow_corpus_policy(tmp_path: Path, *, excerpts: int) -> EgressPolicy:
    policy = EgressPolicy.load()
    fields = policy.model_dump(mode="json")
    fields["corpus_unique"] = {
        "excerpts": excerpts,
        "tokens": excerpts * 512,
        "bytes": excerpts * 8192,
    }
    path = tmp_path / "narrow-corpus.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return EgressPolicy.load(path)


def test_the_same_excerpt_reused_across_cases_is_one_corpus_disclosure() -> None:
    shared = distinct_excerpt(1)

    first = apply_across_cases(None, prepare_in_case("case-1", shared))
    second = apply_across_cases(first.corpus_usage, prepare_in_case("case-2", shared))

    assert first.usage.evaluation_root_id == "case-1"
    assert second.usage.evaluation_root_id == "case-2"
    assert second.usage.root_unique_tokens == 2, "per-case rows stay independent"
    assert len(second.corpus_usage.disclosure_ids) == 1
    assert second.corpus_usage.unique_tokens == 2


def test_distinct_excerpts_accumulate_across_cases_above_the_root_scope() -> None:
    outcome = None
    corpus: CorpusUsage | None = None
    for index in range(6):
        outcome = apply_across_cases(
            corpus,
            prepare_in_case(f"case-{index}", distinct_excerpt(index + 1)),
        )
        corpus = outcome.corpus_usage

    assert outcome is not None
    assert outcome.usage.root_unique_tokens == 2, "each case sees only its own excerpt"
    assert len(outcome.corpus_usage.disclosure_ids) == 6
    assert outcome.corpus_usage.unique_tokens == 12


def test_corpus_cap_stops_an_evaluation_that_every_per_case_cap_would_allow(
    tmp_path: Path,
) -> None:
    policy = narrow_corpus_policy(tmp_path, excerpts=3)
    corpus: CorpusUsage | None = None
    for index in range(3):
        corpus = apply_across_cases(
            corpus,
            prepare_in_case(f"case-{index}", distinct_excerpt(index + 1)),
            policy,
        ).corpus_usage

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_across_cases(
            corpus,
            prepare_in_case("case-3", distinct_excerpt(4)),
            policy,
        )

    assert caught.value.code == "corpus_unique_excerpts_exceeded"


def test_corpus_token_cap_is_enforced_independently_of_the_excerpt_count(
    tmp_path: Path,
) -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    policy = narrow_corpus_policy(tmp_path, excerpts=2)
    fields = policy.model_dump(mode="json")
    fields["corpus_unique"]["tokens"] = 512
    path = tmp_path / "token-capped.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    token_capped = EgressPolicy.load(path)

    corpus = apply_across_cases(
        None,
        prepare_in_case("case-0", distinct_excerpt(1, quote)),
        token_capped,
    ).corpus_usage

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_across_cases(
            corpus,
            prepare_in_case("case-1", distinct_excerpt(2, quote)),
            token_capped,
        )

    assert caught.value.code == "corpus_unique_tokens_exceeded"


def test_corpus_ledger_belongs_to_exactly_one_corpus_manifest() -> None:
    outcome = apply_across_cases(None, prepare_in_case("case-1", distinct_excerpt(1)))
    foreign = outcome.corpus_usage.model_copy(update={"corpus_manifest_id": "e" * 64})

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_across_cases(foreign, prepare_in_case("case-2", distinct_excerpt(2)))

    assert caught.value.code == "corpus_usage_mismatch"


def test_corpus_ledger_is_pinned_to_one_policy_snapshot(tmp_path: Path) -> None:
    outcome = apply_across_cases(None, prepare_in_case("case-1", distinct_excerpt(1)))

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_across_cases(
            outcome.corpus_usage,
            prepare_in_case("case-2", distinct_excerpt(2)),
            narrow_corpus_policy(tmp_path, excerpts=8),
        )

    assert caught.value.code == "policy_snapshot_mismatch"


def test_judge_gold_excerpts_also_consume_the_corpus_budget() -> None:
    from tests.unit.egress.test_disclosure_caps import prepare_judge

    shared = distinct_excerpt(1)
    online = apply_across_cases(None, prepare_in_case("case-1", shared))
    with_judge = apply_across_cases(
        online.corpus_usage,
        prepare_judge((shared, distinct_excerpt(2))),
    )

    assert len(with_judge.corpus_usage.disclosure_ids) == 2, (
        "gold excerpts leave the machine too, and the shared one must not recount"
    )
