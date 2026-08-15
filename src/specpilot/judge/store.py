"""Content-addressed stores for judge records and human dev labels.

Both live under `artifacts/restricted/judge/` — gitignored, 0700, write-once
per content — so a record or a label set is either the bytes it was first
written as, or it fails to read. That is the property the freeze leans on:
the evidence file lists content hashes, and a hash must name bytes that still
exist and still parse.

Content addressing is idempotent by construction: publishing the same record
twice links the same bytes and returns the same id, while a changed record is
a new id — old calibration numbers stay attached to the old prompt and the old
record instead of being silently overwritten (§8.3.2 keeps every old prompt
and its numbers).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from specpilot.contracts.manifests import Sha256
from specpilot.contracts.scoring import HumanDevLabels, JudgeRecord
from specpilot.manifests._secure_records import SecureRecordDirectory
from specpilot.manifests.canonical import canonical_json, canonical_sha256

_MAX_RECORD_BYTES = 256 * 1024

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _ContentAddressedStore:
    def __init__(self, directory: Path, model: type[_ModelT]) -> None:
        self._directory = directory
        self._model = model

    def _publish(self, record: BaseModel) -> str:
        data = canonical_json(record)
        record_id = canonical_sha256(record)
        with SecureRecordDirectory.open(self._directory, create=True) as records:
            stored = records.publish(
                f"{record_id}.json", data, max_bytes=_MAX_RECORD_BYTES
            )
        if stored != data:
            raise ValueError("stored record bytes changed during publication")
        return record_id

    def _read(self, record_id: Sha256) -> BaseModel:
        with SecureRecordDirectory.open(self._directory, create=False) as records:
            data = records.read(f"{record_id}.json", max_bytes=_MAX_RECORD_BYTES)
        try:
            record = self._model.model_validate_json(data)
        except ValueError as error:
            raise ValueError("stored judge record is invalid") from error
        if canonical_json(record) != data:
            raise ValueError("stored record bytes do not match their content")
        return record

    def _iter_ids(self) -> tuple[str, ...]:
        with SecureRecordDirectory.open(self._directory, create=False) as records:
            record_ids = records.content_ids()
        for record_id in record_ids:
            self._read(record_id)
        return record_ids


class JudgeRecordStore(_ContentAddressedStore):
    """One content-addressed JSON record per judge call, write-once."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory, JudgeRecord)

    def create(self, record: JudgeRecord) -> str:
        return self._publish(record)

    def read(self, record_id: Sha256) -> JudgeRecord:
        record = self._read(record_id)
        assert isinstance(record, JudgeRecord)
        return record

    def record_ids(self) -> tuple[str, ...]:
        return self._iter_ids()

    def iter_records(self) -> Iterator[JudgeRecord]:
        for record_id in self.record_ids():
            yield self.read(record_id)


class HumanLabelStore(_ContentAddressedStore):
    """One content-addressed JSON label set per dev case, write-once."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory, HumanDevLabels)

    def create(self, labels: HumanDevLabels) -> str:
        return self._publish(labels)

    def read(self, label_id: Sha256) -> HumanDevLabels:
        labels = self._read(label_id)
        assert isinstance(labels, HumanDevLabels)
        return labels

    def label_ids(self) -> tuple[str, ...]:
        return self._iter_ids()

    def iter_labels(self) -> Iterator[HumanDevLabels]:
        for label_id in self.label_ids():
            yield self.read(label_id)
