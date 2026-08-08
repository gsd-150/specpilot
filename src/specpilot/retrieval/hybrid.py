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

from collections.abc import Sequence
from dataclasses import dataclass, field

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


@dataclass(frozen=True, slots=True)
class FusedRanking:
    """Deliberately not a `RouteRanking`, and deliberately without `unit_ids`."""

    hits: tuple[FusedHit, ...]
    parameters: RrfParameters


def reciprocal_rank_fusion(
    rankings: Sequence[RouteRanking],
    parameters: RrfParameters | None = None,
) -> FusedRanking:
    """Fuse route rankings by reciprocal rank."""
    if not rankings:
        raise ValueError("fusion needs at least one ranking")
    settings = parameters or RrfParameters()

    seen: set[str] = set()
    for ranking in rankings:
        if ranking.route in seen:
            raise ValueError(f"duplicate route {ranking.route!r}")
        seen.add(ranking.route)

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for ranking in rankings:
        for position, unit_id in enumerate(ranking.unit_ids, start=1):
            scores[unit_id] = scores.get(unit_id, 0.0) + 1.0 / (settings.k + position)
            ranks.setdefault(unit_id, {})[ranking.route] = position

    # Sorted by identifier as well as score so equal scores order identically
    # on every run; a pooling log is read long after it is written.
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return FusedRanking(
        hits=tuple(
            FusedHit(unit_id=unit_id, score=score, ranks=ranks[unit_id])
            for unit_id, score in ordered
        ),
        parameters=settings,
    )
