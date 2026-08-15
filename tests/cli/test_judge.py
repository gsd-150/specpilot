"""CLI tests for the judge calibration path.

Fixture-only: no provider is ever called. The tests assert the stable refusal
codes the author's calibration loop depends on — a mistyped id, a missing
answer file, or a mixed prompt set must refuse with a machine-readable code
rather than emit a partial calibration that later freezes quietly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.annotation import (
    AnnotationOrigin,
    GoldOrigin,
    GoldOriginEvent,
    KeyPoint,
    L1Annotation,
    QuestionDirection,
    Split,
)
from specpilot.contracts.scoring import (
    AnswerClaimJudgement,
    ClaimVerdict,
    HumanDevLabels,
    JudgeOutput,
    JudgeRecord,
    KeyPointHit,
)
from specpilot.judge.prompt import JUDGE_PROMPT_V1_BODY, JudgePrompt
from specpilot.judge.store import HumanLabelStore, JudgeRecordStore

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
PROMPT = JudgePrompt(
    identifier="judge-answer-scorer", version="1", body=JUDGE_PROMPT_V1_BODY
)


def _annotation(item_id: str, points: tuple[str, ...]) -> L1Annotation:
    return L1Annotation(
        item_id=item_id,
        split=Split.DEV,
        question="A test question about the corpus.",
        direction=QuestionDirection.CLAUSE_FIRST,
        content_origin=AnnotationOrigin.HUMAN,
        label_origin=AnnotationOrigin.HUMAN,
        document_id="ietf-rfc9110",
        document_version="2022-06",
        gold_clause_ids=("a" * 64,),
        question_gold_jaccard=0.0,
        gold_origins=(GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),),
        key_points=tuple(
            KeyPoint(point_id=point_id, criterion=f"Criterion {point_id}")
            for point_id in points
        ),
    )


def _record(case_id: str, points: tuple[str, ...]) -> JudgeRecord:
    return JudgeRecord(
        case_id=case_id,
        question_hash="1" * 64,
        final_answer_hash="2" * 64,
        prompt_hash=PROMPT.content_sha256,
        prompt_version="1",
        model_id="glm-5.2",
        output=JudgeOutput(
            key_point_hits=tuple(
                KeyPointHit(
                    point_id=point_id,
                    hit=True,
                )
                for point_id in points
            ),
            answer_claims=(
                AnswerClaimJudgement(
                    claim_id="c1",
                    claim="An extracted statement.",
                    verdict=ClaimVerdict.SUPPORTED,
                ),
            ),
        ),
        scored_at=NOW,
    )


def _labels_file(
    tmp_path: Path,
    case_id: str = "l1-dev-001",
    points: tuple[str, ...] = ("p1", "p2"),
) -> Path:
    labels = HumanDevLabels(
        case_id=case_id,
        labeler="chunxue",
        key_points=tuple(
            {"point_id": point_id, "hit": True} for point_id in points
        ),
        claims=(
            {"claim_id": "c1", "verdict": "supported", "severe": False},
        ),
        labelled_at=NOW,
    )
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels.model_dump(mode="json")), encoding="utf-8")
    return path


def _store_record(tmp_path: Path, case_id: str = "l1-dev-001") -> JudgeRecordStore:
    store = JudgeRecordStore(tmp_path / "records")
    store.create(_record(case_id, ("p1", "p2")))
    return store


def test_calibrate_writes_prose_free_evidence(tmp_path: Path) -> None:
    _store_record(tmp_path)
    label_store = HumanLabelStore(tmp_path / "labels")
    label_store.create(
        HumanDevLabels(
            case_id="l1-dev-001",
            labeler="chunxue",
            key_points=(
                {"point_id": "p1", "hit": True},
                {"point_id": "p2", "hit": True},
            ),
            claims=(
                {"claim_id": "c1", "verdict": "supported", "severe": False},
            ),
            labelled_at=NOW,
        )
    )
    evidence = tmp_path / "evidence.json"
    exit_code = main(
        [
            "judge",
            "calibrate",
            "--records-dir",
            str(tmp_path / "records"),
            "--labels-dir",
            str(tmp_path / "labels"),
            "--route-id",
            "judge_calibrated",
            "--evidence-out",
            str(evidence),
        ]
    )
    assert exit_code == 0
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["selected_route"] == "judge_calibrated"
    assert payload["calibration_report"]["case_count"] == 1
    assert payload["calibration_report"]["key_points"]["agreement_rate"] == 1.0


def test_calibrate_refuses_a_mixed_prompt_set(tmp_path: Path) -> None:
    store = _store_record(tmp_path)
    foreign = _record("l1-dev-002", ("p1", "p2")).model_copy(
        update={"prompt_hash": "f" * 64}
    )
    store.create(foreign)
    evidence = tmp_path / "evidence.json"
    exit_code = main(
        [
            "judge",
            "calibrate",
            "--records-dir",
            str(tmp_path / "records"),
            "--labels-dir",
            str(tmp_path / "labels"),
            "--route-id",
            "judge_calibrated",
            "--evidence-out",
            str(evidence),
        ]
    )
    assert exit_code == 2
    assert not evidence.exists()


def test_labels_template_emits_one_sheet_per_case(tmp_path: Path) -> None:
    _store_record(tmp_path)
    annotation_store = AnnotationStore(tmp_path / "annotations")
    annotation_store.create(_annotation("l1-dev-001", ("p1", "p2")))
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "l1-dev-001.json").write_text(
        json.dumps({"answer": "The final answer."}), encoding="utf-8"
    )
    out = tmp_path / "templates"
    exit_code = main(
        [
            "judge",
            "labels-template",
            "--records-dir",
            str(tmp_path / "records"),
            "--annotations-dir",
            str(tmp_path / "annotations"),
            "--answers-dir",
            str(answers),
            "--out-dir",
            str(out),
        ]
    )
    assert exit_code == 0
    template = json.loads((out / "l1-dev-001.json").read_text(encoding="utf-8"))
    assert template["question"] == "A test question about the corpus."
    assert template["final_answer"] == "The final answer."
    assert {p["point_id"] for p in template["key_points"]} == {"p1", "p2"}
    assert [c["claim_id"] for c in template["claims"]] == ["c1"]


def test_labels_template_refuses_a_missing_answer(tmp_path: Path) -> None:
    _store_record(tmp_path)
    annotation_store = AnnotationStore(tmp_path / "annotations")
    annotation_store.create(_annotation("l1-dev-001", ("p1", "p2")))
    answers = tmp_path / "answers"
    answers.mkdir()
    out = tmp_path / "templates"
    exit_code = main(
        [
            "judge",
            "labels-template",
            "--records-dir",
            str(tmp_path / "records"),
            "--annotations-dir",
            str(tmp_path / "annotations"),
            "--answers-dir",
            str(answers),
            "--out-dir",
            str(out),
        ]
    )
    assert exit_code == 2
    assert not out.exists()


def test_labels_template_refuses_a_point_mismatch(tmp_path: Path) -> None:
    _store_record(tmp_path)
    annotation_store = AnnotationStore(tmp_path / "annotations")
    annotation_store.create(_annotation("l1-dev-001", ("p1", "p3")))
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "l1-dev-001.json").write_text(
        json.dumps({"answer": "The final answer."}), encoding="utf-8"
    )
    out = tmp_path / "templates"
    exit_code = main(
        [
            "judge",
            "labels-template",
            "--records-dir",
            str(tmp_path / "records"),
            "--annotations-dir",
            str(tmp_path / "annotations"),
            "--answers-dir",
            str(answers),
            "--out-dir",
            str(out),
        ]
    )
    assert exit_code == 2


def test_labels_add_stores_and_refuses_unknown_cases(tmp_path: Path) -> None:
    _store_record(tmp_path)
    good = _labels_file(tmp_path, "l1-dev-001")
    exit_code = main(
        [
            "judge",
            "labels-add",
            "--labels-file",
            str(good),
            "--records-dir",
            str(tmp_path / "records"),
            "--labels-dir",
            str(tmp_path / "labels"),
        ]
    )
    assert exit_code == 0
    assert len(HumanLabelStore(tmp_path / "labels").label_ids()) == 1

    foreign = _labels_file(tmp_path, "l1-dev-999")
    exit_code = main(
        [
            "judge",
            "labels-add",
            "--labels-file",
            str(foreign),
            "--records-dir",
            str(tmp_path / "records"),
            "--labels-dir",
            str(tmp_path / "labels"),
        ]
    )
    assert exit_code == 2


def test_labels_add_refuses_a_point_mismatch(tmp_path: Path) -> None:
    _store_record(tmp_path)
    labels = _labels_file(tmp_path, "l1-dev-001", ("p1", "p9"))
    exit_code = main(
        [
            "judge",
            "labels-add",
            "--labels-file",
            str(labels),
            "--records-dir",
            str(tmp_path / "records"),
            "--labels-dir",
            str(tmp_path / "labels"),
        ]
    )
    assert exit_code == 2


def test_score_refuses_an_unreadable_payload(tmp_path: Path) -> None:
    payload = tmp_path / "payload.json"
    payload.write_text("not json", encoding="utf-8")
    exit_code = main(
        [
            "judge",
            "score",
            "--payload",
            str(payload),
            "--case-id",
            "l1-dev-001",
            "--task-level",
            "L1",
            "--prompt-version",
            "1",
            "--source-manifest",
            "a" * 64,
            "--manifest-dir",
            str(tmp_path / "manifests"),
            "--corpus-manifest",
            "b" * 64,
            "--corpus-manifest-dir",
            str(tmp_path / "corpus-manifests"),
            "--ledger-dsn",
            "postgresql://localhost/invalid",
            "--evaluation-root-id",
            "root-1",
            "--run-id",
            "run-1",
            "--records-dir",
            str(tmp_path / "records"),
        ]
    )
    assert exit_code == 3


def test_score_refuses_an_unknown_prompt_version(tmp_path: Path) -> None:
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "kind": "judge",
                "query": "The question.",
                "final_answer": "The answer.",
                "scoring_points": [{"point_id": "p1", "text": "The point."}],
            }
        ),
        encoding="utf-8",
    )
    exit_code = main(
        [
            "judge",
            "score",
            "--payload",
            str(payload),
            "--case-id",
            "l1-dev-001",
            "--task-level",
            "L1",
            "--prompt-version",
            "99",
            "--source-manifest",
            "a" * 64,
            "--manifest-dir",
            str(tmp_path / "manifests"),
            "--corpus-manifest",
            "b" * 64,
            "--corpus-manifest-dir",
            str(tmp_path / "corpus-manifests"),
            "--ledger-dsn",
            "postgresql://localhost/invalid",
            "--evaluation-root-id",
            "root-1",
            "--run-id",
            "run-1",
            "--records-dir",
            str(tmp_path / "records"),
        ]
    )
    assert exit_code == 2
