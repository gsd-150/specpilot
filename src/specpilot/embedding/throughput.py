"""Measured embedding throughput, and a cache key that cannot go stale quietly.

Product plan section 7 forbids writing down a wall-clock figure for encoding the
corpus before one has been measured. `estimate_full_corpus_seconds` therefore
refuses a `None` measurement rather than falling back to a plausible number:
a guess and a measurement are indistinguishable once they are both floats in a
report.

A vector is only reusable if three things are unchanged — the weights that
produced it, the pipeline that cut the text, and the text itself. The cache key
covers all three, because the failure it prevents is silent: re-chunk the corpus
and a key over text alone still hits, returning vectors built from spans that no
longer exist. The three are joined by a separator that cannot occur in any of
them, so no two different triples can flatten to the same string.

Nothing here loads a model. `measure_throughput` takes the encoder as an
argument, which keeps the timing harness testable without 2 GB of weights and
keeps the runtime dependency out of the package.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Bumped whenever clause boundaries change. A vector built from a different
# split of the same document is not the same vector.
PIPELINE_VERSION = "clause/v1"

_DOMAIN = b"specpilot/embedding-cache/v1"
_SEPARATOR = "\x1f"


class BatchOrder(StrEnum):
    """How clauses are grouped into batches, which is not a free choice.

    Every batch pads to its longest member, so a batch of mixed-length clauses
    spends most of its compute on padding. Measured on RFC 9110, grouping
    similar lengths together raises throughput by about 60% on both MPS and CPU
    — larger than any batch-size choice — so an unlabelled rate is ambiguous by
    a factor that dwarfs what it is usually reported to a tenth of.

    The measurement applies the ordering itself rather than accepting it as a
    claim, so the label cannot disagree with the run.
    """

    DOCUMENT = "document"
    LENGTH = "length"


class NoMeasurementError(RuntimeError):
    """An estimate was requested before anything was measured."""


def embedding_cache_key(
    weights_sha256: str, pipeline_version: str, text_sha256: str
) -> str:
    """Return the key a cached vector is stored under."""
    joined = _SEPARATOR.join((weights_sha256, pipeline_version, text_sha256))
    return hashlib.sha256(_DOMAIN + joined.encode("utf-8")).hexdigest()


def weights_sha256(directory: Path) -> str:
    """Hash a model directory over both file names and file contents.

    Names are covered because a checkpoint loads by filename: swapping which
    file is `config.json` changes what runs while leaving every byte on disk
    the same.

    Hidden files and directories are skipped. Model downloaders leave their own
    bookkeeping under `.cache`, carrying etags and timestamps that differ on
    every fetch of identical weights; hashing those would make the figure change
    for reasons that have nothing to do with the model. Nothing hidden is ever
    loaded, so this narrows the hash to what actually runs — it is not a defence
    against a file deliberately hidden in the directory.
    """
    digest = hashlib.sha256()
    counted = 0
    for path in sorted(directory.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(directory).parts):
            continue
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{path.name} is not a regular file")
        digest.update(str(path.relative_to(directory)).encode("utf-8"))
        digest.update(_SEPARATOR.encode("utf-8"))
        # Streamed: a checkpoint is gigabytes, and reading one whole into memory
        # to hash it would make this fail on the machines it most needs to run.
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").hexdigest().encode())
        digest.update(b"\x1e")
        counted += 1
    if counted == 0:
        raise ValueError("no weight files to hash")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ThroughputMeasurement:
    """One observed rate, labelled with everything that would change it.

    Holds no text and no vectors: a measurement is a cost figure, and section
    8.1's field rule applies to it as much as to an annotation record.
    """

    model_id: str
    weights_sha256: str
    pipeline_version: str
    device: str
    batch_order: BatchOrder
    batch_size: int
    sample_size: int
    sample_words: int
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if not self.device:
            raise ValueError("a rate needs the device it was measured on")
        if self.sample_size <= 0:
            raise ValueError("a rate needs a non-empty sample")
        if self.elapsed_seconds <= 0.0:
            raise ValueError("a rate needs a positive elapsed time")
        if self.batch_size <= 0:
            raise ValueError("batch size must be positive")

    @property
    def clauses_per_second(self) -> float:
        return self.sample_size / self.elapsed_seconds

    @property
    def words_per_second(self) -> float:
        """Reported alongside the clause rate so the sample can be checked.

        Clauses per second is only a fair estimator if the sample's length
        distribution matches the corpus. Publishing both makes a skewed sample
        visible instead of burying it in one number.
        """
        return self.sample_words / self.elapsed_seconds


def evenly_spaced[T](items: Sequence[T], size: int) -> tuple[T, ...]:
    """Take `size` items spread across `items`, deterministically.

    The first N clauses of an RFC are its abstract and introduction, which run
    shorter than the corpus average; timing those would flatter the estimate.
    Spacing the sample across the whole document costs nothing and removes the
    bias, and doing it by stride rather than at random keeps the run repeatable.
    """
    if size <= 0:
        raise ValueError("sample size must be positive")
    if size >= len(items):
        return tuple(items)
    step = len(items) / size
    return tuple(items[int(index * step)] for index in range(size))


def measure_throughput(
    texts: Sequence[str],
    encode: Callable[[Sequence[str]], object],
    *,
    model_id: str,
    weights_sha256: str,
    pipeline_version: str = PIPELINE_VERSION,
    device: str,
    batch_order: BatchOrder = BatchOrder.DOCUMENT,
    batch_size: int,
) -> ThroughputMeasurement:
    """Time `encode` over `texts` in batches and return the labelled rate."""
    if not texts:
        raise ValueError("a rate needs a non-empty sample")
    if batch_size <= 0:
        raise ValueError("batch size must be positive")

    ordered = (
        tuple(sorted(texts, key=lambda text: len(text.split())))
        if batch_order is BatchOrder.LENGTH
        else tuple(texts)
    )

    started = time.perf_counter()
    for start in range(0, len(ordered), batch_size):
        encode(ordered[start : start + batch_size])
    elapsed = time.perf_counter() - started

    return ThroughputMeasurement(
        model_id=model_id,
        weights_sha256=weights_sha256,
        pipeline_version=pipeline_version,
        device=device,
        batch_order=batch_order,
        batch_size=batch_size,
        sample_size=len(texts),
        sample_words=sum(len(text.split()) for text in texts),
        # A run fast enough to land on the clock's resolution would divide by
        # zero. The smallest representable positive time is honest here in a
        # way that zero is not.
        elapsed_seconds=max(elapsed, 1e-9),
    )


def estimate_full_corpus_seconds(
    measurement: ThroughputMeasurement | None, clause_count: int
) -> float:
    """Derive the full-corpus wall clock from a measured rate and a real count."""
    if measurement is None:
        raise NoMeasurementError(
            "no throughput has been measured; section 7 forbids guessing one"
        )
    if clause_count < 0:
        raise ValueError("clause count cannot be negative")
    return clause_count / measurement.clauses_per_second
