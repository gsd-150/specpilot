from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import pytest

from specpilot.embedding.throughput import (
    BatchOrder,
    LengthMetric,
    NoMeasurementError,
    ThroughputMeasurement,
    embedding_cache_key,
    estimate_full_corpus_seconds,
    evenly_spaced,
    measure_throughput,
    weights_sha256,
)

WEIGHTS = "a" * 64
TEXT = "b" * 64
PIPELINE = "clause/v1"


def measurement(**overrides: object) -> ThroughputMeasurement:
    fields: dict[str, object] = {
        "model_id": "BAAI/bge-m3",
        "weights_sha256": WEIGHTS,
        "pipeline_version": PIPELINE,
        "device": "mps",
        "batch_order": BatchOrder.DOCUMENT,
        "length_metric": LengthMetric.WORDS,
        "batch_size": 16,
        "sample_size": 200,
        "sample_words": 12_000,
        "elapsed_seconds": 10.0,
    }
    return ThroughputMeasurement(**{**fields, **overrides})  # type: ignore[arg-type]


def test_the_cache_key_changes_when_the_weights_change() -> None:
    assert embedding_cache_key(WEIGHTS, PIPELINE, TEXT) != embedding_cache_key(
        "c" * 64, PIPELINE, TEXT
    )


def test_the_cache_key_changes_when_the_pipeline_version_changes() -> None:
    """A re-chunk must not silently reuse vectors built from the old spans."""
    assert embedding_cache_key(WEIGHTS, PIPELINE, TEXT) != embedding_cache_key(
        WEIGHTS, "clause/v2", TEXT
    )


def test_the_cache_key_changes_when_the_text_changes() -> None:
    assert embedding_cache_key(WEIGHTS, PIPELINE, TEXT) != embedding_cache_key(
        WEIGHTS, PIPELINE, "d" * 64
    )


def test_the_cache_key_is_stable_for_the_same_three_inputs() -> None:
    assert embedding_cache_key(WEIGHTS, PIPELINE, TEXT) == embedding_cache_key(
        WEIGHTS, PIPELINE, TEXT
    )


def test_moving_a_boundary_between_inputs_does_not_reproduce_a_key() -> None:
    """Concatenation alone would make ("ab", "c") and ("a", "bc") one string."""
    assert embedding_cache_key("ab", "c", TEXT) != embedding_cache_key("a", "bc", TEXT)


def test_the_estimate_follows_the_measured_rate_and_the_real_clause_count() -> None:
    # 200 clauses in 10 seconds is 20/s, so 1474 clauses is 73.7 seconds.
    assert estimate_full_corpus_seconds(measurement(), 1_474) == pytest.approx(73.7)


def test_an_estimate_without_a_measurement_raises_rather_than_guessing() -> None:
    """Product plan section 7 forbids writing down an unmeasured wall clock."""
    with pytest.raises(NoMeasurementError):
        estimate_full_corpus_seconds(None, 1_474)


def test_a_measurement_that_took_no_time_is_refused() -> None:
    with pytest.raises(ValueError, match="elapsed"):
        measurement(elapsed_seconds=0.0)


def test_a_measurement_over_no_clauses_is_refused() -> None:
    with pytest.raises(ValueError, match="sample"):
        measurement(sample_size=0)


def test_a_rate_is_not_a_rate_without_the_device_it_was_taken_on() -> None:
    """MPS and CPU differ by enough that an unlabelled figure is not a number."""
    with pytest.raises(ValueError, match="device"):
        measurement(device="")

    assert measurement(device="cpu").device == "cpu"
    assert measurement(device="mps").clauses_per_second == pytest.approx(20.0)


def test_measure_encodes_every_sampled_clause_in_batches() -> None:
    texts = tuple(f"clause {index}" for index in range(7))
    batches: list[Sequence[str]] = []

    result = measure_throughput(
        texts,
        lambda batch: batches.append(tuple(batch)),
        model_id="BAAI/bge-m3",
        weights_sha256=WEIGHTS,
        pipeline_version=PIPELINE,
        device="cpu",
        batch_size=3,
    )

    assert [len(batch) for batch in batches] == [3, 3, 1]
    assert tuple(text for batch in batches for text in batch) == texts
    assert result.sample_size == 7
    assert result.sample_words == 14
    assert result.elapsed_seconds > 0.0


def test_measuring_nothing_is_refused_rather_than_reported_as_infinite() -> None:
    with pytest.raises(ValueError, match="sample"):
        measure_throughput(
            (),
            lambda batch: None,
            model_id="BAAI/bge-m3",
            weights_sha256=WEIGHTS,
            pipeline_version=PIPELINE,
            device="cpu",
            batch_size=4,
        )


def test_the_measurement_record_holds_no_clause_text() -> None:
    result = measure_throughput(
        ("the freshness lifetime of a stored response",),
        lambda batch: None,
        model_id="BAAI/bge-m3",
        weights_sha256=WEIGHTS,
        pipeline_version=PIPELINE,
        device="cpu",
        batch_size=4,
    )

    assert "freshness" not in json.dumps(asdict(result))


def test_length_ordering_regroups_the_batches_without_dropping_a_clause() -> None:
    """Every batch pads to its longest member, so grouping by length matters."""
    texts = ("aa bb cc", "dd", "ee ff", "gg hh ii jj", "kk")
    batches: list[Sequence[str]] = []

    result = measure_throughput(
        texts,
        lambda batch: batches.append(tuple(batch)),
        model_id="BAAI/bge-m3",
        weights_sha256=WEIGHTS,
        pipeline_version=PIPELINE,
        device="cpu",
        batch_order=BatchOrder.LENGTH,
        batch_size=2,
    )

    assert batches == [("dd", "kk"), ("ee ff", "aa bb cc"), ("gg hh ii jj",)]
    assert sorted(text for batch in batches for text in batch) == sorted(texts)
    assert result.batch_order is BatchOrder.LENGTH
    assert result.sample_size == 5
    assert result.sample_words == 11


def test_document_order_is_the_default_and_leaves_the_batches_alone() -> None:
    texts = ("aa bb cc", "dd", "ee ff")
    batches: list[Sequence[str]] = []

    result = measure_throughput(
        texts,
        lambda batch: batches.append(tuple(batch)),
        model_id="BAAI/bge-m3",
        weights_sha256=WEIGHTS,
        pipeline_version=PIPELINE,
        device="cpu",
        batch_size=2,
    )

    assert batches == [("aa bb cc", "dd"), ("ee ff",)]
    assert result.batch_order is BatchOrder.DOCUMENT


def test_ordering_by_length_uses_the_measure_it_is_given() -> None:
    """Words stopped being a proxy for tokens when grammar joined the corpus.

    Prose runs 1.50 tokens per word and ABNF runs 2.90, so a 50-word grammar
    block is 145 tokens where a 50-word paragraph is 75. Ordering by words puts
    them in one batch and pads both to the longer — measured on RFC 9110 at
    batch 32, 33.9% waste against 4.3% for ordering by tokens.
    """
    # "aa" is short in words and long in this metric; "bb cc dd" the reverse.
    lengths = {"aa": 100, "bb cc dd": 10}
    batches: list[Sequence[str]] = []

    result = measure_throughput(
        ("aa", "bb cc dd"),
        lambda batch: batches.append(tuple(batch)),
        model_id="BAAI/bge-m3",
        weights_sha256=WEIGHTS,
        pipeline_version=PIPELINE,
        device="cpu",
        batch_order=BatchOrder.LENGTH,
        batch_size=1,
        length_of=lambda text: lengths[text],
    )

    assert batches == [("bb cc dd",), ("aa",)]
    assert result.length_metric is LengthMetric.TOKENS


def test_without_a_measure_the_fallback_to_words_is_on_the_record() -> None:
    """An unlabelled sort key is ambiguous by more than the reported precision."""
    result = measure_throughput(
        ("aa", "bb cc dd"),
        lambda batch: None,
        model_id="BAAI/bge-m3",
        weights_sha256=WEIGHTS,
        pipeline_version=PIPELINE,
        device="cpu",
        batch_order=BatchOrder.LENGTH,
        batch_size=1,
    )

    assert result.length_metric is LengthMetric.WORDS


def test_the_words_per_second_figure_accompanies_the_clause_rate() -> None:
    assert measurement().words_per_second == pytest.approx(1_200.0)


def test_the_sample_is_spread_across_the_document_not_taken_from_the_front() -> None:
    """An RFC's first clauses are its abstract, and shorter than its average."""
    clauses = tuple(range(100))

    sample = evenly_spaced(clauses, 5)

    assert sample == (0, 20, 40, 60, 80)


def test_a_sample_larger_than_the_corpus_takes_the_whole_corpus() -> None:
    assert evenly_spaced((1, 2, 3), 10) == (1, 2, 3)


def test_the_sample_never_repeats_a_clause() -> None:
    for size in range(1, 40):
        sample = evenly_spaced(tuple(range(39)), size)
        assert len(set(sample)) == len(sample) == size


def test_an_empty_sample_is_refused() -> None:
    with pytest.raises(ValueError, match="sample size"):
        evenly_spaced((1, 2, 3), 0)


def test_the_weights_hash_covers_both_file_names_and_file_contents(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    first.mkdir()
    (first / "model.safetensors").write_bytes(b"weights")
    (first / "config.json").write_text("{}", encoding="utf-8")

    second = tmp_path / "second"
    second.mkdir()
    (second / "model.safetensors").write_bytes(b"weights")
    (second / "config.json").write_text("{}", encoding="utf-8")
    assert weights_sha256(first) == weights_sha256(second)

    (second / "config.json").write_text('{"pooling":"cls"}', encoding="utf-8")
    assert weights_sha256(first) != weights_sha256(second)

    third = tmp_path / "third"
    third.mkdir()
    (third / "model.safetensors").write_bytes(b"weights")
    (third / "settings.json").write_text("{}", encoding="utf-8")
    assert weights_sha256(first) != weights_sha256(third)


def test_a_symlink_in_the_weights_directory_is_refused(tmp_path: Path) -> None:
    """A hash over a symlink describes the link, not what gets loaded."""
    directory = tmp_path / "weights"
    directory.mkdir()
    (directory / "model.safetensors").write_bytes(b"weights")
    (tmp_path / "elsewhere.json").write_text("{}", encoding="utf-8")
    (directory / "config.json").symlink_to(tmp_path / "elsewhere.json")

    with pytest.raises(ValueError, match="regular file"):
        weights_sha256(directory)


def test_a_downloaders_hidden_bookkeeping_does_not_change_the_weights_hash(
    tmp_path: Path,
) -> None:
    """Etags and fetch timestamps differ on every download of identical weights."""
    directory = tmp_path / "weights"
    directory.mkdir()
    (directory / "model.safetensors").write_bytes(b"weights")
    before = weights_sha256(directory)

    bookkeeping = directory / ".cache" / "huggingface" / "download"
    bookkeeping.mkdir(parents=True)
    (bookkeeping / "model.safetensors.metadata").write_text(
        "etag\n2026-08-08T02:00:00Z\n", encoding="utf-8"
    )

    assert weights_sha256(directory) == before


def test_an_empty_weights_directory_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "weights"
    directory.mkdir()

    with pytest.raises(ValueError, match="no weight files"):
        weights_sha256(directory)
