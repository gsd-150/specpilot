"""Comparison B (plan §8.5.3): the gate-only pair, pure and offline.

No provider, no ledger, no corpus: the pair is a projection of one persisted
pre-verifier candidate through two arms, and every property here is a fact
about that projection.
"""

from __future__ import annotations

import json

from specpilot.verifier.gate_only import (
    PreVerifierCandidate,
    build_pre_verifier_artifact,
    compare_gate_only,
    deterministic_reason,
    pre_verifier_artifact_hash,
)


def candidate(
    claim_id: str = "a" * 64,
    *,
    verdict: str = "compliant",
    evidence: tuple[str, ...] = ("e" * 64,),
) -> PreVerifierCandidate:
    return PreVerifierCandidate(
        claim_id=claim_id,
        claim="A sender always emits the field.",
        proposed_verdict=verdict,
        evidence_ids=evidence,
        rationale="The shown excerpt requires it.",
    )


def test_the_artifact_hash_is_canonical_across_reordering() -> None:
    first = PreVerifierCandidate(
        claim_id="a" * 64,
        claim="x",
        proposed_verdict="compliant",
        evidence_ids=("e2" + "0" * 62, "e1" + "0" * 62),
        rationale="r",
    )
    second = PreVerifierCandidate(
        claim_id="a" * 64,
        claim="x",
        proposed_verdict="compliant",
        evidence_ids=("e1" + "0" * 62, "e2" + "0" * 62),
        rationale="r",
    )

    assert pre_verifier_artifact_hash([first]) == pre_verifier_artifact_hash(
        [second]
    )


def test_the_artifact_hash_moves_with_content() -> None:
    base = candidate()

    edited = PreVerifierCandidate(
        claim_id=base.claim_id,
        claim=base.claim + "?",
        proposed_verdict=base.proposed_verdict,
        evidence_ids=base.evidence_ids,
        rationale=base.rationale,
    )

    assert pre_verifier_artifact_hash([base]) != pre_verifier_artifact_hash(
        [edited]
    )


def test_the_off_arm_keeps_the_proposed_verdict_when_supported() -> None:
    item = candidate()
    artifact_hash = pre_verifier_artifact_hash([item])

    result = compare_gate_only(
        item,
        artifact_hash,
        deterministic_passed=True,
        exclusion_reason=None,
        semantic_supports=True,
    )

    assert result.excluded is False
    assert result.off_verdict == "compliant"
    assert result.on_verdict == "compliant"
    assert result.downgraded is False
    assert result.artifact_hash == artifact_hash


def test_the_on_arm_downgrades_when_the_gate_refuses() -> None:
    item = candidate(verdict="violating")
    artifact_hash = pre_verifier_artifact_hash([item])

    result = compare_gate_only(
        item,
        artifact_hash,
        deterministic_passed=True,
        exclusion_reason=None,
        semantic_supports=False,
    )

    assert result.excluded is False
    assert result.off_verdict == "violating"
    assert result.on_verdict == "insufficient_evidence"
    assert result.downgraded is True


def test_a_deterministic_failure_excludes_the_pair_not_one_arm() -> None:
    item = candidate()
    artifact_hash = pre_verifier_artifact_hash([item])

    result = compare_gate_only(
        item,
        artifact_hash,
        deterministic_passed=False,
        exclusion_reason="not_disclosed",
        semantic_supports=True,
    )

    assert result.excluded is True
    assert result.exclusion_reason == "not_disclosed"
    assert result.off_verdict is None
    assert result.on_verdict is None
    assert result.downgraded is False


def test_both_arms_read_the_same_hash_from_the_built_artifact() -> None:
    item = candidate()
    artifact = build_pre_verifier_artifact(
        "l2-dev-001", [item], [("a" * 64, True, None)]
    )

    recorded = artifact["artifact_hash"]
    reread = pre_verifier_artifact_hash([item])

    assert recorded == reread


def test_the_built_artifact_projects_deterministic_state_beside_the_hash() -> None:
    item = candidate(claim_id="c" * 64)
    artifact = build_pre_verifier_artifact(
        "l2-dev-001",
        [item],
        [("c" * 64, False, "not_disclosed")],
    )

    projected = artifact["candidates"][0]
    assert projected["deterministic_passed"] is False
    assert projected["deterministic_faults"] == ["not_disclosed"]
    assert projected["evidence_ids"] == list(item.evidence_ids)
    # The hash covers the proposal, not the deterministic outcome beside it.
    assert artifact["artifact_hash"] == pre_verifier_artifact_hash([item])


def test_deterministic_reason_reads_the_closed_status_set() -> None:
    assert deterministic_reason("deterministic_failed") == "deterministic_failed"
    assert deterministic_reason("verified") is None
    assert deterministic_reason("semantic_failed") is None
    assert deterministic_reason("insufficient") is None


def test_the_artifact_schema_version_is_named() -> None:
    artifact = build_pre_verifier_artifact("l2-dev-001", [candidate()], [])

    assert artifact["schema_version"] == "l2-pre-verifier/v1"
    # The artifact is JSON-serializable as built.
    json.dumps(artifact, ensure_ascii=False)


def test_insufficient_proposals_are_not_downgrades_on_either_arm() -> None:
    item = candidate(verdict="insufficient_evidence", evidence=())
    artifact_hash = pre_verifier_artifact_hash([item])

    result = compare_gate_only(
        item,
        artifact_hash,
        deterministic_passed=True,
        exclusion_reason=None,
        semantic_supports=True,
    )

    assert result.off_verdict == "insufficient_evidence"
    assert result.on_verdict == "insufficient_evidence"
    assert result.downgraded is False
