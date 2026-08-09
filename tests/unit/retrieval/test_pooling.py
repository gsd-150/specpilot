from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specpilot.retrieval.hybrid import FusedRanking, RouteRanking, RrfParameters
from specpilot.retrieval.pooling import (
    PoolingApplication,
    PoolingDecision,
    PoolingItem,
    PoolingOutcome,
    PoolingRun,
    PoolingStore,
    PoolingUnitFact,
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
                annotation_id=digest(annotation),
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
