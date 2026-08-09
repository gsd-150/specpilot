"""Create-only, content-addressed storage for forced-choice review decisions.

Kept beside the annotation store rather than inside it. An annotation is what
the item is — its question, its gold, its key points — and its content ID is a
hash over exactly that. A review is a later judgement about the item by a
different actor, so binding it into the annotation's identity would mean the
same item has two IDs depending on whether anyone has looked at it yet.

Two consequences follow, and both are wanted. Records written before review
existed stay byte-identical and readable. And a re-review is an additional
record rather than an edit, so a change of mind leaves both decisions behind
instead of overwriting the first.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from specpilot.contracts.annotation import ReviewDecision
from specpilot.manifests.canonical import canonical_json, canonical_sha256

_MAX_RECORD_BYTES = 16 * 1024


class ReviewStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create(self, decision: ReviewDecision) -> ReviewDecision:
        review_id = canonical_sha256(decision)
        self._write(review_id, decision)
        return self.read(review_id)

    def read(self, review_id: str) -> ReviewDecision:
        path = self._directory / f"{review_id}.json"
        data = path.read_bytes()
        if len(data) > _MAX_RECORD_BYTES:
            raise ValueError("stored review exceeds the maximum record size")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("stored review is invalid")
        if parsed.get("schema_version") != "annotation-review/v1":
            raise ValueError("unsupported review schema")
        try:
            record = ReviewDecision.model_validate_json(data)
        except ValidationError as error:
            raise ValueError("stored review is invalid") from error
        if canonical_sha256(record) != review_id:
            raise ValueError("stored review ID does not match its content")
        return record.model_copy(update={"review_id": review_id})

    def read_all(self) -> tuple[ReviewDecision, ...]:
        return tuple(self._iter_records())

    def for_annotation(self, annotation_id: str) -> tuple[ReviewDecision, ...]:
        """Every decision recorded about one annotation, in stored order.

        More than one is not an error. A second review of the same item is a
        real event — the reviewer went back — and both belong in the audit
        trail, so the caller decides which is current rather than the store
        silently picking.
        """
        return tuple(
            record
            for record in self._iter_records()
            if record.reviewed_annotation_id == annotation_id
        )

    def _iter_records(self) -> Iterator[ReviewDecision]:
        if not self._directory.exists():
            return
        for path in sorted(self._directory.glob("*.json")):
            yield self.read(path.stem)

    def _write(self, review_id: str, decision: ReviewDecision) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        path = self._directory / f"{review_id}.json"
        data = canonical_json(decision)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("stored review differs from the replayed record")
            return
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["ReviewStore"]
