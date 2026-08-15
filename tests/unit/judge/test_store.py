"""Tests for the judge/label stores and the freeze evidence builder.

Written RED-first per the judge scoring plan. The store tests assert the
property the freeze leans on — a hash names bytes that still exist and still
parse — and the evidence tests assert the bytes are prose-free and change
whenever any referenced record changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specpilot.contracts.scoring import (
    HumanDevLabels,
    HumanKeyPointLabel,
    JudgeOutput,
    JudgeRecord,
    KeyPointHit,
)
from specpilot.judge.calibration import build_calibration_report
from specpilot.judge.evidence import build_scoring_evidence
from specpilot.judge.prompt import JUDGE_PROMPT_V1_BODY, JudgePrompt
from specpilot.judge.store import HumanLabelStore, JudgeRecordStore
from specpilot.manifests._secure_records import SecureRecordDirectory

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
PROMPT = JudgePrompt(
    identifier="judge-answer-scorer",
    version="1",
    body=JUDGE_PROMPT_V1_BODY,
)
PROHIBITED_KEYS = frozenset(
    {"question", "claim", "excerpt", "answer", "rationale"}
)


def _record(case_id: str = "l1-dev-001") -> JudgeRecord:
    return JudgeRecord(
        case_id=case_id,
        question_hash="1" * 64,
        final_answer_hash="2" * 64,
        prompt_hash=PROMPT.content_sha256,
        prompt_version="1",
        model_id="glm-5.2",
        output=JudgeOutput(
            key_point_hits=(
                KeyPointHit(point_id="p1", hit=True),
                KeyPointHit(point_id="p2", hit=False, miss_reason="missing"),
            )
        ),
        scored_at=NOW,
    )


def _labels(case_id: str = "l1-dev-001") -> HumanDevLabels:
    return HumanDevLabels(
        case_id=case_id,
        labeler="chunxue",
        key_points=(
            HumanKeyPointLabel(point_id="p1", hit=True),
            HumanKeyPointLabel(point_id="p2", hit=False),
        ),
        labelled_at=NOW,
    )


def test_judge_records_round_trip_and_are_idempotent(tmp_path: Path) -> None:
    store = JudgeRecordStore(tmp_path / "records")
    record = _record()
    record_id = store.create(record)
    assert len(record_id) == 64
    assert store.read(record_id) == record
    assert store.create(record) == record_id
    assert store.record_ids() == (record_id,)
    assert [r.case_id for r in store.iter_records()] == ["l1-dev-001"]


def test_a_changed_record_is_a_new_id_not_an_overwrite(tmp_path: Path) -> None:
    store = JudgeRecordStore(tmp_path / "records")
    first = store.create(_record())
    changed = _record().model_copy(
        update={"final_answer_hash": "f" * 64}
    )
    second = store.create(changed)
    assert second != first
    assert store.read(first).final_answer_hash == "2" * 64
    assert store.read(second).final_answer_hash == "f" * 64


def test_foreign_bytes_fail_to_read(tmp_path: Path) -> None:
    store = JudgeRecordStore(tmp_path / "records")
    foreign_id = "9" * 64
    with SecureRecordDirectory.open(tmp_path / "records", create=True) as records:
        records.publish(f"{foreign_id}.json", b"not a judge record", max_bytes=1024)
    with pytest.raises(ValueError):
        store.read(foreign_id)
    with pytest.raises(ValueError):
        store.record_ids()


def test_human_labels_round_trip(tmp_path: Path) -> None:
    store = HumanLabelStore(tmp_path / "labels")
    label_id = store.create(_labels())
    assert store.read(label_id).labeler == "chunxue"
    assert store.label_ids() == (label_id,)


def _evidence_bytes() -> bytes:
    report = build_calibration_report((_record(),), (_labels(),))
    return build_scoring_evidence(
        route_id="judge_calibrated",
        prompt=PROMPT,
        model_id="glm-5.2",
        report=report,
        judge_record_sha256s=("a" * 64,),
        human_label_sha256s=("b" * 64,),
    )


def test_evidence_is_json_and_prose_free() -> None:
    evidence = json.loads(_evidence_bytes())
    assert evidence["schema_version"] == "judge-calibration-evidence/v1"
    assert evidence["selected_route"] == "judge_calibrated"
    assert evidence["split"] == "dev"
    assert evidence["model_id"] == "glm-5.2"
    assert evidence["prompt"]["content_sha256"] == PROMPT.content_sha256
    assert evidence["calibration_report"]["case_count"] == 1

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in PROHIBITED_KEYS, key
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(evidence)


def test_evidence_changes_when_a_referenced_hash_changes() -> None:
    report = build_calibration_report((_record(),), (_labels(),))
    base = build_scoring_evidence(
        route_id="judge_calibrated",
        prompt=PROMPT,
        model_id="glm-5.2",
        report=report,
        judge_record_sha256s=("a" * 64,),
        human_label_sha256s=("b" * 64,),
    )
    changed = build_scoring_evidence(
        route_id="judge_calibrated",
        prompt=PROMPT,
        model_id="glm-5.2",
        report=report,
        judge_record_sha256s=("c" * 64,),
        human_label_sha256s=("b" * 64,),
    )
    assert changed != base


def test_evidence_refuses_duplicate_hashes() -> None:
    report = build_calibration_report((_record(),), (_labels(),))
    with pytest.raises(ValueError):
        build_scoring_evidence(
            route_id="judge_calibrated",
            prompt=PROMPT,
            model_id="glm-5.2",
            report=report,
            judge_record_sha256s=("a" * 64, "a" * 64),
            human_label_sha256s=("b" * 64,),
        )
