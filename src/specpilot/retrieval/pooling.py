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

from specpilot.annotation.store import Annotation, AnnotationStore
from specpilot.contracts.annotation import GoldOrigin, GoldOriginEvent, SectionPath
from specpilot.contracts.manifests import Identifier, Sha256
from specpilot.corpus.overlap import question_gold_jaccard
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


def inventory_sha256(unit_ids: Sequence[str]) -> str:
    """Bind a route to the exact sorted set of units it can retrieve."""
    return hashlib.sha256("\n".join(sorted(unit_ids)).encode("utf-8")).hexdigest()


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
    dense_inventory_sha256: Sha256
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

    def read_runs(self) -> tuple[PoolingRun, ...]:
        directory = self._directory / "runs"
        if not directory.is_dir():
            return ()
        return tuple(
            self.read_run(path.stem)
            for path in sorted(directory.glob("*.json"))
        )

    def create_decision(self, decision: PoolingDecision) -> PoolingDecision:
        directory = self._scoped_directory("decisions", decision.run_id)
        for path in sorted(directory.glob("*.json")):
            existing = self._read(
                path,
                PoolingDecision,
                "decision_id",
                path.stem,
                "pooling decision",
            )
            if existing.item_id != decision.item_id:
                continue
            if existing.decision_id != decision.decision_id:
                raise ValueError("that item already owns a different decision")
            return existing
        decision_id = cast(str, decision.decision_id)
        self._write(
            directory / f"{decision_id}.json",
            _record_bytes(decision, "decision_id"),
        )
        return self._read(
            directory / f"{decision_id}.json",
            PoolingDecision,
            "decision_id",
            decision_id,
            "pooling decision",
        )

    def read_decisions(self, run_id: str) -> tuple[PoolingDecision, ...]:
        directory = self._directory / "decisions" / run_id
        if not directory.is_dir():
            return ()
        return tuple(
            self._read(
                path,
                PoolingDecision,
                "decision_id",
                path.stem,
                "pooling decision",
            )
            for path in sorted(directory.glob("*.json"))
        )

    def create_application(
        self,
        run_id: str,
        application: PoolingApplication,
    ) -> PoolingApplication:
        decisions = {item.decision_id for item in self.read_decisions(run_id)}
        if application.decision_id not in decisions:
            raise ValueError("application names an unknown pooling decision")
        directory = self._scoped_directory("applications", run_id)
        for path in sorted(directory.glob("*.json")):
            existing = self._read(
                path,
                PoolingApplication,
                "application_id",
                path.stem,
                "pooling application",
            )
            if existing.decision_id != application.decision_id:
                continue
            if existing.application_id != application.application_id:
                raise ValueError("that decision already owns a different application")
            return existing
        application_id = cast(str, application.application_id)
        self._write(
            directory / f"{application_id}.json",
            _record_bytes(application, "application_id"),
        )
        return self._read(
            directory / f"{application_id}.json",
            PoolingApplication,
            "application_id",
            application_id,
            "pooling application",
        )

    def read_applications(self, run_id: str) -> tuple[PoolingApplication, ...]:
        directory = self._directory / "applications" / run_id
        if not directory.is_dir():
            return ()
        return tuple(
            self._read(
                path,
                PoolingApplication,
                "application_id",
                path.stem,
                "pooling application",
            )
            for path in sorted(directory.glob("*.json"))
        )

    def create_seal(self, seal: PoolingSeal) -> PoolingSeal:
        directory = self._scoped_directory("seals", seal.run_id)
        existing = tuple(directory.glob("*.json"))
        if existing:
            stored = self._read(
                existing[0],
                PoolingSeal,
                "seal_id",
                existing[0].stem,
                "pooling seal",
            )
            if stored.seal_id != seal.seal_id:
                raise ValueError("that run already owns a different seal")
            return stored
        seal_id = cast(str, seal.seal_id)
        self._write(
            directory / f"{seal_id}.json",
            _record_bytes(seal, "seal_id"),
        )
        return self._read(
            directory / f"{seal_id}.json",
            PoolingSeal,
            "seal_id",
            seal_id,
            "pooling seal",
        )

    def read_seals(self, run_id: str) -> tuple[PoolingSeal, ...]:
        directory = self._directory / "seals" / run_id
        if not directory.is_dir():
            return ()
        return tuple(
            self._read(
                path,
                PoolingSeal,
                "seal_id",
                path.stem,
                "pooling seal",
            )
            for path in sorted(directory.glob("*.json"))
        )

    def _prepare_directory(self, child: Path) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        child.mkdir(parents=True, exist_ok=True)
        child.chmod(0o700)

    def _scoped_directory(self, category: str, run_id: str) -> Path:
        category_path = self._directory / category
        self._prepare_directory(category_path)
        scoped = category_path / run_id
        scoped.mkdir(parents=False, exist_ok=True)
        scoped.chmod(0o700)
        return scoped

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


def apply_decision(
    pool_store: PoolingStore,
    annotation_store: AnnotationStore,
    run: PoolingRun,
    decision: PoolingDecision,
    *,
    unit_texts: Mapping[str, str],
) -> Annotation:
    """Apply one human decision as an add-only annotation successor."""
    if decision.run_id != run.run_id:
        raise ValueError("decision does not belong to this run")
    registered = {item.item_id: item for item in run.items}
    item = registered.get(decision.item_id)
    if item is None:
        raise ValueError("decision names an unregistered item")
    if decision.reviewed_annotation_id != item.annotation_id:
        raise ValueError("decision names a stale annotation head")
    if decision.outcome is PoolingOutcome.AUDIT_BLOCKED:
        raise ValueError("a blocked decision cannot be applied")

    previous = annotation_store.read(decision.reviewed_annotation_id)
    if previous.item_id != decision.item_id:
        raise ValueError("annotation head belongs to another item")
    candidates = {candidate.unit_id: candidate for candidate in item.candidates}
    if set(decision.selected_unit_ids) - set(candidates):
        raise ValueError("decision selects an unregistered candidate")
    if set(decision.selected_unit_ids) & set(previous.gold_clause_ids):
        raise ValueError("selected gold is already established")

    selected = tuple(candidates[unit_id] for unit_id in decision.selected_unit_ids)
    for candidate in selected:
        text = unit_texts.get(candidate.unit_id)
        if text is None:
            raise ValueError("selected candidate text is unavailable")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != candidate.content_sha256:
            raise ValueError("selected candidate content hash changed")

    overlap = previous.question_gold_jaccard
    origins: list[GoldOriginEvent] = []
    if selected:
        gold_texts: list[str] = []
        for unit_id in (*previous.gold_clause_ids, *decision.selected_unit_ids):
            text = unit_texts.get(unit_id)
            if text is None:
                raise ValueError("gold clause text is unavailable")
            gold_texts.append(text)
        overlap = question_gold_jaccard(previous.question, gold_texts)
        routes = {route for candidate in selected for route in candidate.route_ranks}
        if "bm25" in routes:
            origins.append(
                GoldOriginEvent(
                    origin=GoldOrigin.BM25_RETRIEVAL,
                    producer=run.bm25_fingerprint,
                )
            )
        if "dense" in routes:
            origins.append(
                GoldOriginEvent(
                    origin=GoldOrigin.DENSE_RETRIEVAL,
                    producer=run.dense_collection,
                )
            )
        origins.append(GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW))

    stored_decision = pool_store.create_decision(decision)
    successor = annotation_store.amend(
        decision.reviewed_annotation_id,
        added_gold_clause_ids=decision.selected_unit_ids,
        added_gold_section_paths=tuple(
            candidate.section_path for candidate in selected
        ),
        added_gold_origins=tuple(origins),
        adjudication=(
            "pooling audit found additional gold"
            if selected
            else "pooling audit confirmed the existing gold"
        ),
        question_gold_jaccard=overlap,
    )
    pool_store.create_application(
        run.run_id,
        PoolingApplication(
            decision_id=cast(str, stored_decision.decision_id),
            successor_annotation_id=cast(str, successor.annotation_id),
        ),
    )
    return successor


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
    "apply_decision",
    "build_pool",
    "seal_run",
]
