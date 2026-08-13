"""Create-only, content-addressed annotation storage.

Follows the discipline the manifest store established: canonical bytes, an ID
derived from them, private files, and no silent overwrite. Amendments are
successors rather than mutations, so the completeness audit leaves a chain a
reviewer can walk instead of a record that quietly changed shape.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    model_validator,
)

from specpilot.contracts.annotation import (
    Adjudication,
    GoldOriginEvent,
    L1Annotation,
    L2Annotation,
    annotation_model_for_schema,
)
from specpilot.contracts.manifests import Identifier, Sha256
from specpilot.manifests.canonical import canonical_json, canonical_sha256

type Annotation = L1Annotation | L2Annotation

_MAX_RECORD_BYTES = 64 * 1024
_RETIREMENTS = "retirements"


class AnnotationRetirement(BaseModel):
    """One item removed from the evaluation set, without removing its record.

    **Not a field on the annotation.** Records here are content-addressed, so a
    new field changes the ID of every record already stored — the same reason
    `PoolingSupersession` is its own record. And retirement is an act with its
    own author, moment and reason; it is not a property the item was born with.

    Retirement is for an item that is *defective as a question*, which no
    amendment can repair: `amend` copies the question verbatim and there is
    deliberately no way to change it, because the forced choice, the gold and
    the deep read were all performed against the question as written. Swapping
    the question would leave a provenance chain claiming a human adjudicated
    something they never saw.

    The record stays readable forever. What changes is only that progress stops
    counting it toward the target and the pooling audit stops waiting on it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["annotation-retirement/v1"] = "annotation-retirement/v1"
    item_id: Identifier
    retired_annotation_id: Sha256
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=8, max_length=512)
    ]
    author_id: Identifier
    retired_at: datetime
    retirement_id: Sha256 | None = None

    @model_validator(mode="after")
    def _verify(self) -> Self:
        if self.retired_at.tzinfo is None or self.retired_at.utcoffset() is None:
            raise ValueError("retired_at must be timezone-aware")
        object.__setattr__(self, "retired_at", self.retired_at.astimezone(UTC))
        expected = canonical_sha256(self)
        if self.retirement_id is not None and self.retirement_id != expected:
            raise ValueError("retirement_id does not match canonical content")
        object.__setattr__(self, "retirement_id", expected)
        return self


class GoldRemovalError(ValueError):
    """An amendment tried to drop gold that a previous adjudication established.

    Section 8.2.3 lets pooling propose and the author adjudicate, but existing
    gold is never deleted — a shrinking gold set would quietly raise every
    recall figure computed against it.
    """


class AnnotationStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create(self, record: Annotation) -> Annotation:
        annotation_id = canonical_sha256(record)
        existing = self._find_by_item_id(record.item_id)
        if existing is not None and canonical_sha256(existing) != annotation_id:
            raise ValueError(
                f"item_id {record.item_id!r} already owns a different annotation"
            )
        self._write(annotation_id, record)
        return self.read(annotation_id)

    def amend(
        self,
        annotation_id: str,
        *,
        added_gold_clause_ids: tuple[str, ...],
        added_gold_section_paths: tuple[str, ...],
        added_gold_origins: tuple[GoldOriginEvent, ...],
        adjudication: str,
        question_gold_jaccard: float | None = None,
        removed_gold_clause_ids: tuple[str, ...] = (),
    ) -> Annotation:
        previous = self.read(annotation_id)
        if removed_gold_clause_ids:
            raise GoldRemovalError("an amendment may not remove established gold")
        if (
            added_gold_clause_ids or added_gold_section_paths
        ) and not added_gold_origins:
            raise ValueError("adding gold requires at least one gold origin")

        merged_ids = tuple(
            dict.fromkeys((*previous.gold_clause_ids, *added_gold_clause_ids))
        )
        merged_paths = tuple(
            dict.fromkeys((*previous.gold_section_paths, *added_gold_section_paths))
        )
        if set(previous.gold_clause_ids) - set(merged_ids):
            raise GoldRemovalError("an amendment may not remove established gold")

        model = annotation_model_for_schema(previous.schema_version)
        successor = model.model_validate(
            {
                **previous.model_dump(exclude={"annotation_id"}),
                "gold_clause_ids": merged_ids,
                "gold_section_paths": merged_paths,
                "gold_origins": (*previous.gold_origins, *added_gold_origins),
                "question_gold_jaccard": (
                    previous.question_gold_jaccard
                    if question_gold_jaccard is None
                    else question_gold_jaccard
                ),
                "predecessor_annotation_id": annotation_id,
                "adjudications": (
                    *previous.adjudications,
                    Adjudication(candidate_origin="pooling", note=adjudication),
                ),
            }
        )
        successor_id = canonical_sha256(successor)
        self._write(successor_id, successor)
        return self.read(successor_id)

    def retire(
        self,
        annotation_id: str,
        *,
        reason: str,
        author_id: str,
        retired_at: datetime | None = None,
    ) -> AnnotationRetirement:
        """Take one item out of the evaluation set, keeping every record.

        ``annotation_id`` must be the item's current head, so a caller working
        from a stale view is refused rather than retiring an item whose defect
        someone has since amended away.
        """
        record = self.read(annotation_id)
        heads = {
            head.item_id: head.annotation_id
            for head in _heads(self.iter_records())
        }
        if heads.get(record.item_id) != annotation_id:
            raise ValueError("retirement does not name the item's current head")
        if record.item_id in {entry.item_id for entry in self.read_retirements()}:
            raise ValueError("that item is already retired")

        retirement = AnnotationRetirement(
            item_id=record.item_id,
            retired_annotation_id=annotation_id,
            reason=reason,
            author_id=author_id,
            retired_at=retired_at or datetime.now(UTC),
        )
        directory = self._directory / _RETIREMENTS
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        path = directory / f"{retirement.retirement_id}.json"
        data = canonical_json(retirement)
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return retirement

    def read_retirements(self) -> tuple[AnnotationRetirement, ...]:
        directory = self._directory / _RETIREMENTS
        if not directory.is_dir():
            return ()
        entries = []
        for path in sorted(directory.glob("*.json")):
            data = path.read_bytes()
            if len(data) > _MAX_RECORD_BYTES:
                raise ValueError("stored retirement exceeds the maximum record size")
            entry = AnnotationRetirement.model_validate_json(data)
            if entry.retirement_id != path.stem:
                raise ValueError("stored retirement ID does not match its content")
            entries.append(entry)
        return tuple(entries)

    def read(self, annotation_id: str) -> Annotation:
        path = self._directory / f"{annotation_id}.json"
        data = path.read_bytes()
        if len(data) > _MAX_RECORD_BYTES:
            raise ValueError("stored annotation exceeds the maximum record size")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("stored annotation is invalid")
        model = annotation_model_for_schema(parsed.get("schema_version"))
        try:
            record = model.model_validate_json(data)
        except ValidationError as error:
            raise ValueError("stored annotation is invalid") from error
        if canonical_sha256(record) != annotation_id:
            raise ValueError("stored annotation ID does not match its content")
        return record.model_copy(update={"annotation_id": annotation_id})

    def iter_records(self) -> Iterator[Annotation]:
        """Yield every stored record, each verified against its own content ID.

        Successors are yielded alongside their predecessors: this is the whole
        store, not one record per item. Callers that count items resolve the
        chains themselves.
        """
        if not self._directory.exists():
            return
        for path in sorted(self._directory.glob("*.json")):
            yield self.read(path.stem)

    def _find_by_item_id(self, item_id: str) -> Annotation | None:
        for candidate in self.iter_records():
            is_root = candidate.predecessor_annotation_id is None
            if candidate.item_id == item_id and is_root:
                return candidate
        return None

    def _write(self, annotation_id: str, record: Annotation) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        path = self._directory / f"{annotation_id}.json"
        data = canonical_json(record)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("stored annotation differs from the replayed record")
            return
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _heads(records: Iterator[Annotation]) -> tuple[Annotation, ...]:
    """The current record for each item: the one nothing supersedes."""
    stored = tuple(records)
    superseded = {
        record.predecessor_annotation_id
        for record in stored
        if record.predecessor_annotation_id is not None
    }
    return tuple(
        record for record in stored if record.annotation_id not in superseded
    )
