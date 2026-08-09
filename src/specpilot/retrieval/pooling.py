"""One-time completeness pooling over independent retrieval routes.

Records in this module contain locators and hashes only. Source text is resolved
locally at review time and never becomes part of the durable audit log.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from specpilot.contracts.annotation import SectionPath
from specpilot.contracts.manifests import Identifier, Sha256
from specpilot.retrieval.hybrid import RouteRanking

_MAX_RECORD_BYTES = 512 * 1024
_ROUTES = frozenset({"bm25", "dense"})
_TOP_K = 5
RecordT = TypeVar("RecordT", bound=BaseModel)

SectionNumber = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _record_bytes(record: BaseModel, id_field: str) -> bytes:
    value = record.model_dump(mode="json", exclude={id_field})
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _record_id(record: BaseModel, id_field: str) -> str:
    return hashlib.sha256(_record_bytes(record, id_field)).hexdigest()


def _bind_id(record: BaseModel, id_field: str) -> None:
    expected = _record_id(record, id_field)
    actual = getattr(record, id_field)
    if actual is not None and actual != expected:
        raise ValueError(f"{id_field} does not match canonical content")
    object.__setattr__(record, id_field, expected)


class PoolingOutcome(StrEnum):
    GOLD_COMPLETE = "gold_complete"
    GOLD_EXTENDED = "gold_extended"
    AUDIT_BLOCKED = "audit_blocked"


class PoolingUnitFact(_FrozenModel):
    unit_id: Sha256
    document_id: Identifier
    document_version: Identifier
    section_number: SectionNumber | None
    section_path: SectionPath
    content_sha256: Sha256


class PoolingCandidate(PoolingUnitFact):
    route_ranks: dict[str, Annotated[int, Field(ge=1, le=_TOP_K)]]

    @model_validator(mode="after")
    def _known_routes_only(self) -> Self:
        if not self.route_ranks or set(self.route_ranks) - _ROUTES:
            raise ValueError("candidate routes must be bm25 and/or dense")
        return self


class PoolingItem(_FrozenModel):
    item_id: Identifier
    annotation_id: Sha256
    candidates: tuple[PoolingCandidate, ...]

    @model_validator(mode="after")
    def _candidates_are_unique(self) -> Self:
        identifiers = [candidate.unit_id for candidate in self.candidates]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("a pooling candidate is listed twice")
        return self


class PoolingRun(_FrozenModel):
    schema_version: Literal["pooling-run/v1"] = "pooling-run/v1"
    source_manifest_ids: tuple[Sha256, ...]
    bm25_fingerprint: Sha256
    dense_collection: Identifier
    embedding_weights_sha256: Sha256
    vector_size: Annotated[int, Field(gt=0)]
    point_count: Annotated[int, Field(gt=0)]
    top_k: Literal[5] = 5
    items: tuple[PoolingItem, ...]
    author_id: Identifier
    created_at: datetime
    run_id: Sha256 | None = None

    @model_validator(mode="after")
    def _validate_run(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        item_ids = [item.item_id for item in self.items]
        if not item_ids:
            raise ValueError("a pooling run needs at least one item")
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("a pooling item is listed twice")
        if len(set(self.source_manifest_ids)) != len(self.source_manifest_ids):
            raise ValueError("a source manifest is listed twice")
        _bind_id(self, "run_id")
        return self


class PoolingDecision(_FrozenModel):
    schema_version: Literal["pooling-decision/v1"] = "pooling-decision/v1"
    run_id: Sha256
    item_id: Identifier
    reviewed_annotation_id: Sha256
    outcome: PoolingOutcome
    selected_unit_ids: tuple[Sha256, ...] = ()
    reviewer_id: Identifier
    elapsed_seconds: Annotated[int, Field(ge=0)]
    decision_id: Sha256 | None = None

    @model_validator(mode="after")
    def _outcome_matches_selection(self) -> Self:
        extended = self.outcome is PoolingOutcome.GOLD_EXTENDED
        if extended and not self.selected_unit_ids:
            raise ValueError("gold_extended requires at least one selected candidate")
        if not extended and self.selected_unit_ids:
            raise ValueError("only gold_extended may select candidates")
        if len(set(self.selected_unit_ids)) != len(self.selected_unit_ids):
            raise ValueError("a selected candidate is listed twice")
        _bind_id(self, "decision_id")
        return self


class PoolingApplication(_FrozenModel):
    schema_version: Literal["pooling-application/v1"] = "pooling-application/v1"
    decision_id: Sha256
    successor_annotation_id: Sha256
    application_id: Sha256 | None = None

    @model_validator(mode="after")
    def _verify_id(self) -> Self:
        _bind_id(self, "application_id")
        return self


class PoolingSeal(_FrozenModel):
    schema_version: Literal["pooling-seal/v1"] = "pooling-seal/v1"
    run_id: Sha256
    decision_ids: tuple[Sha256, ...]
    application_ids: tuple[Sha256, ...]
    sealed_at: datetime
    seal_id: Sha256 | None = None

    @model_validator(mode="after")
    def _verify_id(self) -> Self:
        object.__setattr__(self, "sealed_at", self.sealed_at.astimezone(UTC))
        _bind_id(self, "seal_id")
        return self


def build_pool(
    *rankings: RouteRanking,
    units: Mapping[str, PoolingUnitFact],
) -> tuple[PoolingCandidate, ...]:
    """Build the ordered union of BM25-only and dense-only top five."""
    if any(not isinstance(ranking, RouteRanking) for ranking in rankings):
        raise TypeError("pooling inputs must be RouteRanking values")
    if len(rankings) != 2 or {ranking.route for ranking in rankings} != _ROUTES:
        raise ValueError("pooling requires exactly bm25 and dense rankings")
    if any(len(ranking.unit_ids) > _TOP_K for ranking in rankings):
        raise ValueError("pooling accepts only each route's top 5")

    by_route = {ranking.route: ranking for ranking in rankings}
    ordered_ids = tuple(
        dict.fromkeys(
            (*by_route["bm25"].unit_ids, *by_route["dense"].unit_ids)
        )
    )
    candidates: list[PoolingCandidate] = []
    for unit_id in ordered_ids:
        fact = units.get(unit_id)
        if fact is None:
            raise ValueError(f"ranking names unknown unit {unit_id!r}")
        ranks = {
            route: ranking.unit_ids.index(unit_id) + 1
            for route, ranking in by_route.items()
            if unit_id in ranking.unit_ids
        }
        candidates.append(
            PoolingCandidate(
                **fact.model_dump(),
                route_ranks=dict(sorted(ranks.items())),
            )
        )
    return tuple(candidates)


def seal_run(
    run: PoolingRun,
    *,
    decisions: Sequence[PoolingDecision],
    applications: Sequence[PoolingApplication],
    sealed_at: datetime | None = None,
) -> PoolingSeal:
    """Seal only a complete, unblocked, fully applied run."""
    registered = {item.item_id: item for item in run.items}
    by_item: dict[str, PoolingDecision] = {}
    for review in decisions:
        if review.run_id != run.run_id or review.item_id not in registered:
            raise ValueError("decision does not belong to this run")
        item = registered[review.item_id]
        if review.reviewed_annotation_id != item.annotation_id:
            raise ValueError("decision names a stale annotation head")
        if review.item_id in by_item:
            raise ValueError("an item has more than one pooling decision")
        if review.outcome is PoolingOutcome.AUDIT_BLOCKED:
            raise ValueError("a blocked pooling decision prevents sealing")
        allowed = {candidate.unit_id for candidate in item.candidates}
        if set(review.selected_unit_ids) - allowed:
            raise ValueError("decision selects an unregistered candidate")
        by_item[review.item_id] = review

    missing = set(registered) - set(by_item)
    if missing:
        raise ValueError("pooling run has unadjudicated items")

    by_decision = {applied.decision_id: applied for applied in applications}
    decision_ids = tuple(cast(str, review.decision_id) for review in decisions)
    if set(decision_ids) - set(by_decision):
        raise ValueError("pooling run has unapplied decisions")
    if set(by_decision) - set(decision_ids):
        raise ValueError("application does not belong to a run decision")

    return PoolingSeal(
        run_id=cast(str, run.run_id),
        decision_ids=decision_ids,
        application_ids=tuple(
            cast(str, by_decision[item].application_id) for item in decision_ids
        ),
        sealed_at=sealed_at or datetime.now(UTC),
    )


class PoolingStore:
    """Private create-only storage for pooling run records."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create_run(self, run: PoolingRun) -> PoolingRun:
        runs = self._directory / "runs"
        self._prepare_directory(runs)
        wanted_items = tuple(item.item_id for item in run.items)
        for path in sorted(runs.glob("*.json")):
            existing = self.read_run(path.stem)
            if tuple(item.item_id for item in existing.items) != wanted_items:
                continue
            if existing.run_id != run.run_id:
                raise ValueError("that item set already registered a different run")
            return existing
        run_id = cast(str, run.run_id)
        self._write(runs / f"{run_id}.json", _record_bytes(run, "run_id"))
        return self.read_run(run_id)

    def read_run(self, run_id: str) -> PoolingRun:
        return self._read(
            self._directory / "runs" / f"{run_id}.json",
            PoolingRun,
            "run_id",
            run_id,
            "pooling run",
        )

    def _prepare_directory(self, child: Path) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        child.mkdir(parents=True, exist_ok=True)
        child.chmod(0o700)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("stored pooling record differs from replay")
            return
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read(
        path: Path,
        model: type[RecordT],
        id_field: str,
        record_id: str,
        noun: str,
    ) -> RecordT:
        data = path.read_bytes()
        if len(data) > _MAX_RECORD_BYTES:
            raise ValueError(f"stored {noun} exceeds the maximum record size")
        try:
            parsed = json.loads(
                data,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant {value}")
                ),
            )
            record = model.model_validate(parsed)
        except (json.JSONDecodeError, ValueError) as error:
            if "does not match canonical content" in str(error):
                raise ValueError(
                    f"stored {noun} ID does not match its content"
                ) from error
            raise ValueError(f"stored {noun} is invalid") from error
        if getattr(record, id_field) != record_id:
            raise ValueError(f"stored {noun} ID does not match its content")
        return record


__all__ = [
    "PoolingApplication",
    "PoolingCandidate",
    "PoolingDecision",
    "PoolingItem",
    "PoolingOutcome",
    "PoolingRun",
    "PoolingSeal",
    "PoolingStore",
    "PoolingUnitFact",
    "build_pool",
    "seal_run",
]
