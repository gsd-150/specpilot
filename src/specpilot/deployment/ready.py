"""Secure create-only ready state bound to one frozen corpus identity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from specpilot.manifests._secure_records import SecureRecordDirectory

if TYPE_CHECKING:
    from specpilot.contracts.corpus_manifest import CorpusManifest

_MAX_MARKER_BYTES = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class ReadyMarker(BaseModel):
    """The complete public identity required before a service may be healthy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ready-marker/v1"] = "ready-marker/v1"
    source_manifest_ids: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    corpus_manifest_id: Sha256
    collection_name: Annotated[
        str,
        StringConstraints(
            strict=True,
            min_length=1,
            max_length=255,
            pattern=r"^[A-Za-z0-9._-]+$",
        ),
    ]
    point_count: Annotated[int, Field(strict=True, gt=0)]
    inventory_root_sha256: Sha256
    mode: Literal["fixture", "real"]
    ready_id: Sha256

    @classmethod
    def create(
        cls,
        *,
        source_manifest_ids: tuple[str, ...],
        corpus_manifest_id: str,
        collection_name: str,
        point_count: int,
        inventory_root_sha256: str,
        mode: Literal["fixture", "real"],
    ) -> ReadyMarker:
        payload: dict[str, object] = {
            "schema_version": "ready-marker/v1",
            "source_manifest_ids": source_manifest_ids,
            "corpus_manifest_id": corpus_manifest_id,
            "collection_name": collection_name,
            "point_count": point_count,
            "inventory_root_sha256": inventory_root_sha256,
            "mode": mode,
        }
        ready_id = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        return cls.model_validate({**payload, "ready_id": ready_id})

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        if len(set(self.source_manifest_ids)) != len(self.source_manifest_ids):
            raise ValueError("ready marker source manifests must be unique")
        if tuple(sorted(self.source_manifest_ids)) != self.source_manifest_ids:
            raise ValueError("ready marker source manifests must be canonical")
        payload = self.model_dump(mode="json", exclude={"ready_id"})
        expected = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if self.ready_id != expected:
            raise ValueError("ready marker ID does not match canonical content")
        return self


class ReadyMarkerStore:
    """A single-value secure directory; a new binding is never an in-place rewrite."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def publish(self, marker: ReadyMarker) -> ReadyMarker:
        data = _canonical_bytes(marker.model_dump(mode="json"))
        if len(data) > _MAX_MARKER_BYTES:
            raise ValueError("ready marker exceeds maximum storage size")
        with SecureRecordDirectory.open(self._directory, create=True) as records:
            existing = records.content_ids()
            if existing and existing != (marker.ready_id,):
                raise FileExistsError(self._directory)
            stored = records.publish(
                f"{marker.ready_id}.json",
                data,
                max_bytes=_MAX_MARKER_BYTES,
            )
        decoded = self._decode(stored, marker.ready_id)
        if decoded != marker:
            raise FileExistsError(self._directory / f"{marker.ready_id}.json")
        return decoded

    def read(self) -> ReadyMarker:
        with SecureRecordDirectory.open(self._directory, create=False) as records:
            identifiers = records.content_ids()
            if len(identifiers) != 1:
                raise FileNotFoundError(self._directory)
            ready_id = identifiers[0]
            data = records.read(
                f"{ready_id}.json",
                max_bytes=_MAX_MARKER_BYTES,
            )
        return self._decode(data, ready_id)

    def require(self, expected: ReadyMarker) -> ReadyMarker:
        actual = self.read()
        if actual != expected:
            raise ValueError("ready marker identity mismatch")
        return actual

    def require_id(self, ready_id: str) -> ReadyMarker:
        if _SHA256.fullmatch(ready_id) is None:
            raise ValueError("ready marker identity is invalid")
        actual = self.read()
        if actual.ready_id != ready_id:
            raise ValueError("ready marker identity mismatch")
        return actual

    @staticmethod
    def _decode(data: bytes, expected_id: str) -> ReadyMarker:
        marker = ReadyMarker.model_validate_json(data)
        if marker.ready_id != expected_id:
            raise ValueError("ready marker ID does not match its filename")
        if data != _canonical_bytes(marker.model_dump(mode="json")):
            raise ValueError("ready marker is not canonical JSON")
        return marker


def require_ready_corpus(
    *,
    ready_dir: Path,
    ready_id: str,
    corpus: CorpusManifest,
    source_manifest_ids: tuple[str, ...],
    mode: Literal["fixture", "real"] | None = None,
) -> ReadyMarker:
    """Require one marker equal to every configured frozen corpus identity."""
    marker = ReadyMarkerStore(ready_dir).require_id(ready_id)
    if mode is not None and marker.mode != mode:
        raise ValueError("ready marker mode mismatch")
    if marker.source_manifest_ids != source_manifest_ids:
        raise ValueError("ready marker source identity mismatch")
    if (
        marker.corpus_manifest_id != corpus.manifest_id
        or marker.collection_name != corpus.collection_name
        or marker.point_count != corpus.point_count
        or marker.inventory_root_sha256 != corpus.inventory_root_sha256
    ):
        raise ValueError("ready marker corpus identity mismatch")
    return marker
