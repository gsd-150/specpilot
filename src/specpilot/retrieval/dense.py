"""The dense retrieval route: a versioned Qdrant collection.

The collection's name is derived from the things that would invalidate it — the
corpus hash, the chunking version, and the index-text policy. That makes a
rebuilt index a different collection rather than an in-place overwrite, so a
service pinned to one manifest cannot silently start reading vectors built from
a different split of the same document.

**The payload is locators only.** It comes back on every hit, so §8.1's
committable-field rule applies to it exactly as it applies to an annotation
record: unit id, kind, document, version, section number, section path. Never
the text. A retriever that returned clause prose in its payload would have moved
the corpus into every trace and every log that records a search.

Freezing is enforced on the write path rather than in a comment. §6.4 says
ingestion loses write access once the manifest is sealed and serving is
read-only from then on; a late upsert would change what the manifest attests to
while leaving its hash unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

# BGE-M3's dense width. A vector of any other size is a bug upstream, and
# reshaping it here would index a document nothing can ever match.
VECTOR_SIZE = 1024

_NAME_PREFIX = "specpilot"
_NAME_DIGEST_CHARS = 32


class CollectionFrozenError(RuntimeError):
    """A write was attempted against a collection the manifest has sealed."""


def collection_name(
    corpus_sha256: str, pipeline_version: str, index_text_version: str
) -> str:
    """Derive a collection name from everything that would invalidate it.

    Hashed rather than concatenated because Qdrant names allow a narrow
    alphabet and the inputs contain slashes and dots. Two versions that differ
    anywhere produce different names, which is the property that matters.
    """
    joined = "\x1f".join((corpus_sha256, pipeline_version, index_text_version))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"{_NAME_PREFIX}_{digest[:_NAME_DIGEST_CHARS]}"


def point_payload(unit: Any) -> dict[str, Any]:
    """Locators only. Never `text`, and never `indexed`."""
    return {
        "unit_id": unit.unit_id,
        "kind": unit.kind,
        "document_id": unit.document_id,
        "document_version": unit.document_version,
        "section_number": unit.section_number,
        "section_path": unit.section_path,
    }


def guard_writable(name: str, frozen: Collection[str]) -> None:
    if name in frozen:
        raise CollectionFrozenError(f"collection {name!r} is frozen")


@dataclass(frozen=True, slots=True)
class DensePoint:
    unit_id: str
    vector: tuple[float, ...]
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if len(self.vector) != VECTOR_SIZE:
            raise ValueError(
                f"vector dimension {len(self.vector)} is not {VECTOR_SIZE}"
            )


@dataclass(frozen=True, slots=True)
class DenseHit:
    unit_id: str
    score: float
    payload: dict[str, Any]


def _point_id(unit_id: str) -> str:
    """A UUID derived from the unit id, since Qdrant ids are UUID or integer.

    Derived rather than random so that re-upserting the same unit overwrites
    its point instead of adding a second one — a rebuild that doubled the point
    count would break §6.4's load-time check for no real reason.
    """
    digest = hashlib.sha256(unit_id.encode("utf-8")).hexdigest()
    return (
        f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
    )


@dataclass
class DenseIndex:
    """A handle on one collection. Freezing is local to this handle and to the
    corpus manifest that records it; the manifest is the durable authority."""

    name: str
    _client: Any = field(repr=False)
    _frozen: bool = False

    @classmethod
    def create(cls, url: str, name: str) -> DenseIndex:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        client = QdrantClient(url=url)
        if client.collection_exists(name):
            client.delete_collection(name)
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                # Cosine, because BGE-M3's dense vectors are L2-normalized and
                # the model is trained against cosine similarity.
                distance=Distance.COSINE,
            ),
        )
        return cls(name=name, _client=client)

    @classmethod
    def open(cls, url: str, name: str, *, frozen: bool = False) -> DenseIndex:
        from qdrant_client import QdrantClient

        return cls(name=name, _client=QdrantClient(url=url), _frozen=frozen)

    def freeze(self) -> None:
        self._frozen = True

    def drop(self) -> None:
        self._client.delete_collection(self.name)

    def vector_size(self) -> int:
        info = self._client.get_collection(self.name)
        return int(info.config.params.vectors.size)

    def point_count(self) -> int:
        return int(self._client.count(self.name, exact=True).count)

    def unit_ids(self) -> frozenset[str]:
        """Read the complete payload inventory without retrieving vectors."""
        found: set[str] = set()
        offset: Any = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                unit_id = str((point.payload or {}).get("unit_id", ""))
                if not unit_id:
                    raise ValueError("dense point has no unit_id payload")
                found.add(unit_id)
            if offset is None:
                return frozenset(found)

    def upsert(self, points: Sequence[DensePoint]) -> None:
        from qdrant_client.models import PointStruct

        if self._frozen:
            raise CollectionFrozenError(f"collection {self.name!r} is frozen")
        if not points:
            return
        self._client.upsert(
            collection_name=self.name,
            points=[
                PointStruct(
                    id=_point_id(point.unit_id),
                    vector=list(point.vector),
                    payload=point.payload,
                )
                for point in points
            ],
            wait=True,
        )

    def search(self, vector: Sequence[float], k: int) -> list[DenseHit]:
        if k <= 0:
            raise ValueError("k must be positive")
        if len(vector) != VECTOR_SIZE:
            raise ValueError(f"vector dimension {len(vector)} is not {VECTOR_SIZE}")
        found = self._client.query_points(
            collection_name=self.name,
            query=list(vector),
            limit=k,
            with_payload=True,
        ).points
        return [
            DenseHit(
                unit_id=str((point.payload or {}).get("unit_id", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in found
        ]
