"""Reciprocal rank fusion over the two independent routes.

RRF combines rankings without needing their scores to be comparable, which
matters here because BM25 returns an unbounded term-weighted sum and the dense
route returns a cosine in [-1, 1]. Normalizing those onto a shared scale would
require deciding what a BM25 score of 21 is worth against a cosine of 0.77, and
nothing in this project can answer that. Ranks are comparable by construction.

**A fused ranking may never feed pooling.** §8.2.3 pools BM25-only and
dense-only top-5 precisely so the pool is not shaped by the system whose recall
is being measured against it. That is enforced by the types rather than by a
comment: pooling takes `RouteRanking`, fusion returns `FusedRanking`, and there
is no way to turn one back into the other. The route names that would most
plausibly be used to smuggle it back — "hybrid", "rrf", "fused" — are refused
outright.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from specpilot.retrieval.protocol import RetrievalLocator

# Reserved because they name the fusion rather than a route into it. §8.2.3's
# argument fails the moment the hybrid ranking enters the pool.
_FUSION_NAMES = frozenset({"hybrid", "rrf", "fused", "fusion"})


@dataclass(frozen=True, slots=True)
class RrfParameters:
    """Frozen with the index, like BM25's. §6.4 binds it to the manifest.

    `k` damps the advantage of rank one. At k=1 the top of either list wins
    outright and the fusion degenerates into "trust whichever route was more
    confident"; at 60, the standard value, ranks well down each list still
    contribute, which is the behaviour RRF is chosen for.
    """

    k: int = 60

    def __post_init__(self) -> None:
        if self.k <= 0:
            raise ValueError("k must be positive")


@dataclass(frozen=True, slots=True)
class RouteRanking:
    """One route's ordered result. The only thing pooling accepts."""

    route: str
    unit_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.route:
            raise ValueError("a ranking must name its route")
        if self.route.lower() in _FUSION_NAMES:
            raise ValueError(f"route name {self.route!r} is reserved for the fusion")


@dataclass(frozen=True, slots=True)
class FusedHit:
    unit_id: str
    score: float
    # Which route placed it where. §8.2.3 requires a candidate's origin to be
    # recorded, and a fused score alone cannot say where it came from.
    ranks: dict[str, int] = field(default_factory=dict)
    locator: RetrievalLocator = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class FusedRanking:
    """Deliberately not a `RouteRanking`, and deliberately without `unit_ids`."""

    hits: tuple[FusedHit, ...]
    parameters: RrfParameters


def reciprocal_rank_fusion(
    rankings: Sequence[RouteRanking],
    *,
    locators: Mapping[str, RetrievalLocator],
    parameters: RrfParameters | None = None,
) -> FusedRanking:
    """Fuse route rankings by reciprocal rank."""
    if not rankings:
        raise ValueError("fusion needs at least one ranking")
    settings = parameters or RrfParameters()

    if len({ranking.route for ranking in rankings}) != len(rankings):
        raise ValueError("fusion routes must be unique; duplicate route")
    wanted = {unit_id for ranking in rankings for unit_id in ranking.unit_ids}
    if wanted - set(locators):
        raise ValueError("fusion candidate has no retrieval locator")
    if wanted and len(
        {locators[unit_id].corpus_manifest_id for unit_id in wanted}
    ) != 1:
        raise ValueError("fusion candidates cross corpus manifests")

    grouped: dict[tuple[object, ...], dict[str, tuple[int, str]]] = {}
    identity_locator: dict[tuple[object, ...], RetrievalLocator] = {}
    tie_owner: dict[tuple[object, ...], tuple[object, ...]] = {}
    for ranking in rankings:
        for rank, unit_id in enumerate(ranking.unit_ids, start=1):
            locator = locators[unit_id]
            identity = locator.dedupe_key
            previous = identity_locator.setdefault(identity, locator)
            if previous != locator:
                raise ValueError("one identity has conflicting locators")
            owner = tie_owner.setdefault(locator.stable_tie_key, identity)
            if owner != identity:
                raise ValueError("two identities share one stable tie key")
            grouped.setdefault(identity, {}).setdefault(
                ranking.route, (rank, unit_id)
            )

    hits: list[FusedHit] = []
    for identity, by_route in grouped.items():
        score = math.fsum(
            1.0 / (settings.k + by_route[route][0])
            for route in sorted(by_route)
        )
        hits.append(
            FusedHit(
                unit_id=min(value[1] for value in by_route.values()),
                score=score,
                ranks={
                    route: value[0]
                    for route, value in sorted(by_route.items())
                },
                locator=identity_locator[identity],
            )
        )
    hits.sort(key=lambda hit: (-hit.score, *hit.locator.stable_tie_key))
    return FusedRanking(hits=tuple(hits), parameters=settings)
