"""Deterministic judge payload preparation, promoted from tmp/.

Both scripts lived in tmp/ -- gitignored, untested, and unreviewed -- exactly
the shape AGENTS.md records for ask.sh and the superseded sweep drivers. This
module is the reviewed form, with the one thing the tmp scripts lacked: the
sweep's count assertion. A run whose prepared payload count differs from the
caller's expected count refuses and writes nothing, so a miscounted batch
cannot reach the judge and read as a complete one.

Provider-free by construction: reads the annotation store, the author's answer
files or the L2 outcome artifacts, and the frozen RFC renditions; writes full
JudgePayload JSON per case. Every failure is a typed error with a stable code,
never a partial payload directory.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from specpilot.annotation.store import AnnotationStore
from specpilot.answer.evidence import build_evidence
from specpilot.contracts.annotation import Split
from specpilot.contracts.egress import JudgePayload, ScoringPoint
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import (
    EXCLUDED_SECTIONS,
    ClauseLimits,
    iter_clause_texts,
)
from specpilot.judge.template import read_answer_file

CORPUS_MANIFEST_ID = (
    "1abafff704358c2357ead5b837d212f130cadfa330dfa30d1df0a24f76d74295"
)
L2_OUTCOME_SCHEMA = "l2-outcome/v1"
L1_SCHEMA = "annotation-l1/v2"
L2_SCHEMA = "annotation-l2/v2"


class JudgePrepareError(Exception):
    """One stable, machine-readable preparation failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _clause_table(
    xmls: Mapping[str, Path],
) -> dict[str, tuple[Any, str]]:
    table: dict[str, tuple[Any, str]] = {}
    for xml in xmls.values():
        for clause, text in iter_clause_texts(
            xml, RfcLimits(), ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
        ):
            table[clause.clause_id] = (clause, text)
    return table


def _scoring_text(key_point: Any) -> str:
    parts = [key_point.criterion]
    if key_point.factual_values:
        parts.append("factual values: " + ", ".join(key_point.factual_values))
    return "; ".join(parts)


def _roots(
    annotation_dir: Path,
    *,
    schema: str,
    with_retirements: bool,
) -> dict[str, Any]:
    store = AnnotationStore(annotation_dir)
    try:
        records = tuple(store.iter_records())
        retirements = store.read_retirements() if with_retirements else ()
    except (OSError, ValueError) as error:
        raise JudgePrepareError("annotations_unreadable", str(error)) from error
    retired = {entry.item_id for entry in retirements}
    roots: dict[str, Any] = {}
    for record in records:
        if record.predecessor_annotation_id is not None:
            continue
        if with_retirements and record.item_id in retired:
            continue
        if getattr(record, "schema_version", None) != schema:
            continue
        if record.split is not Split.DEV or record.expected_refusal:
            continue
        roots[record.item_id] = record
    return roots


def _resolve_gold(
    annotation: Any,
    table: dict[str, tuple[Any, str]],
    item_id: str,
) -> tuple[Any, ...]:
    excerpts = []
    for clause_id in annotation.gold_clause_ids:
        resolved = table.get(clause_id)
        if resolved is None:
            raise JudgePrepareError(
                "gold_clause_unresolved",
                f"{item_id}: gold clause {clause_id[:8]} unresolved",
            )
        clause, text = resolved
        excerpts.append(
            build_evidence(
                clause, text, corpus_manifest_id=CORPUS_MANIFEST_ID
            ).excerpt
        )
    return tuple(excerpts)


def _write_payload(out_dir: Path, item_id: str, payload: JudgePayload) -> None:
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    path = out_dir / f"{item_id}.json"
    path.write_text(
        json.dumps(
            payload.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    # The payload holds model-authored answer prose and gold excerpt text;
    # keep it private like every other restricted artifact.
    path.chmod(0o600)


def _assert_count(prepared: int, expected: int | None, items: tuple[str, ...]) -> None:
    if expected is not None and prepared != expected:
        raise JudgePrepareError(
            "judge_payload_count_mismatch",
            f"prepared {prepared}, expected {expected}; "
            f"prepared items: {', '.join(items)}",
        )


def prepare_l1_payloads(
    annotation_dir: Path,
    answers_dir: Path,
    out_dir: Path,
    *,
    xmls: Mapping[str, Path],
    expected: int | None = None,
) -> tuple[str, ...]:
    """One JudgePayload per answered L1 dev case, or a typed refusal.

    A case whose answer file is missing is a refusal and is legitimately
    skipped -- the judge scores answered cases only. The count assertion turns
    a *silent* short batch into a refusal: prepared must equal expected.
    Nothing is written until every payload is built and the count has been
    checked, so a refusal leaves no partial directory.
    """
    roots = _roots(annotation_dir, schema=L1_SCHEMA, with_retirements=False)
    table = _clause_table(xmls)
    built: list[tuple[str, JudgePayload]] = []
    for item_id in sorted(roots):
        annotation = roots[item_id]
        answer = read_answer_file(answers_dir / f"{item_id}.json")
        if answer is None:
            continue
        if not annotation.key_points:
            raise JudgePrepareError(
                "missing_gold_key_points", f"{item_id}: case has no gold key points"
            )
        excerpts = _resolve_gold(annotation, table, item_id)
        built.append(
            (
                item_id,
                JudgePayload(
                    query=annotation.question,
                    final_answer=answer,
                    scoring_points=tuple(
                        ScoringPoint(
                            point_id=key_point.point_id,
                            text=_scoring_text(key_point),
                        )
                        for key_point in annotation.key_points
                    ),
                    gold_excerpts=excerpts,
                ),
            )
        )
    prepared = tuple(item_id for item_id, _ in built)
    _assert_count(len(built), expected, prepared)
    for item_id, payload in built:
        _write_payload(out_dir, item_id, payload)
    return prepared


def _render_final_answer(outcome: Mapping[str, Any]) -> str:
    lines = ["The system's design analysis follows."]
    for candidate in outcome.get("candidates", []):
        lines.append(
            f"Claim {candidate['claim_id'][:12]}: {candidate['claim']} "
            f"(proposed verdict: {candidate['proposed_verdict']}; "
            f"rationale: {candidate['rationale']})"
        )
    for result in outcome.get("results", []):
        lines.append(
            f"Final verdict for claim {result['claim_id'][:12]}: "
            f"{result['verdict']} ({result['verification_status']})"
        )
    return "\n".join(lines)


def prepare_l2_payloads(
    annotation_dir: Path,
    outcomes_dir: Path,
    answers_out: Path,
    out_dir: Path,
    *,
    xmls: Mapping[str, Path],
    expected: int | None = None,
) -> tuple[str, ...]:
    """One JudgePayload per completed L2 dev case, or a typed refusal.

    The judge's question is the design description and its final answer is the
    rendered system output: each atomic claim with its proposed verdict and
    rationale, then the final verdicts. The rendering is bounded and
    deterministic; no clause prose beyond what the model authored enters it.
    """
    roots = _roots(annotation_dir, schema=L2_SCHEMA, with_retirements=True)
    table = _clause_table(xmls)
    built: list[tuple[str, JudgePayload, str]] = []
    for item_id in sorted(roots):
        annotation = roots[item_id]
        outcome_path = outcomes_dir / f"{item_id}.json"
        if not outcome_path.exists():
            continue  # a refused or unrun case has no outcome to score
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise JudgePrepareError(
                "outcome_unreadable", f"{item_id}: outcome unreadable"
            ) from error
        if outcome.get("schema_version") != L2_OUTCOME_SCHEMA:
            raise JudgePrepareError(
                "outcome_schema_mismatch",
                f"{item_id}: outcome schema is not {L2_OUTCOME_SCHEMA}",
            )
        if not outcome.get("candidates") and outcome.get("provider_error") is None:
            raise JudgePrepareError(
                "outcome_empty", f"{item_id}: outcome has no candidates"
            )
        if not annotation.key_points:
            raise JudgePrepareError(
                "missing_gold_key_points", f"{item_id}: case has no gold key points"
            )
        final_answer = _render_final_answer(outcome)
        excerpts = _resolve_gold(annotation, table, item_id)
        try:
            payload = JudgePayload(
                query=annotation.question,
                final_answer=final_answer,
                scoring_points=tuple(
                    ScoringPoint(
                        point_id=key_point.point_id,
                        text=_scoring_text(key_point),
                    )
                    for key_point in annotation.key_points
                ),
                gold_excerpts=excerpts,
            )
        except ValidationError as error:
            raise JudgePrepareError(
                "payload_invalid", f"{item_id}: payload did not validate"
            ) from error
        built.append((item_id, payload, final_answer))
    prepared = tuple(item_id for item_id, _, _ in built)
    _assert_count(len(built), expected, prepared)
    for item_id, payload, final_answer in built:
        answers_out.mkdir(mode=0o700, parents=True, exist_ok=True)
        answers_out.chmod(0o700)
        (answers_out / f"{item_id}.json").write_text(
            json.dumps({"answer": final_answer}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _write_payload(out_dir, item_id, payload)
    return prepared


__all__ = [
    "CORPUS_MANIFEST_ID",
    "JudgePrepareError",
    "prepare_l1_payloads",
    "prepare_l2_payloads",
]
