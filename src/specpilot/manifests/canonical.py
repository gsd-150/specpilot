from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

# Fields that hold a record's own content ID. They are excluded from the bytes
# the ID is computed from, or the hash would have to contain itself. Excluding
# a name a model does not declare is a no-op, so listing both is safe.
_CONTENT_ID_FIELDS = {"manifest_id", "annotation_id"}


def canonical_json(model: BaseModel, *, include_manifest_id: bool = False) -> bytes:
    """Serialize a model to deterministic, normalized UTF-8 JSON."""
    excluded = None if include_manifest_id else set(_CONTENT_ID_FIELDS)
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
