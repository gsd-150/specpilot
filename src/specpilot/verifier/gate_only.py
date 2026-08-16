"""Comparison B (plan §8.5.3): the semantic gate measured by its removal.

The 'on' arm is the frozen chain: deterministic checks first, then the semantic
support gate, which downgrades a candidate to 'insufficient_evidence' when the
gate finds the shown excerpts do not support the proposed verdict. The 'off'
arm runs the same deterministic checks -- citation existence, manifest and
content-hash identity, document applicability -- and then keeps the candidate's
proposed verdict. The gate is the only difference between the arms.

Both arms consume one pre-verifier artifact -- claim, proposed verdict,
evidence ids, rationale, hashed -- and neither re-retrieves. The hash binds the
pair: two arms reading the same artifact is a fact the report can state rather
than a discipline somebody remembered.

REPORTING CONSTRAINT, kept here because the report writer will be reading this
code: the 'off' arm's L2-adv result is close to construction-determined. The
adversarial negative subset is built so citations exist, versions match, and
semantics do not support -- the deterministic checks pass by design, so 'off'
necessarily returns determinate verdicts on it. The measured quantity is the
'on' arm's semantic accuracy, which §8.1.1's direct-fed matched pairs already
measure under hand-picked distractors; this comparison adds the end-to-end
reading with real retrieved evidence. The report merges the three into one
narrative, never three selling points.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from specpilot.contracts.verdict import ComplianceVerdict, VerificationStatus

PRE_VERIFIER_SCHEMA_VERSION = "l2-pre-verifier/v1"
_DOWNGRADE_VERDICT = ComplianceVerdict.INSUFFICIENT_EVIDENCE.value
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class PreVerifierCandidate:
    """One candidate exactly as it stood before the semantic gate ran."""

    claim_id: str
    claim: str
    proposed_verdict: str
    evidence_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class GateOnlyResult:
    """One claim seen through both arms, joined by the artifact hash."""

    artifact_hash: str
    claim_id: str
    excluded: bool
    exclusion_reason: str | None
    off_verdict: str | None
    on_verdict: str | None
    downgraded: bool


def pre_verifier_artifact_hash(
    candidates: Sequence[PreVerifierCandidate],
) -> str:
    """Canonical digest of the whole pre-verifier candidate block.

    Field order is fixed, candidates are sorted by claim id and evidence ids
    are sorted, so two serializations of the same content agree and a reordered
    or edited copy does not. The digest is what the pair's arms both name when
    they claim to have read the same artifact.
    """
    block = [
        {
            "claim_id": item.claim_id,
            "claim": item.claim,
            "proposed_verdict": item.proposed_verdict,
            "evidence_ids": sorted(item.evidence_ids),
            "rationale": item.rationale,
        }
        for item in sorted(candidates, key=lambda c: c.claim_id)
    ]
    encoded = json.dumps(
        block, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare_gate_only(
    candidate: PreVerifierCandidate,
    artifact_hash: str,
    *,
    deterministic_passed: bool,
    exclusion_reason: str | None,
    semantic_supports: bool,
) -> GateOnlyResult:
    """Run one candidate through both arms.

    A candidate whose deterministic checks failed is excluded from the pair
    and reported separately with its reason -- the 'off' arm is not a licence
    to keep a verdict the local identity checks already killed, and neither
    arm may silently absorb it. For a passed candidate the 'off' arm keeps the
    proposed verdict and the 'on' arm keeps it only when the semantic gate
    supports it; 'downgraded' is the gate's measured effect on this claim.
    """
    if not deterministic_passed or exclusion_reason is not None:
        return GateOnlyResult(
            artifact_hash=artifact_hash,
            claim_id=candidate.claim_id,
            excluded=True,
            exclusion_reason=exclusion_reason or "deterministic_failed",
            off_verdict=None,
            on_verdict=None,
            downgraded=False,
        )
    off = candidate.proposed_verdict
    on = candidate.proposed_verdict if semantic_supports else _DOWNGRADE_VERDICT
    return GateOnlyResult(
        artifact_hash=artifact_hash,
        claim_id=candidate.claim_id,
        excluded=False,
        exclusion_reason=None,
        off_verdict=off,
        on_verdict=on,
        downgraded=off != on,
    )


def build_pre_verifier_artifact(
    case_id: str,
    candidates: Sequence[PreVerifierCandidate],
    deterministic: Sequence[tuple[str, bool, str | None]],
) -> dict[str, object]:
    """Project the pre-verifier state into its artifact contract.

    'deterministic' carries, per claim id, whether the local identity checks
    passed and the joined fault codes when they did not. The artifact is what
    both arms of the comparison read; its hash is computed over the candidate
    block only, so a deterministic outcome recorded beside it cannot move the
    identity of what the model proposed.
    """
    by_claim: dict[str, tuple[bool, str | None]] = {}
    for claim_id, passed, reason in deterministic:
        # Last phase wins: the chain can re-verify a claim after recovery, and
        # the pre-verifier state that fed the semantic call is the final one.
        by_claim[claim_id] = (passed, reason)
    projected = []
    for item in candidates:
        passed, reason = by_claim.get(item.claim_id, (True, None))
        projected.append(
            {
                "claim_id": item.claim_id,
                "claim": item.claim,
                "proposed_verdict": item.proposed_verdict,
                "evidence_ids": list(item.evidence_ids),
                "rationale": item.rationale,
                "deterministic_passed": passed,
                "deterministic_faults": (
                    [reason] if reason is not None else []
                ),
            }
        )
    return {
        "schema_version": PRE_VERIFIER_SCHEMA_VERSION,
        "case_id": case_id,
        "artifact_hash": pre_verifier_artifact_hash(candidates),
        "candidates": projected,
    }


def write_pre_verifier_artifact(
    out_dir: Path,
    case_id: str,
    payload: dict[str, object],
) -> Path:
    """Write the pre-verifier capture under the same discipline as the outcome.

    The artifact carries model-authored claim and rationale prose, so it gets
    the same treatment as the l2 outcome it accompanies: a 0700 directory, a
    0600 file via fchmod, a case-id pattern check before it becomes a path
    component, and an fsync before returning.
    """
    if _CASE_ID.fullmatch(case_id) is None:
        raise ValueError("invalid pre-verifier case id")
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    path = out_dir / f"{case_id}.pre-verifier.json"
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("unable to write pre-verifier artifact")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def score_gate_only_pairs(
    outcome_artifact: dict[str, object],
    pre_verifier_artifact: dict[str, object],
) -> tuple[GateOnlyResult, ...]:
    """Join the sealed outcome against the persisted pre-verifier artifact.

    The 'on' arm's verdict per claim is the outcome's final result; the 'off'
    arm's is the pre-verifier proposed verdict whenever the deterministic
    checks passed. A claim missing from either side refuses loudly rather than
    being guessed at: a pair is a claim seen through both arms, and a silent
    half-pair is exactly the shape this comparison exists to catch.
    """
    if pre_verifier_artifact.get("schema_version") != PRE_VERIFIER_SCHEMA_VERSION:
        raise ValueError("unsupported pre-verifier schema version")
    artifact_hash = pre_verifier_artifact.get("artifact_hash")
    if not isinstance(artifact_hash, str):
        raise ValueError("pre-verifier artifact hash is missing")
    projected = pre_verifier_artifact.get("candidates")
    results = outcome_artifact.get("results")
    if not isinstance(projected, list) or not isinstance(results, list):
        raise ValueError("artifacts carry no candidate or result lists")
    on_by_claim: dict[str, str] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("outcome result must be a mapping")
        claim_id = result.get("claim_id")
        verdict = result.get("verdict")
        if isinstance(claim_id, str) and isinstance(verdict, str):
            on_by_claim[claim_id] = verdict
    pairs: list[GateOnlyResult] = []
    for item in projected:
        if not isinstance(item, dict):
            raise ValueError("pre-verifier candidate must be a mapping")
        claim_id = item.get("claim_id")
        if not isinstance(claim_id, str):
            raise ValueError("pre-verifier candidate claim id is missing")
        if claim_id not in on_by_claim:
            raise ValueError(f"claim {claim_id} has no outcome result")
        passed = item.get("deterministic_passed") is True
        faults = item.get("deterministic_faults")
        reason = (
            "deterministic_failed"
            if faults
            else None
        )
        candidate = PreVerifierCandidate(
            claim_id=claim_id,
            claim=str(item.get("claim", "")),
            proposed_verdict=str(item.get("proposed_verdict", "")),
            evidence_ids=tuple(
                str(entry) for entry in (item.get("evidence_ids") or ())
            ),
            rationale=str(item.get("rationale", "")),
        )
        on_verdict = on_by_claim[claim_id]
        pairs.append(
            compare_gate_only(
                candidate,
                artifact_hash,
                deterministic_passed=passed,
                exclusion_reason=reason,
                semantic_supports=on_verdict == candidate.proposed_verdict,
            )
        )
    return tuple(pairs)


def deterministic_reason(status: str) -> str | None:
    """Map a final verification status onto the comparison's exclusion axis.

    A claim that the chain settled as DETERMINISTIC_FAILED failed the local
    identity checks; every other status passed them and belongs to the pair.
    """
    if status == VerificationStatus.DETERMINISTIC_FAILED.value:
        return "deterministic_failed"
    return None


__all__ = [
    "PRE_VERIFIER_SCHEMA_VERSION",
    "GateOnlyResult",
    "PreVerifierCandidate",
    "build_pre_verifier_artifact",
    "compare_gate_only",
    "deterministic_reason",
    "pre_verifier_artifact_hash",
    "score_gate_only_pairs",
    "write_pre_verifier_artifact",
]
