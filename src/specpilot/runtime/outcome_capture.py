"""Author-run capture of the L2 prose the durable records are forbidden to hold.

The calibration judge for the W5 evaluation freeze needs the L2 answer prose —
the design description, each atomic claim's text, its proposed verdict and
rationale, and the final verdict — exactly once, in-process. The durable records
cannot carry it by design: ``ComplianceResult``, the sanitized run events, and
the checkpoints are all prose-free so that no RFC clause text or provider prose
ever lands in a git-tracked or ledger-tracked record. This module writes that
one-off capture into the author's ``artifacts/restricted/`` directory, never into
git, under private permissions.

The output is a JSON object whose only prose is what the model itself authored
(``design_description``, ``claim``, ``rationale``). No clause excerpt, quote, or
index entry is ever placed in it, so the "no source text in a committable
record" invariant is preserved even though the artifact is gitignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from specpilot.contracts.verdict import ComplianceVerdict, VerificationStatus
from specpilot.runtime.l2 import L2Outcome

L2_OUTCOME_SCHEMA_VERSION = "l2-outcome/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_CLAIM_ID = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_VERDICTS = frozenset(item.value for item in ComplianceVerdict)
_VERIFICATION_STATUSES = frozenset(item.value for item in VerificationStatus)


def build_prompt_identity() -> dict[str, str]:
    """Hash the two L2 stage instructions as the wire renders them.

    Which prompt produced a result was, until this existed, recoverable only by
    comparing a commit timestamp against a file mtime — which answers nothing
    when an edit lands minutes before a run, or is never committed, and leaves
    the dev evidence a freeze rests on unable to say what produced it.

    Imported from the module the transport renders from rather than copied.
    A second copy is the defect class AGENTS.md records: a value present in the
    code and absent from the bytes that left, with nothing afterwards able to
    tell the two apart.
    """
    from specpilot.providers.http import (
        COMPLIANCE_REPLY_INSTRUCTIONS,
        SEMANTIC_REPLY_INSTRUCTIONS,
    )

    return {
        "compliance_prompt_sha256": hashlib.sha256(
            COMPLIANCE_REPLY_INSTRUCTIONS.encode("utf-8")
        ).hexdigest(),
        "verifier_prompt_sha256": hashlib.sha256(
            SEMANTIC_REPLY_INSTRUCTIONS.encode("utf-8")
        ).hexdigest(),
    }


def build_l2_outcome(
    case_id: str,
    design_description: str,
    outcome: L2Outcome,
) -> dict[str, Any]:
    """Project one ``L2Outcome`` into the ``l2-outcome/v1`` artifact contract.

    ``candidates`` come from the compliance stage's still-untrusted prose
    (``ComplianceCandidate`` claim/proposed_verdict/rationale); ``results`` come
    from the final prose-free ``ComplianceResult`` tuple and carry only the claim
    id, verdict, verification status, and the number of citations — never any
    citation text. Provider and parse failures are projected verbatim as their
    nullable error fields and otherwise leave both lists empty, so a refused run
    still yields an artifact the judge can inspect rather than a missing file.
    """
    candidates: list[dict[str, Any]] = []
    if outcome.compliance is not None:
        for item in outcome.compliance.candidates:
            candidate = item.candidate
            candidates.append(
                {
                    "claim_id": item.claim_id,
                    "claim": candidate.claim,
                    "proposed_verdict": candidate.proposed_verdict.value,
                    "rationale": candidate.rationale,
                    "evidence_ids": list(candidate.evidence_ids),
                }
            )
    results: list[dict[str, Any]] = [
        {
            "claim_id": item.claim_id,
            "verdict": item.verdict.value,
            "verification_status": item.verification_status.value,
            "citation_count": len(item.citations),
        }
        for item in outcome.results
    ]
    evidence_refs: list[dict[str, Any]] = []
    seen_evidence: set[tuple[str, str]] = set()
    for checkpoint in outcome.checkpoints:
        for ref in checkpoint.evidence:
            key = (ref.clause_id, ref.content_hash)
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            evidence_refs.append(
                {
                    "clause_id": ref.clause_id,
                    "content_hash": ref.content_hash,
                    "document_id": ref.document_id,
                    "document_version": ref.document_version,
                    "section_number": ref.section_number,
                }
            )
    search_scopes: list[dict[str, Any]] = []
    if outcome.planning is not None:
        from specpilot.agents.contracts import SearchClausesStep

        for step in outcome.planning.plan.steps:
            if isinstance(step, SearchClausesStep):
                search_scopes.append(
                    {
                        "step_id": step.step_id,
                        "document_ids": step.args.document_ids,
                    }
                )
    artifact: dict[str, Any] = {
        "schema_version": L2_OUTCOME_SCHEMA_VERSION,
        "case_id": case_id,
        "design_description": design_description,
        "candidates": candidates,
        "results": results,
        "evidence": evidence_refs,
        "search_scopes": search_scopes,
        "provider_error": outcome.provider_error,
        "parse_fault": outcome.parse_fault,
        **build_prompt_identity(),
    }
    if outcome.raw_planning_reply is not None:
        artifact["raw_plan_reply"] = outcome.raw_planning_reply[:16384]
    if outcome.raw_compliance_reply is not None:
        # Present only on a compliance parse fault, so the author can fix the
        # reply contract from the data; bounded again on the way in.
        artifact["raw_reply"] = outcome.raw_compliance_reply[:16384]
    return artifact


def validate_l2_outcome(payload: object) -> dict[str, Any]:
    """Fail closed on any shape the judge would have to guess at.

    Every field is checked rather than trusted so a later schema change cannot
    quietly drop a claim or a citation count, and the two string enums are
    checked against the closed sets rather than accepted as free text.
    """
    if not isinstance(payload, dict):
        raise ValueError("l2 outcome must be a mapping")
    if payload.get("schema_version") != L2_OUTCOME_SCHEMA_VERSION:
        raise ValueError("unsupported l2 outcome schema version")
    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None:
        raise ValueError("invalid l2 outcome case id")
    if not isinstance(payload.get("design_description"), str):
        raise ValueError("l2 outcome design description must be a string")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("l2 outcome candidates must be a list")
    for candidate in candidates:
        _require_candidate(candidate)
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("l2 outcome results must be a list")
    for result in results:
        _require_result(result)
    for key in ("compliance_prompt_sha256", "verifier_prompt_sha256"):
        value = payload.get(key)
        # Optional so artifacts written before this existed still validate, but
        # never accepted malformed: a prompt identity that is present and wrong
        # is worse than an absent one, which at least prompts the question.
        if value is not None and (
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
        ):
            raise ValueError(f"l2 outcome {key} must be a sha256 digest")
    for key in ("provider_error", "parse_fault", "raw_reply", "raw_plan_reply"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"l2 outcome {key} must be a string or null")
    return payload


def _require_candidate(candidate: object) -> None:
    if not isinstance(candidate, dict):
        raise ValueError("l2 outcome candidate must be a mapping")
    claim_id = candidate.get("claim_id")
    if not isinstance(claim_id, str) or _CLAIM_ID.fullmatch(claim_id) is None:
        raise ValueError("l2 outcome candidate claim id must be 64 hex")
    if not isinstance(candidate.get("claim"), str):
        raise ValueError("l2 outcome candidate claim must be a string")
    if candidate.get("proposed_verdict") not in _VERDICTS:
        raise ValueError("l2 outcome candidate verdict is invalid")
    if not isinstance(candidate.get("rationale"), str):
        raise ValueError("l2 outcome candidate rationale must be a string")


def _require_result(result: object) -> None:
    if not isinstance(result, dict):
        raise ValueError("l2 outcome result must be a mapping")
    claim_id = result.get("claim_id")
    if not isinstance(claim_id, str) or _CLAIM_ID.fullmatch(claim_id) is None:
        raise ValueError("l2 outcome result claim id must be 64 hex")
    if result.get("verdict") not in _VERDICTS:
        raise ValueError("l2 outcome result verdict is invalid")
    if result.get("verification_status") not in _VERIFICATION_STATUSES:
        raise ValueError("l2 outcome result verification status is invalid")
    citation_count = result.get("citation_count")
    if (
        not isinstance(citation_count, int)
        or isinstance(citation_count, bool)
        or citation_count < 0
    ):
        raise ValueError("l2 outcome citation count must be a non-negative integer")


def write_l2_outcome(
    out_dir: Path,
    case_id: str,
    payload: dict[str, Any],
) -> Path:
    """Write one validated artifact under private permissions.

    The directory is forced to 0700 and the file to 0600 so a capture that
    carries the author's design prose is not readable by other local accounts.
    ``case_id`` is pattern-checked before it becomes a path component, so a
    hostile id cannot escape ``out_dir``.
    """
    validate_l2_outcome(payload)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    path = out_dir / f"{case_id}.json"
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # fchmod rather than relying on the mode argument, so an unusual umask
        # cannot leave the design prose readable to other local accounts.
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("unable to write l2 outcome artifact")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


__all__ = [
    "L2_OUTCOME_SCHEMA_VERSION",
    "build_prompt_identity",
    "build_l2_outcome",
    "validate_l2_outcome",
    "write_l2_outcome",
]
