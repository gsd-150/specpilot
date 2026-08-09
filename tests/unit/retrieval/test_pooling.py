from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specpilot.annotation.store import AnnotationStore
from specpilot.contracts.annotation import GoldOriginEvent, L1Annotation
from specpilot.retrieval.hybrid import FusedRanking, RouteRanking, RrfParameters
from specpilot.retrieval.pooling import (
    PoolingApplication,
    PoolingDecision,
    PoolingItem,
    PoolingOutcome,
    PoolingRun,
    PoolingStore,
    PoolingUnitFact,
    apply_decision,
    build_pool,
    seal_run,
)


def digest(character: str) -> str:
    return hashlib.sha256(character.encode()).hexdigest()


def unit(character: str, section: str) -> PoolingUnitFact:
    return PoolingUnitFact(
        unit_id=digest(character),
        document_id="rfc9110",
        document_version="2022-06",
        section_number=section,
        section_path=f"HTTP Semantics > {section}",
        content_sha256=digest(f"content:{character}"),
    )


def sample_pool() -> tuple:
    facts = {
        fact.unit_id: fact
        for fact in (unit("a", "1"), unit("b", "2"), unit("c", "3"), unit("d", "4"))
    }
    return build_pool(
        RouteRanking(route="bm25", unit_ids=(digest("a"), digest("b"), digest("c"))),
        RouteRanking(route="dense", unit_ids=(digest("b"), digest("d"))),
        units=facts,
    )


def sample_run(*, item_id: str = "l1-dev-001", annotation: str = "1") -> PoolingRun:
    return PoolingRun(
        source_manifest_ids=(digest("e"), digest("f")),
        bm25_fingerprint=digest("2"),
        dense_collection="specpilot_0123456789abcdef",
        embedding_weights_sha256=digest("3"),
        vector_size=1024,
        point_count=1922,
        items=(
            PoolingItem(
                item_id=item_id,
                annotation_id=(
                    annotation if len(annotation) == 64 else digest(annotation)
                ),
                candidates=sample_pool(),
            ),
        ),
        author_id="chunxue",
        created_at=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
    )


def decision(run: PoolingRun, outcome: PoolingOutcome) -> PoolingDecision:
    selected = (
        (run.items[0].candidates[0].unit_id,)
        if outcome is PoolingOutcome.GOLD_EXTENDED
        else ()
    )
    return PoolingDecision(
        run_id=run.run_id,
        item_id=run.items[0].item_id,
        reviewed_annotation_id=run.items[0].annotation_id,
        outcome=outcome,
        selected_unit_ids=selected,
        reviewer_id="chunxue",
        elapsed_seconds=12,
    )


def application(review: PoolingDecision) -> PoolingApplication:
    return PoolingApplication(
        decision_id=review.decision_id,
        successor_annotation_id=digest("9"),
    )


def test_pool_is_the_ordered_union_of_independent_top_five_routes() -> None:
    pool = sample_pool()

    assert tuple(candidate.unit_id for candidate in pool) == (
        digest("a"),
        digest("b"),
        digest("c"),
        digest("d"),
    )
    assert pool[1].route_ranks == {"bm25": 2, "dense": 1}


def test_each_route_is_limited_to_the_preregistered_top_five() -> None:
    facts = {digest(str(index)): unit(str(index), str(index)) for index in range(6)}
    with pytest.raises(ValueError, match="top 5"):
        build_pool(
            RouteRanking(route="bm25", unit_ids=tuple(facts)),
            RouteRanking(route="dense", unit_ids=()),
            units=facts,
        )


@pytest.mark.parametrize(
    "routes",
    [
        (RouteRanking(route="bm25", unit_ids=()),),
        (
            RouteRanking(route="bm25", unit_ids=()),
            RouteRanking(route="sparse", unit_ids=()),
        ),
    ],
)
def test_pool_requires_exactly_bm25_and_dense(routes: tuple[RouteRanking, ...]) -> None:
    with pytest.raises(ValueError, match="bm25 and dense"):
        build_pool(*routes, units={})


def test_fused_ranking_is_not_a_pooling_input() -> None:
    fused = FusedRanking(hits=(), parameters=RrfParameters())
    with pytest.raises(TypeError, match="RouteRanking"):
        build_pool(
            RouteRanking(route="bm25", unit_ids=()),
            fused,
            units={},
        )


def test_unknown_units_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown unit"):
        build_pool(
            RouteRanking(route="bm25", unit_ids=(digest("a"),)),
            RouteRanking(route="dense", unit_ids=()),
            units={},
        )


def test_candidate_records_have_no_field_that_can_hold_source_text() -> None:
    serialized = sample_pool()[0].model_dump()
    assert set(serialized) == {
        "unit_id",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
        "content_sha256",
        "route_ranks",
    }


def test_run_cannot_seal_with_an_unadjudicated_item() -> None:
    with pytest.raises(ValueError, match="unadjudicated"):
        seal_run(sample_run(), decisions=(), applications=())


def test_blocked_decision_prevents_sealing() -> None:
    run = sample_run()
    blocked = decision(run, PoolingOutcome.AUDIT_BLOCKED)
    with pytest.raises(ValueError, match="blocked"):
        seal_run(run, decisions=(blocked,), applications=())


def test_an_unapplied_decision_prevents_sealing() -> None:
    run = sample_run()
    complete = decision(run, PoolingOutcome.GOLD_COMPLETE)
    with pytest.raises(ValueError, match="unapplied"):
        seal_run(run, decisions=(complete,), applications=())


def test_a_fully_applied_run_seals_with_content_ids() -> None:
    run = sample_run()
    complete = decision(run, PoolingOutcome.GOLD_COMPLETE)
    applied = application(complete)

    sealed = seal_run(run, decisions=(complete,), applications=(applied,))

    assert sealed.run_id == run.run_id
    assert sealed.decision_ids == (complete.decision_id,)
    assert sealed.application_ids == (applied.application_id,)
    assert sealed.seal_id is not None


def test_registration_is_create_only_and_safe_to_replay(tmp_path: Path) -> None:
    store = PoolingStore(tmp_path)
    original = store.create_run(sample_run())

    assert store.create_run(sample_run()) == original
    changed = sample_run(item_id="l1-dev-001", annotation="4")
    with pytest.raises(ValueError, match="already registered"):
        store.create_run(changed)


def test_store_uses_private_permissions(tmp_path: Path) -> None:
    stored = PoolingStore(tmp_path / "pool").create_run(sample_run())
    path = tmp_path / "pool" / "runs" / f"{stored.run_id}.json"

    assert stat.S_IMODE((tmp_path / "pool").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "pool" / "runs").stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_tampered_run_is_refused(tmp_path: Path) -> None:
    store = PoolingStore(tmp_path)
    stored = store.create_run(sample_run())
    path = tmp_path / "runs" / f"{stored.run_id}.json"
    payload = json.loads(path.read_text())
    payload["author_id"] = "somebody-else"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="ID does not match"):
        store.read_run(stored.run_id)


def annotation(**overrides: object) -> L1Annotation:
    values: dict[str, object] = {
        "item_id": "l1-dev-001",
        "split": "dev",
        "question": "Which alpha and beta condition applies?",
        "direction": "clause_first",
        "content_origin": "model",
        "label_origin": "mixed",
        "document_id": "rfc9110",
        "document_version": "2022-06",
        "gold_clause_ids": (digest("existing-gold"),),
        "gold_section_paths": ("HTTP Semantics > Existing",),
        "key_points": ({"point_id": "kp-1", "criterion": "names the condition"},),
        "expected_refusal": False,
        "question_gold_jaccard": 0.0,
        "gold_origins": (
            {"origin": "model_proposal", "producer": "draft-model"},
            {"origin": "human_source_review"},
        ),
    }
    return L1Annotation(**{**values, **overrides})


def application_fixture(tmp_path: Path) -> tuple[
    PoolingStore,
    AnnotationStore,
    PoolingRun,
    L1Annotation,
    str,
]:
    annotations = AnnotationStore(tmp_path / "annotations")
    root = annotations.create(annotation())
    selected_text = "alpha beta gamma"
    selected = PoolingUnitFact(
        unit_id=digest("selected"),
        document_id="rfc9110",
        document_version="2022-06",
        section_number="15.4.5",
        section_path="HTTP Semantics > 15.4.5",
        content_sha256=hashlib.sha256(selected_text.encode()).hexdigest(),
    )
    candidates = build_pool(
        RouteRanking(route="bm25", unit_ids=(selected.unit_id,)),
        RouteRanking(route="dense", unit_ids=(selected.unit_id,)),
        units={selected.unit_id: selected},
    )
    run = PoolingStore(tmp_path / "pool").create_run(
        PoolingRun(
            source_manifest_ids=(digest("manifest"),),
            bm25_fingerprint=digest("bm25"),
            dense_collection="specpilot_fixture",
            embedding_weights_sha256=digest("weights"),
            vector_size=1024,
            point_count=1,
            items=(
                PoolingItem(
                    item_id=root.item_id,
                    annotation_id=root.annotation_id,
                    candidates=candidates,
                ),
            ),
            author_id="chunxue",
            created_at=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
        )
    )
    return PoolingStore(tmp_path / "pool"), annotations, run, root, selected_text


def test_gold_complete_creates_an_adjudication_only_successor(tmp_path: Path) -> None:
    pool_store, annotations, run, root, selected_text = application_fixture(tmp_path)
    review = decision(run, PoolingOutcome.GOLD_COMPLETE)

    successor = apply_decision(
        pool_store,
        annotations,
        run,
        review,
        unit_texts={
            root.gold_clause_ids[0]: "unrelated existing clause",
            run.items[0].candidates[0].unit_id: selected_text,
        },
    )

    assert successor.gold_clause_ids == root.gold_clause_ids
    assert successor.predecessor_annotation_id == root.annotation_id
    assert successor.adjudications[-1].candidate_origin == "pooling"
    assert len(pool_store.read_decisions(run.run_id)) == 1
    assert len(pool_store.read_applications(run.run_id)) == 1


def test_gold_extension_is_add_only_and_recomputes_overlap(tmp_path: Path) -> None:
    pool_store, annotations, run, root, selected_text = application_fixture(tmp_path)
    review = decision(run, PoolingOutcome.GOLD_EXTENDED).model_copy(
        update={"selected_unit_ids": (run.items[0].candidates[0].unit_id,)}
    )
    review = PoolingDecision.model_validate(review.model_dump(exclude={"decision_id"}))

    successor = apply_decision(
        pool_store,
        annotations,
        run,
        review,
        unit_texts={
            root.gold_clause_ids[0]: "unrelated existing clause",
            run.items[0].candidates[0].unit_id: selected_text,
        },
    )

    assert successor.gold_clause_ids == (
        root.gold_clause_ids[0],
        run.items[0].candidates[0].unit_id,
    )
    assert successor.question_gold_jaccard == pytest.approx(2 / 7)
    assert successor.gold_origins[-3:] == (
        GoldOriginEvent(origin="bm25_retrieval", producer=run.bm25_fingerprint),
        GoldOriginEvent(origin="dense_retrieval", producer=run.dense_collection),
        GoldOriginEvent(origin="human_source_review"),
    )


def test_an_extension_cannot_select_existing_gold(tmp_path: Path) -> None:
    pool_store, annotations, run, root, selected_text = application_fixture(tmp_path)
    candidate = run.items[0].candidates[0]
    rooted = annotations.create(
        annotation(
            item_id="l1-dev-002",
            gold_clause_ids=(candidate.unit_id,),
            gold_section_paths=(candidate.section_path,),
        )
    )
    changed_run = PoolingRun(
        **run.model_dump(
            exclude={"run_id", "items"},
        ),
        items=(
            PoolingItem(
                item_id=rooted.item_id,
                annotation_id=rooted.annotation_id,
                candidates=(candidate,),
            ),
        ),
    )
    changed_store = PoolingStore(tmp_path / "other-pool")
    changed_run = changed_store.create_run(changed_run)
    review = decision(changed_run, PoolingOutcome.GOLD_EXTENDED)

    with pytest.raises(ValueError, match="already established"):
        apply_decision(
            changed_store,
            annotations,
            changed_run,
            review,
            unit_texts={candidate.unit_id: selected_text},
        )

    assert changed_store.read_decisions(changed_run.run_id) == ()
