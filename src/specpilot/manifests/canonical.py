from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_json(model: BaseModel, *, include_manifest_id: bool = False) -> bytes:
    """Serialize a model to deterministic, normalized UTF-8 JSON."""
    excluded = None if include_manifest_id else {"manifest_id"}
    value = model.model_dump(mode="json", exclude=excluded)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(model: BaseModel) -> str:
    """Return the content ID for every model field except ``manifest_id``."""
    return hashlib.sha256(canonical_json(model)).hexdigest()
