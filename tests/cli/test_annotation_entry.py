from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import specpilot.cli as cli_module
from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits, build_clauses
from specpilot.ingestion.rfc import RfcByteSnapshot
from specpilot.manifests.store import ManifestStore
from tests.helpers import rfc_factory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


def stored_manifest(tmp_path: Path, xml: Path) -> tuple[Path, str]:
    directory = tmp_path / "manifests"
    manifest = ManifestStore(directory).create_source_v2(
        RfcSourceManifestDraft(
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            text_url="https://www.rfc-editor.org/rfc/rfc9999.txt",
            xml_url="https://www.rfc-editor.org/rfc/rfc9999.xml",
            text_sha256="a" * 64,
            xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest(),
            downloaded_at="2026-08-07T09:00:00Z",
            created_at="2026-08-07T09:01:00Z",
        )
    )
    return directory, manifest.manifest_id


def source(tmp_path: Path, corpus: Path) -> list[str]:
    xml = rfc_factory.write_safe(corpus)
    directory, manifest_id = stored_manifest(tmp_path, xml)
    return [
        "--manifest", manifest_id,
        "--manifest-dir", str(directory),
        "--xml", str(xml),
    ]


def test_the_clause_index_gives_ids_a_gold_field_can_reference(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["corpus", "clauses", *source(tmp_path, corpus)])

    assert code == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows
    for row in rows:
        assert len(row["clause_id"]) == 64
        assert set(row) == {
            "clause_id",
            "section_number",
            "section_path",
            "ordinal",
            "anchor",
            "word_count",
            "byte_count",
        }


def test_the_clause_index_carries_no_clause_text(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["corpus", "clauses", *source(tmp_path, corpus)])

    assert "This paragraph exists" not in capsys.readouterr().out


def test_the_clause_index_can_be_narrowed_to_one_section(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = source(tmp_path, corpus)
    main(["corpus", "clauses", *arguments])
    everything = capsys.readouterr().out.splitlines()

    main(["corpus", "clauses", *arguments, "--section", "1.1"])
    narrowed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert 0 < len(narrowed) < len(everything)
    assert all(row["section_number"] == "1.1" for row in narrowed)


def test_a_document_that_is_not_the_frozen_one_has_no_clause_index(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = source(tmp_path, corpus)
    Path(arguments[-1]).write_text(
        rfc_factory.SAFE_RFC_XML.replace("Introduction", "Introduction "),
        encoding="utf-8",
    )

    code = main(["corpus", "clauses", *arguments])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_hash_mismatch\n"


def test_the_overlap_command_reports_the_figure_the_contract_requires(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = source(tmp_path, corpus)
    main(["corpus", "clauses", *arguments])
    first = json.loads(capsys.readouterr().out.splitlines()[0])

    code = main(
        [
            "corpus", "overlap", *arguments,
            "--clause-id", first["clause_id"],
            "--question", "Which paragraph exists in this document?",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert 0.0 < payload["question_gold_jaccard"] <= 1.0
    assert payload["gold_clause_count"] == 1


def test_the_overlap_command_refuses_a_clause_that_is_not_in_the_document(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "corpus", "overlap", *source(tmp_path, corpus),
            "--clause-id", "f" * 64,
            "--question", "anything",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unknown_clause_id\n"


def test_the_template_is_a_record_the_contract_would_reject_until_filled_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A template that validates as-is invites twenty-three copies of itself."""
    code = main(["annotation", "template", "--level", "l1"])

    assert code == 0
    template = json.loads(capsys.readouterr().out)
    assert template["schema_version"] == "annotation-l1/v2"
    assert "question" in template
    assert "gold_clause_ids" in template
    assert "content_origin" in template
    assert "label_origin" in template
    assert "gold_origins" in template
    assert "independent_path" not in template


def gold_record(
    tmp_path: Path, corpus: Path, **overrides: object
) -> tuple[Path, list[str]]:
    """A record whose gold really is in the document it names."""
    from specpilot.contracts.rfc import RfcLimits
    from specpilot.corpus.clauses import ClauseLimits, build_clauses

    arguments = source(tmp_path, corpus)
    xml = Path(arguments[-1])
    clause = build_clauses(xml, RfcLimits(), ClauseLimits())[0]
    path = tmp_path / "record.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "annotation-l1/v2",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": "Which condition makes a stored response stale?",
                "direction": "clause_first",
                "content_origin": "mixed",
                "label_origin": "mixed",
                "document_id": "ietf-rfc-9999",
                "document_version": "2026-08",
                "gold_clause_ids": [clause.clause_id],
                "gold_section_paths": [clause.section_path],
                "expected_refusal": False,
                "question_gold_jaccard": 0.12,
                "gold_origins": [
                    {"origin": "model_proposal", "producer": "openai-codex"},
                    {"origin": "human_source_review"},
                ],
                **overrides,
            }
        ),
        encoding="utf-8",
    )
    return path, arguments


def test_a_record_carrying_gold_needs_the_document_it_names(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Shape validation cannot tell whether a clause id exists."""
    record, _ = gold_record(tmp_path, corpus)

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
        ]
    )

    assert code == 2
    assert capsys.readouterr().err == "source_required_for_gold\n"


def test_a_gold_clause_the_document_does_not_contain_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record, arguments = gold_record(tmp_path, corpus, gold_clause_ids=["f" * 64])

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
            *arguments,
        ]
    )

    assert code == 2
    assert capsys.readouterr().err == "unknown_gold_clause\n"


def test_a_path_swap_cannot_authorize_replacement_only_gold(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement_xml = rfc_factory.SAFE_RFC_XML.replace(
        '      <section anchor="scope"',
        '      <t>A replacement-only paragraph.</t>\n'
        '      <section anchor="scope"',
    )
    replacement_path = rfc_factory.write(corpus, "replacement.xml", replacement_xml)
    replacement_only = build_clauses(
        replacement_path,
        RfcLimits(),
        ClauseLimits(),
    )[1]
    record, arguments = gold_record(
        tmp_path,
        corpus,
        gold_clause_ids=[replacement_only.clause_id],
        gold_section_paths=[replacement_only.section_path],
    )
    original_read = cli_module.read_rfc_snapshot

    def read_then_replace(path: Path, limits: RfcLimits) -> RfcByteSnapshot:
        snapshot = original_read(path, limits)
        path.write_text(replacement_xml, encoding="utf-8")
        return snapshot

    monkeypatch.setattr(cli_module, "read_rfc_snapshot", read_then_replace)
    annotation_dir = tmp_path / "annotations"

    code = main(
        [
            "annotation",
            "add",
            "--record",
            str(record),
            "--annotation-dir",
            str(annotation_dir),
            *arguments,
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unknown_gold_clause\n"
    assert not annotation_dir.exists()


def test_a_record_pointed_at_the_wrong_document_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record, arguments = gold_record(tmp_path, corpus, document_id="ietf-rfc-9111")

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
            *arguments,
        ]
    )

    assert code == 2
    assert capsys.readouterr().err == "document_id_mismatch\n"


def test_a_record_pointed_at_the_wrong_document_version_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record, arguments = gold_record(
        tmp_path,
        corpus,
        document_version="2025-01",
    )
    directory = tmp_path / "annotations"

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
            *arguments,
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_version_mismatch\n"
    assert not directory.exists()


def test_a_key_point_that_restates_its_clause_is_refused(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§8.1: a key point may carry factual values but may not reproduce the
    clause's wording as a sentence."""
    record, arguments = gold_record(
        tmp_path,
        corpus,
        key_points=[
            {
                "point_id": "kp-1",
                "criterion": "This paragraph exists so the tree has content",
            }
        ],
    )

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
            *arguments,
        ]
    )

    assert code == 2
    assert capsys.readouterr().err == "key_point_restates_clause\n"


def test_a_record_whose_gold_checks_out_is_stored(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record, arguments = gold_record(tmp_path, corpus)

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
            *arguments,
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "stored"


def test_an_unanswerable_record_needs_no_source_because_it_has_no_gold(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = tmp_path / "l1-dev-001.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "annotation-l1/v2",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": "Which condition makes a stored response stale?",
                "direction": "clause_first",
                "content_origin": "human",
                "label_origin": "human",
                "document_id": "ietf-rfc-9111",
                "document_version": "2022-06",
                "expected_refusal": True,
                "gold_origins": [],
            }
        ),
        encoding="utf-8",
    )
    directory = tmp_path / "annotations"

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "stored"
    assert len(payload["annotation_id"]) == 64
    assert AnnotationStore(directory).read(payload["annotation_id"]).item_id == (
        "l1-dev-001"
    )


def test_adding_a_record_the_contract_rejects_stores_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An answerable item without gold is the mistake this catches."""
    record = tmp_path / "bad.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "annotation-l1/v2",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": "Which condition makes a stored response stale?",
                "direction": "clause_first",
                "content_origin": "human",
                "label_origin": "human",
                "document_id": "ietf-rfc-9111",
                "document_version": "2022-06",
                "expected_refusal": False,
            }
        ),
        encoding="utf-8",
    )
    directory = tmp_path / "annotations"

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "invalid_annotation_record\n"
    assert not directory.exists()


@pytest.mark.parametrize(
    ("origin", "producer"),
    [
        ("model_proposal", "openai-codex"),
        ("hybrid_retrieval", "hybrid-pool-r0"),
    ],
)
def test_model_and_hybrid_retrieval_gold_origins_can_be_added_with_source(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    origin: str,
    producer: str,
) -> None:
    record, arguments = gold_record(
        tmp_path,
        corpus,
        gold_origins=[{"origin": origin, "producer": producer}],
    )

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(tmp_path / "annotations"),
            *arguments,
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["status"] == "stored"


@pytest.mark.parametrize("schema", ["annotation-l1/v1", "annotation-l2/v1", "unknown"])
def test_v1_and_unknown_annotation_schemas_are_refused_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    schema: str,
) -> None:
    record = tmp_path / "unsupported.json"
    record.write_text(json.dumps({"schema_version": schema}), encoding="utf-8")
    directory = tmp_path / "annotations"

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unsupported_annotation_schema\n"
    assert not directory.exists()


@pytest.mark.parametrize("schema", ["annotation-l1/v1", "unknown"])
def test_adding_a_valid_v2_record_refuses_an_unsupported_stored_schema(
    corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    schema: str,
) -> None:
    record, arguments = gold_record(tmp_path, corpus)
    directory = tmp_path / "annotations"
    directory.mkdir()
    (directory / f"{'a' * 64}.json").write_text(
        json.dumps({"schema_version": schema}), encoding="utf-8"
    )
    before = set(directory.iterdir())

    code = main(
        [
            "annotation", "add",
            "--record", str(record),
            "--annotation-dir", str(directory),
            *arguments,
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unsupported_annotation_schema\n"
    assert set(directory.iterdir()) == before
