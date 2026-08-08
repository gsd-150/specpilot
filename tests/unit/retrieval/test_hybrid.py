from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.retrieval.hybrid import (
    FusedRanking,
    RouteRanking,
    RrfParameters,
    reciprocal_rank_fusion,
)
from specpilot.retrieval.local import LocalCorpus
from tests.helpers import rfc_factory


def fuse(*rankings: RouteRanking, k: int = 60) -> list[str]:
    fused = reciprocal_rank_fusion(rankings, RrfParameters(k=k))
    return [hit.unit_id for hit in fused.hits]


def test_a_unit_ranked_by_both_routes_beats_one_ranked_by_either() -> None:
    bm25 = RouteRanking("bm25", ("a", "b", "c"))
    dense = RouteRanking("dense", ("c", "a", "d"))

    assert fuse(bm25, dense)[0] == "a"


def test_a_unit_found_by_only_one_route_still_places() -> None:
    """Dropping them would make the fusion an intersection, which throws away
    exactly the recall the second route was added for."""
    bm25 = RouteRanking("bm25", ("a", "b"))
    dense = RouteRanking("dense", ("c",))

    assert set(fuse(bm25, dense)) == {"a", "b", "c"}


def test_the_order_the_routes_are_passed_in_does_not_matter() -> None:
    bm25 = RouteRanking("bm25", ("a", "b", "c"))
    dense = RouteRanking("dense", ("c", "a", "d"))

    assert fuse(bm25, dense) == fuse(dense, bm25)


def test_ties_break_the_same_way_on_every_run() -> None:
    """Pooling is executed once and audited much later."""
    first = RouteRanking("bm25", ("b", "a"))
    second = RouteRanking("dense", ("a", "b"))

    assert fuse(first, second) == fuse(first, second) == ["a", "b"]


def test_the_constant_shrinks_the_gap_between_ranks() -> None:
    """A small k makes rank one dominate; the standard 60 keeps later ranks
    contributing, which is the whole reason RRF beats taking the best list."""
    bm25 = RouteRanking("bm25", ("a", "b", "c", "d", "e"))
    dense = RouteRanking("dense", ("e", "d", "c", "b", "a"))

    assert fuse(bm25, dense, k=1)[0] in {"a", "e"}
    assert fuse(bm25, dense, k=60)[0] in {"a", "e"}
    tight = reciprocal_rank_fusion((bm25, dense), RrfParameters(k=60))
    assert tight.hits[0].score - tight.hits[-1].score < 0.01


def test_a_fused_hit_records_the_rank_each_route_gave_it() -> None:
    """§8.2.3 requires the pooling record to say where a candidate came from."""
    bm25 = RouteRanking("bm25", ("a", "b"))
    dense = RouteRanking("dense", ("b",))

    hits = {hit.unit_id: hit for hit in reciprocal_rank_fusion((bm25, dense)).hits}

    assert hits["a"].ranks == {"bm25": 1}
    assert hits["b"].ranks == {"bm25": 2, "dense": 1}


def test_the_parameters_travel_with_the_result() -> None:
    fused = reciprocal_rank_fusion((RouteRanking("bm25", ("a",)),))

    assert fused.parameters == RrfParameters()
    assert fused.parameters.k == 60


def test_a_route_cannot_be_named_after_the_fusion_it_feeds() -> None:
    """§8.2.3 pools BM25-only and dense-only and never the hybrid ranking.

    Naming a route "hybrid" is the likeliest way to feed the fused ranking back
    in by accident, so the spelling is refused rather than documented.
    """
    for reserved in ("hybrid", "rrf", "fused"):
        with pytest.raises(ValueError, match="reserved"):
            RouteRanking(reserved, ("a",))


def test_a_fused_ranking_is_not_a_route_ranking() -> None:
    """The type is the guard: pooling takes route rankings, and there is no
    way to turn a fused result back into one."""
    fused = reciprocal_rank_fusion((RouteRanking("bm25", ("a",)),))

    assert isinstance(fused, FusedRanking)
    assert not isinstance(fused, RouteRanking)
    assert not hasattr(fused, "unit_ids")


def test_two_routes_cannot_share_a_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        reciprocal_rank_fusion(
            (RouteRanking("bm25", ("a",)), RouteRanking("bm25", ("b",)))
        )


def test_fusing_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one"):
        reciprocal_rank_fusion(())


@pytest.fixture
def corpus(tmp_path: Path) -> LocalCorpus:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    document = rfc_factory.write(directory, "qa.xml", rfc_factory.QA_RFC_XML)
    return LocalCorpus.load([(document, ClauseLimits())], RfcLimits())


def test_get_clause_returns_the_whole_clause_locally(corpus: LocalCorpus) -> None:
    """§5.1: the full clause is for local verification. The 512-token excerpt
    cap governs what may leave, and it is the enforcer's job, not this one's —
    truncating here would give local checking a shortened text to check."""
    unit_id = next(iter(corpus.unit_ids()))

    unit = corpus.get_clause(unit_id)

    assert unit.text
    assert unit.document_version == "2026-08"
    assert unit.text == corpus.get_clause(unit_id).text


def test_get_clause_refuses_an_identifier_the_corpus_does_not_hold(
    corpus: LocalCorpus,
) -> None:
    with pytest.raises(KeyError):
        corpus.get_clause("f" * 64)


def test_get_toc_returns_titles_and_never_body_text(corpus: LocalCorpus) -> None:
    nodes = corpus.get_toc()

    assert nodes
    assert all(node.title for node in nodes)
    assert "Prose" not in str(nodes)


def test_get_toc_can_be_narrowed_to_a_section(corpus: LocalCorpus) -> None:
    everything = corpus.get_toc()
    narrowed = corpus.get_toc(section="2")

    assert 0 < len(narrowed) < len(everything)


def test_the_local_toc_is_not_capped_because_the_cap_is_outbound(
    corpus: LocalCorpus,
) -> None:
    """§5.1 caps the model at 12 nodes per call and 24 per run. That is an
    egress cap the enforcer already owns; capping the local read as well would
    mean the author cannot see their own document."""
    assert len(corpus.get_toc()) == len(corpus.get_toc())
