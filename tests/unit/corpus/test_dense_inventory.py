from __future__ import annotations

import hashlib
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import (
    build_dense_inventory,
    derived_corpus_sha256,
    vector_sha256,
)
from specpilot.corpus.indexable import IndexUnit
from specpilot.corpus.qa import QaLine, QaReport, qa_evidence_sha256
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.dense import (
    DenseRecord,
    collection_name,
    point_id_for_unit,
    point_payload,
)
from specpilot.retrieval.local import LocalCorpus


def _unit(**changes: object) -> IndexUnit:
    values: dict[str, object] = {
        "unit_id": "u1",
        "kind": "clause",
        "document_id": "ietf-rfc-1",
        "document_version": "1",
        "section_number": "1",
        "section_path": "One",
        "ordinal": 1,
        "text": "source",
        "indexed": "1 One\nsource",
    }
    values.update(changes)
    return IndexUnit(**values)  # type: ignore[arg-type]


def _record(unit: IndexUnit) -> DenseRecord:
    return DenseRecord(
        point_id=point_id_for_unit(unit.unit_id),
        payload=point_payload(unit),
        vector=(0.0,) * 1023 + (1.0,),
    )


def _qa_report(
    *,
    numerator_delta: int = 0,
    threshold_delta: float = 0.0,
) -> QaReport:
    lines = (
        QaLine("section_numbering", 1.0, 1.0, True, 10, 10),
        QaLine("cross_references", 0.9, 0.9, True, 9, 10),
        QaLine("table_fidelity", 0.9, 0.9, True, 9, 10),
        QaLine("coverage", 0.01, 0.02, True, 1, 100),
        QaLine("orphan_normatives", 0.0, 0.01, True, 0, 10),
        QaLine("excerpt_fit", 1.0, 1.0, True, 10, 10),
    )
    if numerator_delta:
        lines = (replace(lines[0], numerator=10 + numerator_delta), *lines[1:])
    if threshold_delta:
        lines = (
            lines[0],
            replace(lines[1], threshold=0.9 + threshold_delta),
            *lines[2:],
        )
    return QaReport(document_id="ietf-rfc-1", passed=True, lines=lines)


def test_derived_corpus_hash_has_a_fixed_golden_and_ignores_input_order() -> None:
    first = _unit()
    second = _unit(unit_id="u2", indexed="2 Two\nother")

    assert derived_corpus_sha256((first,)) == (
        "0bd8382bb7b8c04d69e3428887d728cc408148ac188063adc419c81a78bfb4e8"
    )
    assert derived_corpus_sha256((first, second)) == derived_corpus_sha256(
        (second, first)
    )


def test_vector_hash_uses_little_endian_float32() -> None:
    vector = (0.0,) * 1023 + (1.0,)
    expected = hashlib.sha256(struct.pack("<1024f", *vector)).hexdigest()

    assert expected == (
        "a69bf4f8284250b0063e51eed4244ccb40a428c75672dde1d103bf0b8995dc87"
    )
    assert vector_sha256(vector) == expected


def test_inventory_has_a_fixed_golden_and_ignores_input_order() -> None:
    first = _unit()
    second = _unit(unit_id="u2", indexed="2 Two\nother")

    assert build_dense_inventory((first,), (_record(first),)).inventory_root_sha256 == (
        "4b569f5d3be3f7ea18e31425e2ffffa3ef37e80195b20af33a42b0a58bf57149"
    )
    assert build_dense_inventory(
        (first, second), (_record(first), _record(second))
    ) == build_dense_inventory(
        (second, first), (_record(second), _record(first))
    )


def test_live_point_or_payload_drift_is_refused() -> None:
    unit = _unit()
    record = _record(unit)
    with pytest.raises(ValueError):
        build_dense_inventory((unit,), (replace(record, point_id="wrong"),))
    with pytest.raises(ValueError):
        build_dense_inventory(
            (unit,),
            (replace(record, payload={**record.payload, "section_path": "Other"}),),
        )


@pytest.mark.parametrize(
    "mutation", ["identity", "locator", "source", "indexed", "vector"]
)
def test_inventory_changes_for_every_bound_fact(mutation: str) -> None:
    unit = _unit()
    record = _record(unit)
    original = build_dense_inventory((unit,), (record,))
    if mutation == "identity":
        changed_unit = replace(unit, unit_id="u2")
        changed_record = _record(changed_unit)
    elif mutation == "locator":
        changed_unit = replace(unit, section_path="Other")
        changed_record = _record(changed_unit)
    elif mutation == "source":
        changed_unit = replace(unit, text="changed source")
        changed_record = record
    elif mutation == "indexed":
        changed_unit = replace(unit, indexed="changed indexed")
        changed_record = record
    else:
        changed_unit = unit
        changed = list(record.vector)
        changed[0] = 0.5
        changed_record = replace(record, vector=tuple(changed))
    observed = build_dense_inventory((changed_unit,), (changed_record,))
    assert observed.inventory_root_sha256 != original.inventory_root_sha256


def test_duplicate_local_and_dense_identities_are_refused() -> None:
    first = _unit()
    second = _unit(unit_id="u2")
    first_record = _record(first)
    second_record = _record(second)

    with pytest.raises(ValueError, match="duplicate local unit"):
        build_dense_inventory((first, first), (first_record,))
    with pytest.raises(ValueError, match="duplicate point"):
        build_dense_inventory(
            (first, second),
            (first_record, replace(second_record, point_id=first_record.point_id)),
        )
    with pytest.raises(ValueError, match="duplicate payload unit"):
        build_dense_inventory(
            (first,),
            (first_record, replace(first_record, point_id="different-point-id")),
        )


def test_dense_and_local_sets_must_match_exactly() -> None:
    first = _unit()
    second = _unit(unit_id="u2")

    with pytest.raises(ValueError, match="does not match"):
        build_dense_inventory((first, second), (_record(first),))
    with pytest.raises(ValueError, match="does not match"):
        build_dense_inventory((first,), (_record(first), _record(second)))


@pytest.mark.parametrize(
    "payload",
    [
        {"unit_id": "u1"},
        {
            **point_payload(_unit()),
            "text": "must never enter a locator payload",
        },
        {**point_payload(_unit()), "section_number": 1},
    ],
)
def test_payload_must_be_the_exact_six_field_locator(
    payload: dict[str, object],
) -> None:
    record = replace(_record(_unit()), payload=payload)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="payload"):
        build_dense_inventory((_unit(),), (record,))


@pytest.mark.parametrize(
    "vector",
    [
        None,
        {"dense": [0.0] * 1024},
        ((0.0,) * 1024, (1.0,) * 1024),
        (0.0,) * 1023,
        (0.0,) * 1023 + (float("nan"),),
        (0.0,) * 1023 + (float("inf"),),
        (0.0,) * 1023 + (3.5e38,),
        (0.0,) * 1023 + (10**400,),
        (0.0,) * 1023 + (True,),
    ],
)
def test_vectors_must_be_one_finite_float32_1024_value_sequence(vector: object) -> None:
    record = replace(_record(_unit()), vector=vector)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="vector"):
        build_dense_inventory((_unit(),), (record,))


def test_qa_evidence_has_a_fixed_domain_separated_golden() -> None:
    assert qa_evidence_sha256("a" * 64, _qa_report()) == (
        "5af5b2224cd9913eaa26ac683aa8a40c65cf6d84452830859bbf92a938df4676"
    )


def test_qa_evidence_binds_counts_thresholds_and_source() -> None:
    original = qa_evidence_sha256("a" * 64, _qa_report())
    assert qa_evidence_sha256("b" * 64, _qa_report()) != original
    assert qa_evidence_sha256("a" * 64, _qa_report(numerator_delta=1)) != original
    assert qa_evidence_sha256("a" * 64, _qa_report(threshold_delta=0.01)) != original
    assert qa_evidence_sha256(
        "a" * 64, replace(_qa_report(), document_id="ietf-rfc-2")
    ) != original


@pytest.mark.parametrize("source_manifest_id", ["A" * 64, "a" * 63, "z" * 64])
def test_qa_evidence_requires_a_lowercase_sha256(source_manifest_id: str) -> None:
    with pytest.raises(ValueError, match="source manifest"):
        qa_evidence_sha256(source_manifest_id, _qa_report())


def test_qa_evidence_requires_all_six_passing_lines_in_canonical_order() -> None:
    report = _qa_report()
    with pytest.raises(ValueError, match="incomplete or failed"):
        qa_evidence_sha256("a" * 64, replace(report, passed=False))
    with pytest.raises(ValueError, match="incomplete or failed"):
        qa_evidence_sha256("a" * 64, replace(report, lines=report.lines[:-1]))
    with pytest.raises(ValueError, match="incomplete or failed"):
        qa_evidence_sha256(
            "a" * 64,
            replace(
                report,
                lines=(report.lines[1], report.lines[0], *report.lines[2:]),
            ),
        )
    with pytest.raises(ValueError, match="unmeasured or failed"):
        qa_evidence_sha256(
            "a" * 64,
            replace(
                report,
                lines=(*report.lines[:-1], replace(report.lines[-1], passed=False)),
            ),
        )
    with pytest.raises(ValueError, match="unmeasured"):
        qa_evidence_sha256(
            "a" * 64,
            replace(
                report,
                lines=(
                    *report.lines[:-1],
                    replace(report.lines[-1], numerator=0, denominator=0),
                ),
            ),
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1])
def test_qa_evidence_requires_finite_float_measurements(value: object) -> None:
    report = _qa_report()
    changed = replace(report.lines[0], measured=value)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="non-finite measurement"):
        qa_evidence_sha256(
            "a" * 64,
            replace(report, lines=(changed, *report.lines[1:])),
        )


def test_restricted_rfc_corpus_preserves_all_three_compatibility_constants() -> None:
    repository = Path(__file__).resolve().parents[3]
    paths = (
        repository / "artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml",
        repository / "artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml",
    )
    if not all(path.is_file() for path in paths):
        pytest.skip("restricted RFC fixtures are not available")
    clause_limits = ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)
    verified_rfc_9110 = load_verified_rfc(paths[0], RfcLimits())
    verified_rfc_9112 = load_verified_rfc(paths[1], RfcLimits())
    corpus = LocalCorpus.load(
        (
            (verified_rfc_9110, clause_limits),
            (verified_rfc_9112, clause_limits),
        ),
        RfcLimits(),
    )

    assert corpus.unit_count() == 1922
    assert Bm25Index.build(corpus.indexable()).fingerprint == (
        "8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3"
    )
    assert derived_corpus_sha256(corpus.units()) == (
        "46616bd050308f6f77782afe8706b8e2d8f577de9b9b698e228e1c52b40596eb"
    )
    assert collection_name(
        derived_corpus_sha256(corpus.units()), "clause/v1", "index-text/v1"
    ) == "specpilot_ff4841e2d846388014efa06870fbbdb7"
