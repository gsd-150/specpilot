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
from pathlib import Path

from pydantic import ValidationError

from specpilot.contracts.annotation import (
    Adjudication,
    GoldOriginEvent,
    L1Annotation,
    L2Annotation,
    annotation_model_for_schema,
)
from specpilot.manifests.canonical import canonical_json, canonical_sha256

type Annotation = L1Annotation | L2Annotation

_MAX_RECORD_BYTES = 64 * 1024


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
