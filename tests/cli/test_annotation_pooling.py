from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import cast

import pytest

from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.annotation import L1Annotation
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, build_clauses
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.dense import DenseHit
from specpilot.retrieval.pooling import (
    PoolingDecision,
    PoolingOutcome,
    PoolingStore,
    head_decisions,
)
from tests.helpers import rfc_factory

POOL_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Pool</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="notmod" numbered="true">
      <name>Not Modified</name>
      <t>The server generating this response must generate the following header
        fields that would have been sent in a successful response.</t>
      <t>Content-Location, Date, ETag, and Vary.</t>
      <t>A sender ought not generate representation metadata beyond those fields.</t>
    </section>
  </middle>
</rfc>
"""


class FakeRow:
    def tolist(self) -> list[float]:
        return [0.0] * 1024


class FakeVectors:
    def __getitem__(self, index: int) -> FakeRow:
        assert index == 0
        return FakeRow()


class FakeDenseIndex:
    candidate_id = ""
    count = 0
    ids: set[str] = set()
    searches = 0
    closes = 0

    @classmethod
    def open(cls, url: str, name: str) -> FakeDenseIndex:
        assert url == "http://127.0.0.1:6333"
        assert name == "specpilot_fixture"
        return cls()

    def vector_size(self) -> int:
        return 1024

    def point_count(self) -> int:
        return self.count

    def unit_ids(self) -> frozenset[str]:
        return frozenset(self.ids)

    def search(self, vector: list[float], k: int) -> list[DenseHit]:
        assert len(vector) == 1024
        assert k == 5
        type(self).searches += 1
        return [DenseHit(unit_id=self.candidate_id, score=0.9, payload={})]

    def close(self) -> None:
        type(self).closes += 1


@pytest.fixture
def pooling_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    xml = rfc_factory.write(tmp_path, "rfc9999.xml", POOL_RFC_XML)
    manifest_dir = tmp_path / "manifests"
    manifest = ManifestStore(manifest_dir).create_source_v2(
        RfcSourceManifestDraft(
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            text_url="https://www.rfc-editor.org/rfc/rfc9999.txt",
            xml_url="https://www.rfc-editor.org/rfc/rfc9999.xml",
            text_sha256="a" * 64,
            xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest(),
            downloaded_at="2026-08-09T09:00:00Z",
            created_at="2026-08-09T09:01:00Z",
        )
    )
    clauses = build_clauses(xml, RfcLimits(), ClauseLimits())
    obligation, fields, _ = clauses
    FakeDenseIndex.candidate_id = obligation.clause_id
    FakeDenseIndex.count = len(clauses)
    FakeDenseIndex.ids = {clause.clause_id for clause in clauses}
    FakeDenseIndex.searches = 0
    FakeDenseIndex.closes = 0
    monkeypatch.setattr(
        "specpilot.cli.DenseIndex",
        FakeDenseIndex,
        raising=False,
    )
    monkeypatch.setattr(
        "specpilot.cli.load_encoder",
        lambda *args, **kwargs: lambda texts: FakeVectors(),
    )
    monkeypatch.setattr("specpilot.cli.weights_sha256", lambda path: "b" * 64)

    annotation_dir = tmp_path / "annotations"
    store = AnnotationStore(annotation_dir)
    store.create(
        L1Annotation(
            item_id="l1-dev-001",
            split="dev",
            question="Which header fields belong in the response?",
            direction="clause_first",
            content_origin="model",
            label_origin="mixed",
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            gold_clause_ids=(fields.clause_id,),
            gold_section_paths=(fields.section_path,),
            key_points=({"point_id": "kp-1", "criterion": "names the fields"},),
            question_gold_jaccard=0.2,
            gold_origins=(
                {"origin": "model_proposal", "producer": "draft-model"},
                {"origin": "human_source_review"},
            ),
        )
    )
    store.create(
        L1Annotation(
            item_id="l1-dev-010",
            split="dev",
            question="What must a 304 response carry over from a 200 response?",
            direction="scenario_first",
            content_origin="model",
            label_origin="mixed",
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            gold_clause_ids=(fields.clause_id,),
            gold_section_paths=(fields.section_path,),
            key_points=(
                {
                    "point_id": "kp-1",
                    "criterion": "states the obligation and fields",
                },
            ),
            question_gold_jaccard=0.0,
            gold_origins=(
                {"origin": "model_proposal", "producer": "draft-model"},
                {"origin": "human_source_review"},
            ),
        )
    )
    return {
        "xml": xml,
        "manifest_dir": manifest_dir,
        "manifest_id": manifest.manifest_id,
        "annotation_dir": annotation_dir,
        "pool_dir": tmp_path / "pool",
        "model_dir": tmp_path / "model",
        "obligation_id": obligation.clause_id,
        "fields_id": fields.clause_id,
    }


def registration_args(workspace: dict[str, object]) -> list[str]:
    return [
        "annotation", "pool-register",
        "--annotation-dir", str(workspace["annotation_dir"]),
        "--pool-dir", str(workspace["pool_dir"]),
        "--manifest-dir", str(workspace["manifest_dir"]),
        "--manifest", str(workspace["manifest_id"]),
        "--xml", str(workspace["xml"]),
        "--model-dir", str(workspace["model_dir"]),
        "--model-id", "BAAI/bge-m3",
        "--device", "cpu",
        "--qdrant-url", "http://127.0.0.1:6333",
        "--collection", "specpilot_fixture",
        "--weights-sha256", "b" * 64,
        "--author-id", "chunxue",
        "--created-at", "2026-08-09T10:00:00Z",
    ]


def review_args(workspace: dict[str, object], run_id: str) -> list[str]:
    return [
        "annotation", "pool-review",
        "--annotation-dir", str(workspace["annotation_dir"]),
        "--pool-dir", str(workspace["pool_dir"]),
        "--run-id", run_id,
        "--manifest-dir", str(workspace["manifest_dir"]),
        "--manifest", str(workspace["manifest_id"]),
        "--xml", str(workspace["xml"]),
        "--reviewer", "chunxue",
    ]


def last_json(output: str) -> dict[str, object]:
    return json.loads(output.strip().splitlines()[-1])


def test_registration_freezes_two_independent_candidate_routes(
    pooling_workspace: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(registration_args(pooling_workspace))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    result = last_json(captured.out)
    assert result["status"] == "registered"
    assert result["item_count"] == 2
    assert FakeDenseIndex.searches == 2
    assert FakeDenseIndex.closes == 1
    run = PoolingStore(Path(pooling_workspace["pool_dir"])).read_run(
        str(result["run_id"])
    )
    expected_inventory = hashlib.sha256(
        "\n".join(sorted(FakeDenseIndex.ids)).encode()
    ).hexdigest()
    assert run.dense_inventory_sha256 == expected_inventory
    routes = {
        route
        for item in run.items
        for candidate in item.candidates
        for route in candidate.route_ranks
    }
    assert routes == {"bm25", "dense"}
    assert "Which header" not in captured.out
    assert "Content-Location" not in captured.out


def test_registration_refuses_a_same_size_dense_inventory_from_an_old_corpus(
    pooling_workspace: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    current = set(FakeDenseIndex.ids)
    FakeDenseIndex.ids = {"f" * 64, *tuple(sorted(current))[1:]}

    code = main(registration_args(pooling_workspace))
    captured = capsys.readouterr()

    assert code != 0
    assert captured.err.strip() == "dense_point_inventory_mismatch"
    assert not Path(pooling_workspace["pool_dir"]).exists()


def test_review_extends_multi_paragraph_gold_and_seals_all_items(
    pooling_workspace: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(registration_args(pooling_workspace)) == 0
    registered = last_json(capsys.readouterr().out)
    run = PoolingStore(Path(pooling_workspace["pool_dir"])).read_run(
        str(registered["run_id"])
    )
    target = next(item for item in run.items if item.item_id == "l1-dev-010")
    chosen = next(
        index
        for index, candidate in enumerate(target.candidates)
        if candidate.unit_id == pooling_workspace["obligation_id"]
    )
    # l1-dev-001 confirms its existing gold; l1-dev-010 adds the obligation
    # paragraph preceding its current field-list gold.
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(f"complete\n{chr(ord('A') + chosen)}\n"),
    )

    code = main(review_args(pooling_workspace, str(registered["run_id"])))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    result = last_json(captured.out)
    assert result["status"] == "sealed"
    assert result["adjudicated_items"] == 2
    records = tuple(
        AnnotationStore(
            Path(pooling_workspace["annotation_dir"])
        ).iter_records()
    )
    heads = {record.item_id: record for record in records if record.adjudications}
    assert set(heads["l1-dev-010"].gold_clause_ids) == {
        pooling_workspace["fields_id"],
        pooling_workspace["obligation_id"],
    }
    assert heads["l1-dev-001"].gold_clause_ids == (pooling_workspace["fields_id"],)

    searches = FakeDenseIndex.searches
    assert main(review_args(pooling_workspace, str(registered["run_id"]))) == 0
    resumed = last_json(capsys.readouterr().out)
    assert resumed["status"] == "sealed"
    assert FakeDenseIndex.searches == searches


def test_review_can_pause_without_regenerating_candidates(
    pooling_workspace: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(registration_args(pooling_workspace)) == 0
    registered = last_json(capsys.readouterr().out)
    searches = FakeDenseIndex.searches
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main(review_args(pooling_workspace, str(registered["run_id"]))) == 0
    paused = last_json(capsys.readouterr().out)

    assert paused["status"] == "paused"
    assert paused["adjudicated_items"] == 0
    assert FakeDenseIndex.searches == searches


def test_pool_status_is_aggregate_only(
    pooling_workspace: dict[str, object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(registration_args(pooling_workspace)) == 0
    registered = last_json(capsys.readouterr().out)

    code = main(
        [
            "annotation",
            "pool-status",
            "--pool-dir",
            str(pooling_workspace["pool_dir"]),
            "--run-id",
            str(registered["run_id"]),
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    status = last_json(captured.out)
    assert status["registered_items"] == 2
    assert status["adjudicated_items"] == 0
    assert status["sealed"] is False
    assert "Which header" not in captured.out
    assert "Content-Location" not in captured.out


def test_annotation_progress_includes_the_sealed_pooling_audit(
    pooling_workspace: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(registration_args(pooling_workspace)) == 0
    registered = last_json(capsys.readouterr().out)
    run = PoolingStore(Path(pooling_workspace["pool_dir"])).read_run(
        str(registered["run_id"])
    )
    target = next(item for item in run.items if item.item_id == "l1-dev-010")
    selected = next(
        index
        for index, candidate in enumerate(target.candidates)
        if candidate.unit_id == pooling_workspace["obligation_id"]
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(f"complete\n{chr(ord('A') + selected)}\n"),
    )
    assert main(review_args(pooling_workspace, str(registered["run_id"]))) == 0
    capsys.readouterr()

    code = main(
        [
            "annotation",
            "progress",
            "--annotation-dir",
            str(pooling_workspace["annotation_dir"]),
            "--pool-dir",
            str(pooling_workspace["pool_dir"]),
        ]
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    progress = last_json(captured.out)
    assert progress["l1"]["awaiting_adjudication"] == 0
    assert progress["pooling_audit"] == {
        "registered_items": 2,
        "adjudicated_items": 2,
        "gold_complete": 1,
        "gold_extended": 1,
        "blocked": 0,
        "added_gold_clauses": 1,
        "fully_sealed": True,
        # Per run as well as in aggregate. With one run the two agree; the
        # breakdown exists because a grown gold set needs a second run and the
        # aggregate alone cannot say which run covered what.
        "runs": [
            {
                "run_id": registered["run_id"],
                "registered_items": 2,
                "adjudicated_items": 2,
                "gold_complete": 1,
                "gold_extended": 1,
                "blocked": 0,
                "added_gold_clauses": 1,
                "sealed": True,
            }
        ],
    }
    lowered = captured.out.lower()
    assert "recall" not in lowered
    assert "mrr" not in lowered
    assert "accuracy" not in lowered


def test_a_mistyped_block_does_not_end_the_run(
    pooling_workspace: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One wrong word at an interactive prompt used to be unrecoverable.

    `blocked` wrote a decision that could not be replaced, that prevented
    sealing, and whose item set could not register a second run. Resuming then
    fell through to `apply_decision` with the blocked decision and refused with
    `pooling_decision_not_applied` — so the run was wedged on its first item
    with nothing in the tool that could move it.
    """
    assert main(registration_args(pooling_workspace)) == 0
    registered = last_json(capsys.readouterr().out)
    run_id = str(registered["run_id"])

    monkeypatch.setattr("sys.stdin", io.StringIO("blocked\n"))
    assert main(review_args(pooling_workspace, run_id)) != 0
    assert "pooling_audit_blocked" in capsys.readouterr().err

    # The same reviewer, resuming, is re-presented the item rather than told
    # the run is broken.
    monkeypatch.setattr("sys.stdin", io.StringIO("complete\ncomplete\n"))
    assert main(review_args(pooling_workspace, run_id)) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "annotation",
                "progress",
                "--annotation-dir",
                str(pooling_workspace["annotation_dir"]),
                "--pool-dir",
                str(pooling_workspace["pool_dir"]),
            ]
        )
        == 0
    )
    audit = last_json(capsys.readouterr().out)["pooling_audit"]
    assert audit["blocked"] == 0
    assert audit["adjudicated_items"] == audit["registered_items"]
    assert audit["fully_sealed"] is True


def test_only_a_blocked_decision_may_be_superseded(
    pooling_workspace: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a reviewer could re-roll a judgement until they liked it,
    which is exactly what forced choice exists to prevent."""
    assert main(registration_args(pooling_workspace)) == 0
    registered = last_json(capsys.readouterr().out)
    run_id = str(registered["run_id"])
    store = PoolingStore(cast(Path, pooling_workspace["pool_dir"]))
    run = store.read_run(run_id)

    monkeypatch.setattr("sys.stdin", io.StringIO("complete\ncomplete\n"))
    assert main(review_args(pooling_workspace, run_id)) == 0
    capsys.readouterr()

    settled = head_decisions(store.read_decisions(run_id))[0]
    assert settled.outcome is PoolingOutcome.GOLD_COMPLETE
    replacement = PoolingDecision(
        run_id=run_id,
        item_id=settled.item_id,
        reviewed_annotation_id=settled.reviewed_annotation_id,
        outcome=PoolingOutcome.GOLD_EXTENDED,
        selected_unit_ids=(run.items[0].candidates[0].unit_id,),
        reviewer_id="chunxue",
        elapsed_seconds=1,
    )

    with pytest.raises(ValueError, match="only a blocked"):
        store.supersede_decision(settled, replacement, reviewer_id="chunxue")
