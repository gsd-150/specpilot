"""CLI tests for judge payload preparation (Task 7 Step 1).

Fixture-only, provider-free: a synthetic RFC 9999 supplies the gold clauses,
the annotation store is written in-process, and the answer/outcome files are
authored by the test. What is pinned is the discipline the promotion added:
the count assertion refuses a short batch and writes nothing, and every
failure is a stable machine-readable code.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.annotation import (
    AnnotationOrigin,
    GoldOrigin,
    GoldOriginEvent,
    KeyPoint,
    L1Annotation,
    L2Annotation,
    QuestionDirection,
    Split,
)
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import (
    EXCLUDED_SECTIONS,
    ClauseLimits,
    iter_clause_texts,
)
from tests.helpers import rfc_factory

XML = rfc_factory.NORMATIVE_RFC_XML


@pytest.fixture
def synthetic_xml(tmp_path: Path) -> Path:
    return rfc_factory.write(tmp_path, "rfc9999.xml", XML)


@pytest.fixture
def gold_clause_ids(synthetic_xml: Path) -> tuple[str, ...]:
    clauses = tuple(
        iter_clause_texts(
            synthetic_xml,
            RfcLimits(),
            ClauseLimits(excluded_sections=EXCLUDED_SECTIONS),
        )
    )
    assert clauses
    return tuple(clause.clause_id for clause, _ in clauses[:1])


def _l1_annotation(item_id: str, gold: tuple[str, ...]) -> L1Annotation:
    return L1Annotation(
        item_id=item_id,
        split=Split.DEV,
        question="A synthetic question about RFC 9999.",
        direction=QuestionDirection.CLAUSE_FIRST,
        content_origin=AnnotationOrigin.HUMAN,
        label_origin=AnnotationOrigin.HUMAN,
        document_id="ietf-rfc9999",
        document_version="2026-08",
        gold_clause_ids=gold,
        question_gold_jaccard=0.0,
        gold_origins=(GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),),
        key_points=(KeyPoint(point_id="p1", criterion="The synthetic criterion."),),
    )


def _l2_annotation(item_id: str, gold: tuple[str, ...]) -> L2Annotation:
    return L2Annotation(
        item_id=item_id,
        split=Split.DEV,
        question="A synthetic design description about RFC 9999.",
        direction=QuestionDirection.CLAUSE_FIRST,
        content_origin=AnnotationOrigin.HUMAN,
        label_origin=AnnotationOrigin.HUMAN,
        document_id="ietf-rfc9999",
        document_version="2026-08",
        gold_clause_ids=gold,
        question_gold_jaccard=0.0,
        gold_origins=(GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),),
        key_points=(KeyPoint(point_id="p1", criterion="The synthetic criterion."),),
        claim_id="c" * 64,
        expected_verdict="compliant",
        proposed_verdict="compliant",
        supports_verdict=True,
    )


def _answer(tmp_path: Path, item_id: str) -> Path:
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / f"{item_id}.json").write_text(
        json.dumps({"answer": "The synthetic final answer."}), encoding="utf-8"
    )
    return answers


def _outcome(tmp_path: Path, item_id: str) -> Path:
    outcomes = tmp_path / "outcomes"
    outcomes.mkdir()
    (outcomes / f"{item_id}.json").write_text(
        json.dumps(
            {
                "schema_version": "l2-outcome/v1",
                "case_id": item_id,
                "design_description": "A synthetic design description.",
                "candidates": [
                    {
                        "claim_id": "c" * 64,
                        "claim": "The design satisfies the requirement.",
                        "proposed_verdict": "compliant",
                        "evidence_ids": ["e" * 64],
                        "rationale": "The shown excerpt requires it.",
                    }
                ],
                "results": [
                    {
                        "claim_id": "c" * 64,
                        "verdict": "compliant",
                        "verification_status": "verified",
                        "citation_count": 1,
                    }
                ],
                "evidence": [],
                "search_scopes": [],
                "compliance_prompt_sha256": "f" * 64,
                "verifier_prompt_sha256": "f" * 64,
                "provider_error": None,
                "parse_fault": None,
            }
        ),
        encoding="utf-8",
    )
    return outcomes


def test_l1_prepare_writes_one_payload_and_matches_the_expected_count(
    tmp_path: Path,
    synthetic_xml: Path,
    gold_clause_ids: tuple[str, ...],
) -> None:
    store = AnnotationStore(tmp_path / "annotations")
    store.create(_l1_annotation("l1-dev-001", gold_clause_ids))
    answers = _answer(tmp_path, "l1-dev-001")
    out = tmp_path / "payloads"

    code = main(
        [
            "judge",
            "prepare",
            "--level",
            "l1",
            "--annotation-dir",
            str(tmp_path / "annotations"),
            "--answers-dir",
            str(answers),
            "--out-dir",
            str(out),
            "--xml9110",
            str(synthetic_xml),
            "--xml9112",
            str(synthetic_xml),
            "--expected",
            "1",
        ]
    )

    assert code == 0
    payload_path = out / "l1-dev-001.json"
    assert stat.S_IMODE(os.stat(payload_path).st_mode) == 0o600
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["query"] == "A synthetic question about RFC 9999."
    assert payload["final_answer"] == "The synthetic final answer."
    assert [p["point_id"] for p in payload["scoring_points"]] == ["p1"]
    assert len(payload["gold_excerpts"]) == 1


def test_l1_prepare_refuses_a_short_batch_and_writes_nothing(
    tmp_path: Path,
    synthetic_xml: Path,
    gold_clause_ids: tuple[str, ...],
) -> None:
    store = AnnotationStore(tmp_path / "annotations")
    store.create(_l1_annotation("l1-dev-001", gold_clause_ids))
    # No answer file: the case reads as refused and is legitimately skipped,
    # which the count assertion then refuses instead of silently accepting.
    out = tmp_path / "payloads"

    code = main(
        [
            "judge",
            "prepare",
            "--level",
            "l1",
            "--annotation-dir",
            str(tmp_path / "annotations"),
            "--answers-dir",
            str(tmp_path / "answers"),
            "--out-dir",
            str(out),
            "--xml9110",
            str(synthetic_xml),
            "--xml9112",
            str(synthetic_xml),
            "--expected",
            "1",
        ]
    )

    assert code == 2
    assert not out.exists()


def test_l1_prepare_refuses_an_unresolved_gold_clause(
    tmp_path: Path,
    synthetic_xml: Path,
) -> None:
    store = AnnotationStore(tmp_path / "annotations")
    store.create(_l1_annotation("l1-dev-001", ("a" * 64,)))
    answers = _answer(tmp_path, "l1-dev-001")
    out = tmp_path / "payloads"

    code = main(
        [
            "judge",
            "prepare",
            "--level",
            "l1",
            "--annotation-dir",
            str(tmp_path / "annotations"),
            "--answers-dir",
            str(answers),
            "--out-dir",
            str(out),
            "--xml9110",
            str(synthetic_xml),
            "--xml9112",
            str(synthetic_xml),
            "--expected",
            "1",
        ]
    )

    assert code == 2
    assert not out.exists()


def test_l2_prepare_writes_payload_and_rendered_answer(
    tmp_path: Path,
    synthetic_xml: Path,
    gold_clause_ids: tuple[str, ...],
) -> None:
    store = AnnotationStore(tmp_path / "annotations")
    store.create(_l2_annotation("l2-dev-001", gold_clause_ids))
    outcomes = _outcome(tmp_path, "l2-dev-001")
    answers_out = tmp_path / "l2-answers"
    out = tmp_path / "l2-payloads"

    code = main(
        [
            "judge",
            "prepare",
            "--level",
            "l2",
            "--annotation-dir",
            str(tmp_path / "annotations"),
            "--outcomes-dir",
            str(outcomes),
            "--answers-out",
            str(answers_out),
            "--out-dir",
            str(out),
            "--xml9110",
            str(synthetic_xml),
            "--xml9112",
            str(synthetic_xml),
            "--expected",
            "1",
        ]
    )

    assert code == 0
    payload = json.loads((out / "l2-dev-001.json").read_text(encoding="utf-8"))
    assert "The system's design analysis follows." in payload["final_answer"]
    assert "proposed verdict: compliant" in payload["final_answer"]
    assert "Final verdict for claim" in payload["final_answer"]
    rendered = json.loads(
        (answers_out / "l2-dev-001.json").read_text(encoding="utf-8")
    )
    assert rendered["answer"] == payload["final_answer"]


def test_l2_prepare_skips_a_refused_outcome_and_the_count_shows_it(
    tmp_path: Path,
    synthetic_xml: Path,
    gold_clause_ids: tuple[str, ...],
) -> None:
    store = AnnotationStore(tmp_path / "annotations")
    store.create(_l2_annotation("l2-dev-001", gold_clause_ids))
    store.create(_l2_annotation("l2-dev-002", gold_clause_ids))
    outcomes = tmp_path / "outcomes"
    outcomes.mkdir()
    # 001 answered; 002 refused with an empty-candidate capture.
    (outcomes / "l2-dev-001.json").write_text(
        json.dumps(
            {
                "schema_version": "l2-outcome/v1",
                "case_id": "l2-dev-001",
                "design_description": "d",
                "candidates": [
                    {
                        "claim_id": "c" * 64,
                        "claim": "claim",
                        "proposed_verdict": "compliant",
                        "evidence_ids": ["e" * 64],
                        "rationale": "r",
                    }
                ],
                "results": [],
                "evidence": [],
                "search_scopes": [],
                "compliance_prompt_sha256": "f" * 64,
                "verifier_prompt_sha256": "f" * 64,
                "provider_error": None,
                "parse_fault": None,
            }
        ),
        encoding="utf-8",
    )
    (outcomes / "l2-dev-002.json").write_text(
        json.dumps(
            {
                "schema_version": "l2-outcome/v1",
                "case_id": "l2-dev-002",
                "design_description": "d",
                "candidates": [],
                "results": [],
                "evidence": [],
                "search_scopes": [],
                "compliance_prompt_sha256": "f" * 64,
                "verifier_prompt_sha256": "f" * 64,
                "provider_error": None,
                "parse_fault": None,
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "l2-payloads"

    code = main(
        [
            "judge",
            "prepare",
            "--level",
            "l2",
            "--annotation-dir",
            str(tmp_path / "annotations"),
            "--outcomes-dir",
            str(outcomes),
            "--answers-out",
            str(tmp_path / "l2-answers"),
            "--out-dir",
            str(out),
            "--xml9110",
            str(synthetic_xml),
            "--xml9112",
            str(synthetic_xml),
            "--expected",
            "1",
        ]
    )

    assert code == 0
    assert (out / "l2-dev-001.json").exists()
    assert not (out / "l2-dev-002.json").exists()


def test_l2_prepare_refuses_an_outcome_with_the_wrong_schema(
    tmp_path: Path,
    synthetic_xml: Path,
    gold_clause_ids: tuple[str, ...],
) -> None:
    store = AnnotationStore(tmp_path / "annotations")
    store.create(_l2_annotation("l2-dev-001", gold_clause_ids))
    outcomes = tmp_path / "outcomes"
    outcomes.mkdir()
    (outcomes / "l2-dev-001.json").write_text(
        json.dumps({"schema_version": "l2-outcome/v0", "candidates": []}),
        encoding="utf-8",
    )
    out = tmp_path / "l2-payloads"

    code = main(
        [
            "judge",
            "prepare",
            "--level",
            "l2",
            "--annotation-dir",
            str(tmp_path / "annotations"),
            "--outcomes-dir",
            str(outcomes),
            "--answers-out",
            str(tmp_path / "l2-answers"),
            "--out-dir",
            str(out),
            "--xml9110",
            str(synthetic_xml),
            "--xml9112",
            str(synthetic_xml),
            "--expected",
            "1",
        ]
    )

    assert code == 2
    assert not out.exists()
