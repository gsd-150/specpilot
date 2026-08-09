"""Deterministic attestations for the local corpus and complete dense index.

Only aggregate roots leave this module.  Individual source, locator, and
vector hashes remain transient so a corpus manifest cannot become an inventory
of restricted material.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from specpilot.corpus.indexable import IndexUnit
from specpilot.retrieval.dense import (
    VECTOR_SIZE,
    DenseRecord,
    point_id_for_unit,
    point_payload,
)

_LOCATOR_KEYS = frozenset(
    (
        "unit_id",
        "kind",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
    )
)


@dataclass(frozen=True, slots=True)
class DenseInventoryEvidence:
    """The only dense inventory values safe to persist in a manifest."""

    point_count: int
    inventory_root_sha256: str


def canonical_mapping_bytes(value: object) -> bytes:
    """Encode a JSON-compatible value with the manifest canonical settings."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_mapping_sha256(value: object) -> str:
    return hashlib.sha256(canonical_mapping_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique_units(units: Iterable[IndexUnit]) -> dict[str, IndexUnit]:
    by_id: dict[str, IndexUnit] = {}
    for unit in units:
        if unit.unit_id in by_id:
            raise ValueError("dense inventory has a duplicate local unit ID")
        by_id[unit.unit_id] = unit
    return by_id


def derived_corpus_sha256(units: Iterable[IndexUnit]) -> str:
    """Hash unit identity and indexed text independently of input order."""
    by_id = _unique_units(units)
    lines = [
        f"{unit_id}\x1f{hashlib.sha256(by_id[unit_id].indexed.encode('utf-8')).hexdigest()}"
        for unit_id in sorted(by_id)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def vector_sha256(vector: Sequence[float]) -> str:
    """Hash exactly 1,024 finite little-endian IEEE-754 float32 values."""
    try:
        invalid = (
            not isinstance(vector, Sequence)
            or isinstance(vector, (str, bytes, bytearray))
            or len(vector) != VECTOR_SIZE
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for value in vector
            )
        )
    except OverflowError as error:
        raise ValueError("dense vector cannot be represented as float32") from error
    if invalid:
        raise ValueError("dense vector is not finite and 1024-dimensional")
    try:
        encoded = struct.pack(f"<{VECTOR_SIZE}f", *vector)
    except (OverflowError, struct.error) as error:
        raise ValueError("dense vector cannot be represented as float32") from error
    return hashlib.sha256(encoded).hexdigest()


def _payload_unit_id(payload: object) -> str:
    if not isinstance(payload, Mapping) or set(payload) != _LOCATOR_KEYS:
        raise ValueError("dense point payload is not the exact locator payload")
    unit_id = payload.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id.strip():
        raise ValueError("dense point payload has an invalid unit ID")
    return unit_id


def build_dense_inventory(
    units: Iterable[IndexUnit], records: Iterable[DenseRecord]
) -> DenseInventoryEvidence:
    """Match all live points to local units and return one aggregate root."""
    local_by_id = _unique_units(units)
    record_by_unit_id: dict[str, DenseRecord] = {}
    point_ids: set[int | str] = set()
    for record in records:
        if type(record.point_id) not in (int, str):
            raise ValueError("dense point ID is invalid")
        if record.point_id in point_ids:
            raise ValueError("dense inventory has a duplicate point ID")
        unit_id = _payload_unit_id(record.payload)
        if unit_id in record_by_unit_id:
            raise ValueError("dense inventory has a duplicate payload unit ID")
        point_ids.add(record.point_id)
        record_by_unit_id[unit_id] = record

    if set(local_by_id) != set(record_by_unit_id):
        raise ValueError("dense point set does not match the local corpus")

    entries: list[dict[str, object]] = []
    for unit_id in sorted(local_by_id):
        unit = local_by_id[unit_id]
        record = record_by_unit_id[unit_id]
        if record.point_id != point_id_for_unit(unit_id):
            raise ValueError("dense point ID does not match its deterministic ID")
        expected_payload = point_payload(unit)
        if record.payload != expected_payload:
            raise ValueError("dense point payload does not match the local locator")
        entries.append(
            {
                "point_id": str(record.point_id),
                "unit_id": unit_id,
                "locator_payload_sha256": canonical_mapping_sha256(record.payload),
                "source_text_sha256": sha256_text(unit.text),
                "indexed_text_sha256": sha256_text(unit.indexed),
                "dense_vector_sha256": vector_sha256(record.vector),
            }
        )

    return DenseInventoryEvidence(
        point_count=len(entries),
        inventory_root_sha256=canonical_mapping_sha256(entries),
    )
