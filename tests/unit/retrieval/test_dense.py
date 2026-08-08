from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.indexable import IndexTextPolicy, build_index_units
from specpilot.retrieval.dense import (
    VECTOR_SIZE,
    CollectionFrozenError,
    DensePoint,
    collection_name,
    point_payload,
)
from tests.helpers import rfc_factory

WEIGHTS = "a" * 64


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "units.xml", rfc_factory.QA_RFC_XML)


def units(document: Path, policy: IndexTextPolicy | None = None):  # type: ignore[no-untyped-def]
    return build_index_units(document, RfcLimits(), ClauseLimits(), policy)


def test_a_section_heading_joins_the_indexed_text_but_not_the_source_text(
    document: Path,
) -> None:
    """A section number is a locator and never appears in its own body, so
    without this a question naming a section is unanswerable by retrieval."""
    first = units(document)[0]

    assert first.indexed.startswith("1 One")
    assert "1 One" not in first.text


def test_the_policy_can_be_turned_off_and_then_the_texts_match(
    document: Path,
) -> None:
    """It is a recorded decision, not a property of the parser."""
    plain = units(document, IndexTextPolicy(include_section_heading=False))[0]

    assert plain.indexed == plain.text


def test_tables_become_units_alongside_clauses(document: Path) -> None:
    kinds = {unit.kind for unit in units(document)}

    assert kinds == {"clause", "table"}


def test_every_unit_id_is_distinct(document: Path) -> None:
    all_units = units(document)

    assert len({unit.unit_id for unit in all_units}) == len(all_units)


def test_the_collection_name_carries_the_corpus_and_pipeline_versions() -> None:
    name = collection_name("c" * 64, "clause/v1", "index-text/v1")

    assert name.startswith("specpilot_")
    assert collection_name("d" * 64, "clause/v1", "index-text/v1") != name
    assert collection_name("c" * 64, "clause/v2", "index-text/v1") != name
    assert collection_name("c" * 64, "clause/v1", "index-text/v2") != name


def test_the_collection_name_is_stable_for_the_same_versions() -> None:
    first = collection_name("c" * 64, "clause/v1", "index-text/v1")
    second = collection_name("c" * 64, "clause/v1", "index-text/v1")

    assert first == second


def test_the_collection_name_is_a_legal_qdrant_identifier() -> None:
    name = collection_name("c" * 64, "clause/v1", "index-text/v1")

    assert name.replace("_", "").isalnum()
    assert len(name) <= 255


def test_a_point_payload_holds_locators_and_never_clause_text(
    document: Path,
) -> None:
    """The payload comes back on every hit. Section 8.1's field rule applies to
    it exactly as it applies to an annotation record."""
    unit = units(document)[0]

    payload = point_payload(unit)

    assert set(payload) == {
        "unit_id",
        "kind",
        "document_id",
        "document_version",
        "section_number",
        "section_path",
    }
    assert "Prose" not in str(payload)
    assert unit.text not in str(payload)


def test_a_point_carries_a_vector_of_the_models_width() -> None:
    point = DensePoint(unit_id="u1", vector=(0.0,) * VECTOR_SIZE, payload={})

    assert len(point.vector) == VECTOR_SIZE


def test_a_vector_of_the_wrong_width_is_refused() -> None:
    """A silently reshaped vector would index a document nothing can match."""
    with pytest.raises(ValueError, match="dimension"):
        DensePoint(unit_id="u1", vector=(0.0, 1.0), payload={})


def test_writing_into_a_frozen_collection_is_refused() -> None:
    """§6.4: after freezing, ingestion loses write access and serving is
    read-only. A late upsert would change what the manifest attests to."""
    from specpilot.retrieval.dense import guard_writable

    guard_writable("specpilot_abc", frozen=frozenset())

    with pytest.raises(CollectionFrozenError):
        guard_writable("specpilot_abc", frozen=frozenset({"specpilot_abc"}))
