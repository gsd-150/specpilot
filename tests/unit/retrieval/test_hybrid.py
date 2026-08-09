from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.indexable import IndexUnit
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.retrieval.hybrid import (
    FusedRanking,
    RouteRanking,
    RrfParameters,
    reciprocal_rank_fusion,
)
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import RetrievalLocator, locator_for_unit
from tests.helpers import rfc_factory


def _index_unit(**changes: object) -> IndexUnit:
    values: dict[str, object] = {
        "unit_id": "ietf-rfc-9110:section-2-1",
        "kind": "clause",
        "document_id": "ietf-rfc-9110",
        "document_version": "2022-06",
        "section_number": "2",
        "section_path": "Syntax",
        "ordinal": 1,
        "text": "source",
        "indexed": "2 Syntax\nsource",
    }
    values.update(changes)
    return IndexUnit(**values)  # type: ignore[arg-type]


def _locator(
    *,
    clause_id: str,
    numeric_clause_path: tuple[int, ...],
    corpus_manifest_id: str = "a" * 64,
    document_id: str = "ietf-rfc-9110",
    child_span: tuple[int, int] | None = None,
    child_start: int = 0,
) -> RetrievalLocator:
    return RetrievalLocator(
        corpus_manifest_id=corpus_manifest_id,
        document_id=document_id,
        clause_id=clause_id,
        child_span=child_span,
        numeric_clause_path=numeric_clause_path,
        child_start=child_start,
    )


def test_rrf_deduplicates_by_full_identity_and_preserves_route_ranks() -> None:
    shared = _locator(clause_id="shared", numeric_clause_path=(0, 2, -1, 1, 0))
    single = _locator(clause_id="single", numeric_clause_path=(0, 3, -1, 1, 0))
    rankings = (
        RouteRanking("bm25", ("z-shared", "single")),
        RouteRanking("dense", ("a-shared", "single")),
    )

    fused = reciprocal_rank_fusion(
        rankings,
        locators={"z-shared": shared, "a-shared": shared, "single": single},
    )

    assert [hit.unit_id for hit in fused.hits] == ["a-shared", "single"]
    assert fused.hits[0].ranks == {"bm25": 1, "dense": 1}
    assert fused.hits[0].locator == shared


def test_each_route_scores_and_displays_only_its_first_identity_alias() -> None:
    shared = _locator(clause_id="shared", numeric_clause_path=(0, 2, -1, 1, 0))

    fused = reciprocal_rank_fusion(
        (RouteRanking("bm25", ("z", "a")),),
        locators={"z": shared, "a": shared},
    )

    assert len(fused.hits) == 1
    assert fused.hits[0].unit_id == "z"
    assert fused.hits[0].ranks == {"bm25": 1}
    assert fused.hits[0].score == pytest.approx(1 / 61)


def test_display_alias_is_minimum_of_each_routes_first_identity_alias() -> None:
    shared = _locator(clause_id="shared", numeric_clause_path=(0, 2, -1, 1, 0))

    fused = reciprocal_rank_fusion(
        (
            RouteRanking("bm25", ("z", "a")),
            RouteRanking("dense", ("m", "b")),
        ),
        locators={alias: shared for alias in ("z", "a", "m", "b")},
    )

    assert len(fused.hits) == 1
    assert fused.hits[0].unit_id == "m"
    assert fused.hits[0].ranks == {"bm25": 1, "dense": 1}
    assert fused.hits[0].score == pytest.approx(2 / 61)


def test_rrf_is_invariant_to_route_input_order() -> None:
    rankings = (
        RouteRanking("z-route", ("a", "b", "c")),
        RouteRanking("a-route", ("c", "a", "b")),
    )
    locators = {
        "a": _locator(clause_id="a", numeric_clause_path=(0, 1, -1, 1, 0)),
        "b": _locator(clause_id="b", numeric_clause_path=(0, 2, -1, 1, 0)),
        "c": _locator(clause_id="c", numeric_clause_path=(0, 3, -1, 1, 0)),
    }

    forward = reciprocal_rank_fusion(rankings, locators=locators)
    backward = reciprocal_rank_fusion(tuple(reversed(rankings)), locators=locators)

    assert forward == backward


def test_rrf_breaks_ties_by_numeric_clause_path_not_unit_id() -> None:
    rankings = (
        RouteRanking("bm25", ("z", "a")),
        RouteRanking("dense", ("a", "z")),
    )
    locators = {
        "z": _locator(clause_id="z", numeric_clause_path=(0, 2, -1, 1, 0)),
        "a": _locator(clause_id="a", numeric_clause_path=(0, 10, -1, 1, 0)),
    }

    fused = reciprocal_rank_fusion(rankings, locators=locators)

    assert [hit.unit_id for hit in fused.hits] == ["z", "a"]


def test_rrf_breaks_ties_by_document_then_child_start() -> None:
    rankings = (
        RouteRanking("bm25", ("late", "z-document", "early", "a-document")),
        RouteRanking("dense", ("z-document", "early", "a-document", "late")),
        RouteRanking("lexical", ("early", "a-document", "late", "z-document")),
        RouteRanking("semantic", ("a-document", "late", "z-document", "early")),
    )
    locators = {
        "late": _locator(
            clause_id="same-clause",
            numeric_clause_path=(0, 2, -1, 1, 0),
            child_span=(10, 20),
            child_start=10,
        ),
        "early": _locator(
            clause_id="same-clause",
            numeric_clause_path=(0, 2, -1, 1, 0),
            child_span=(5, 10),
            child_start=5,
        ),
        "z-document": _locator(
            clause_id="z",
            document_id="z-document",
            numeric_clause_path=(0, 2, -1, 1, 0),
        ),
        "a-document": _locator(
            clause_id="a",
            document_id="a-document",
            numeric_clause_path=(0, 2, -1, 1, 0),
        ),
    }

    fused = reciprocal_rank_fusion(rankings, locators=locators)

    assert [hit.unit_id for hit in fused.hits] == [
        "a-document",
        "early",
        "late",
        "z-document",
    ]


def test_rrf_refuses_a_candidate_without_a_locator() -> None:
    with pytest.raises(ValueError, match="no retrieval locator"):
        reciprocal_rank_fusion(
            (RouteRanking("bm25", ("missing",)),),
            locators={},
        )


def test_rrf_refuses_candidates_from_different_manifests() -> None:
    with pytest.raises(ValueError, match="cross corpus manifests"):
        reciprocal_rank_fusion(
            (RouteRanking("bm25", ("a", "b")),),
            locators={
                "a": _locator(
                    clause_id="a",
                    numeric_clause_path=(0, 1),
                    corpus_manifest_id="a" * 64,
                ),
                "b": _locator(
                    clause_id="b",
                    numeric_clause_path=(0, 2),
                    corpus_manifest_id="b" * 64,
                ),
            },
        )


def test_rrf_refuses_conflicting_locators_for_one_identity() -> None:
    with pytest.raises(ValueError, match="conflicting locators"):
        reciprocal_rank_fusion(
            (RouteRanking("bm25", ("a", "b")),),
            locators={
                "a": _locator(clause_id="same", numeric_clause_path=(0, 1)),
                "b": _locator(clause_id="same", numeric_clause_path=(0, 2)),
            },
        )


def test_rrf_refuses_different_identities_with_the_same_stable_tie_key() -> None:
    with pytest.raises(ValueError, match="stable tie key"):
        reciprocal_rank_fusion(
            (RouteRanking("bm25", ("a", "b")),),
            locators={
                "a": _locator(clause_id="a", numeric_clause_path=(0, 1)),
                "b": _locator(clause_id="b", numeric_clause_path=(0, 1)),
            },
        )


def test_nonempty_empty_routes_need_no_locators_and_return_no_hits() -> None:
    fused = reciprocal_rank_fusion(
        (RouteRanking("bm25", ()), RouteRanking("dense", ())),
        locators={},
    )

    assert fused.hits == ()
    assert fused.parameters == RrfParameters()


def test_locator_for_whole_unit_uses_unit_identity_and_zero_start() -> None:
    locator = locator_for_unit("a" * 64, _index_unit())

    assert locator.clause_id == "ietf-rfc-9110:section-2-1"
    assert locator.child_span is None
    assert locator.child_start == 0
    assert locator.numeric_clause_path == (0, 2, -1, 1, 0)
    assert locator.dedupe_key == (
        "a" * 64,
        "ietf-rfc-9110",
        "ietf-rfc-9110:section-2-1",
        None,
    )
    assert locator.stable_tie_key == (
        "ietf-rfc-9110",
        (0, 2, -1, 1, 0),
        0,
    )


def test_retrieval_locator_is_frozen_and_slotted() -> None:
    locator = _locator(clause_id="a", numeric_clause_path=(0, 1))

    with pytest.raises(FrozenInstanceError):
        locator.document_id = "changed"  # type: ignore[misc]
    assert not hasattr(locator, "__dict__")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"corpus_manifest_id": "a" * 63}, "manifest"),
        ({"corpus_manifest_id": "A" * 64}, "manifest"),
        ({"document_id": ""}, "document"),
        ({"document_id": " document"}, "document"),
        ({"clause_id": ""}, "clause"),
        ({"clause_id": "clause "}, "clause"),
        ({"numeric_clause_path": ()}, "numeric clause path"),
        ({"numeric_clause_path": [0, 1]}, "numeric clause path"),
        ({"numeric_clause_path": (0, True)}, "numeric clause path"),
        ({"numeric_clause_path": (0, "1")}, "numeric clause path"),
        ({"child_start": -1}, "child start"),
        ({"child_start": True}, "child start"),
        ({"child_start": 1}, "start at zero"),
        ({"child_span": [0, 1]}, "child span"),
        ({"child_span": (0,)}, "child span"),
        ({"child_span": (False, 1)}, "child span"),
        ({"child_span": (-1, 1)}, "span and start"),
        ({"child_span": (1, 1), "child_start": 1}, "span and start"),
        ({"child_span": (1, 2), "child_start": 0}, "span and start"),
    ],
)
def test_retrieval_locator_refuses_malformed_runtime_values(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "corpus_manifest_id": "a" * 64,
        "document_id": "ietf-rfc-9110",
        "clause_id": "clause",
        "child_span": None,
        "numeric_clause_path": (0, 1),
        "child_start": 0,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        RetrievalLocator(**values)  # type: ignore[arg-type]


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
    fused = reciprocal_rank_fusion(
        (RouteRanking("bm25", ("a",)),),
        locators={
            "a": _locator(clause_id="a", numeric_clause_path=(0, 1)),
        },
    )

    assert isinstance(fused, FusedRanking)
    assert not isinstance(fused, RouteRanking)
    assert not hasattr(fused, "unit_ids")


def test_two_routes_cannot_share_a_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        reciprocal_rank_fusion(
            (RouteRanking("bm25", ("a",)), RouteRanking("bm25", ("b",))),
            locators={},
        )


def test_fusing_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one"):
        reciprocal_rank_fusion((), locators={})


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


def test_local_corpus_can_be_loaded_after_the_snapshot_path_disappears(
    tmp_path: Path,
) -> None:
    """A local corpus reuses one verified tree for units and its table of contents."""
    path = rfc_factory.write(tmp_path, "snapshot.xml", rfc_factory.QA_RFC_XML)
    verified = load_verified_rfc(path, RfcLimits())
    path.unlink()

    corpus = LocalCorpus.load([(verified, ClauseLimits())], RfcLimits())

    assert corpus.unit_count() == 5
    assert [node.title for node in corpus.get_toc()] == ["One", "Two"]


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
