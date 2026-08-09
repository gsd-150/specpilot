"""`annotation deep-review`: the check that has to produce something.

The forced choice shows four candidates. A deep read shows the whole section, so
the failure the choice cannot catch — a second clause that also answers — is on
screen. What comes back is a finding with clause ids and a measured duration,
not a flag that a banner was printed.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pytest

from specpilot.annotation.review import DeepReviewStore, deep_review_required
from specpilot.annotation.store import AnnotationStore
from specpilot.cli import main
from specpilot.contracts.annotation import L1Annotation
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import Clause, ClauseLimits, build_clauses
from specpilot.manifests.store import ManifestStore
from tests.helpers import rfc_factory

# Two paragraphs of §1.1 both bear on the same question — the shape a forced
# choice over four candidates can miss and a full-section read cannot.
DEEP_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Deep</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="status" numbered="true">
      <name>Status Codes</name>
      <t>A status code of three digits describes the result of the request.</t>
      <section anchor="notmod" numbered="true">
        <name>Not Modified</name>
        <t>The server generating this response must generate any of the
          following header fields that would have been sent in a successful
          response to the same request.</t>
        <t>Content-Location, Date, ETag, and Vary.</t>
        <t>A sender ought not generate representation metadata beyond those
          fields, since the recipient already holds a stored copy.</t>
      </section>
      <section anchor="other" numbered="true">
        <name>Elsewhere</name>
        <t>An unrelated paragraph about proxy behaviour and forwarding.</t>
      </section>
    </section>
  </middle>
</rfc>
"""

SALT = "r1-2026-08"
CANDIDATE = re.compile(r"^\s*\[([A-Z])\]\s+(GOLD|\s{4})\s+§(\S+)\s+¶(\d+)\s*$")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


def source(tmp_path: Path, workspace: Path) -> tuple[Path, list[str]]:
    xml = rfc_factory.write(workspace, "rfc9999.xml", DEEP_RFC_XML)
    directory = tmp_path / "manifests"
    manifest = ManifestStore(directory).create_source_v2(
        RfcSourceManifestDraft(
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            text_url="https://www.rfc-editor.org/rfc/rfc9999.txt",
            xml_url="https://www.rfc-editor.org/rfc/rfc9999.xml",
            text_sha256="a" * 64,
            xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest(),
            downloaded_at="2026-08-09T09:00:00Z",
            created_at="2026-08-09T09:01:00Z",
        )
    )
    return xml, [
        "--manifest", manifest.manifest_id,
        "--manifest-dir", str(directory),
        "--xml", str(xml),
    ]


def clauses_of(xml: Path) -> tuple[Clause, ...]:
    return build_clauses(xml, RfcLimits(), ClauseLimits())


def in_section(xml: Path, anchor: str) -> tuple[Clause, ...]:
    return tuple(c for c in clauses_of(xml) if c.section_anchor == anchor)


def sampled_item() -> str:
    """An item id the pre-registered sample actually contains."""
    for n in range(500):
        item = f"l1-dev-{n:03d}"
        if deep_review_required(item, rate=0.25, salt=SALT):
            return item
    raise AssertionError("no sampled id found")


def unsampled_item() -> str:
    for n in range(500):
        item = f"l1-dev-{n:03d}"
        if not deep_review_required(item, rate=0.25, salt=SALT):
            return item
    raise AssertionError("no unsampled id found")


def store_annotation(
    tmp_path: Path, xml: Path, item_id: str, **overrides: object
) -> tuple[Path, L1Annotation]:
    directory = tmp_path / "annotations"
    gold = in_section(xml, "notmod")[0]
    fields: dict[str, object] = {
        "item_id": item_id,
        "split": "dev",
        "question": "Which header fields must this response carry over?",
        "direction": "clause_first",
        "content_origin": "model",
        "label_origin": "mixed",
        "document_id": "ietf-rfc-9999",
        "document_version": "2026-08",
        "gold_clause_ids": (gold.clause_id,),
        "gold_section_paths": (gold.section_path,),
        "question_gold_jaccard": 0.2,
        "gold_origins": (
            {"origin": "model_proposal", "producer": "claude-opus-5"},
            {"origin": "human_source_review"},
        ),
        **overrides,
    }
    stored = AnnotationStore(directory).create(L1Annotation(**fields))
    assert isinstance(stored, L1Annotation)
    return directory, stored


def argv(tmp_path: Path, item_id: str, manifest: list[str], **extra: str) -> list[str]:
    return [
        "annotation", "deep-review",
        "--item", item_id,
        "--annotation-dir", str(tmp_path / "annotations"),
        "--deep-review-dir", str(tmp_path / "findings"),
        "--reviewer", "chunxue",
        "--deep-review-rate", extra.get("rate", "0.25"),
        "--deep-review-salt", SALT,
        *manifest,
    ]


def answer(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{text}\n"))


def sheet_of(
    tmp_path: Path,
    item: str,
    manifest: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Render the sheet without recording a finding for having looked.

    The probe answers "complete", which is a real finding, so it goes to a
    throwaway directory — otherwise reading the sheet counts as a deep review.
    """
    probe = argv(tmp_path, item, manifest)
    probe[probe.index("--deep-review-dir") + 1] = str(tmp_path / "probe-findings")
    answer(monkeypatch, "complete")
    main(probe)
    return capsys.readouterr().out


def offered(out: str) -> dict[str, tuple[str, str, int]]:
    found = {}
    for line in out.splitlines():
        matched = CANDIDATE.match(line)
        if matched:
            found[matched.group(1)] = (
                matched.group(2).strip(),
                matched.group(3),
                int(matched.group(4)),
            )
    return found


def test_the_whole_section_is_shown_with_the_gold_marked(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a blind test — the choice is already recorded. The job now is to see
    what else the section says, so the gold is labelled rather than hidden."""
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    answer(monkeypatch, "complete")

    code = main(argv(tmp_path, item, manifest))

    out = capsys.readouterr().out
    assert code == 0
    shown = offered(out)
    # Three paragraphs in §1.1, and nothing from §1 or §1.2.
    assert len(shown) == 3
    assert [mark for mark, _, _ in shown.values()].count("GOLD") == 1
    assert all(number == "1.1" for _, number, _ in shown.values())
    assert "unrelated paragraph about proxy" not in out


def test_confirming_completeness_records_a_finding_with_a_duration(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    answer(monkeypatch, "complete")

    main(argv(tmp_path, item, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "gold_complete"
    assert payload["clauses_examined"] == 3
    assert payload["scope"] == "section"
    assert payload["elapsed_seconds"] >= 0
    assert payload["amended_annotation_id"] is None

    stored = DeepReviewStore(tmp_path / "findings").read_all()
    assert [f.outcome.value for f in stored] == ["gold_complete"]
    assert stored[0].reviewer_id == "chunxue"


def test_naming_another_clause_extends_the_gold_and_records_why(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The l1-dev-010 shape: the obligation and its list are separate clauses."""
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    _, original = store_annotation(tmp_path, xml, item)
    sheet = sheet_of(tmp_path, item, manifest, capsys, monkeypatch)
    extra = next(
        letter for letter, (mark, _, _) in offered(sheet).items() if mark != "GOLD"
    )

    monkeypatch.setattr("sys.stdin", io.StringIO(f"{extra}\n"))
    code = main(argv(tmp_path, item, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 0
    assert payload["outcome"] == "gold_extended"
    assert payload["additional_gold_clause_count"] == 1
    assert payload["amended_annotation_id"] is not None

    amended = AnnotationStore(tmp_path / "annotations").read(
        payload["amended_annotation_id"]
    )
    assert len(amended.gold_clause_ids) == 2
    assert set(original.gold_clause_ids) <= set(amended.gold_clause_ids)
    assert amended.predecessor_annotation_id == original.annotation_id
    assert amended.adjudications


def test_several_clauses_can_be_added_at_once(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    sheet = sheet_of(tmp_path, item, manifest, capsys, monkeypatch)
    extras = [
        letter for letter, (mark, _, _) in offered(sheet).items() if mark != "GOLD"
    ]

    monkeypatch.setattr("sys.stdin", io.StringIO(",".join(extras) + "\n"))
    main(argv(tmp_path, item, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["additional_gold_clause_count"] == 2


def test_naming_only_the_clause_that_is_already_gold_writes_nothing(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recording `gold_extended` for that would report work it did not do."""
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    sheet = sheet_of(tmp_path, item, manifest, capsys, monkeypatch)
    already = next(
        letter for letter, (mark, _, _) in offered(sheet).items() if mark == "GOLD"
    )

    monkeypatch.setattr("sys.stdin", io.StringIO(f"{already}\n"))
    code = main(argv(tmp_path, item, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "no_new_clause_named\n"
    assert not (tmp_path / "findings").exists()


def test_an_item_outside_the_sample_is_refused(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep-reviewing an unsampled item is choosing the sample."""
    xml, manifest = source(tmp_path, workspace)
    item = unsampled_item()
    store_annotation(tmp_path, xml, item)
    answer(monkeypatch, "complete")

    code = main(argv(tmp_path, item, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "item_not_sampled\n"


def test_an_unanswerable_item_is_checked_against_literal_search(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no section to read; the claim is about the whole document.

    §8.2.3's completeness audit — pooling proposes, the human adjudicates — is
    exactly this, so literal search supplies the candidates.
    """
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(
        tmp_path,
        xml,
        item,
        expected_refusal=True,
        gold_clause_ids=(),
        gold_section_paths=(),
        question_gold_jaccard=None,
        gold_origins=(),
    )
    answer(monkeypatch, "complete")

    code = main(argv(tmp_path, item, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 0
    assert payload["scope"] == "literal_search"
    assert payload["clauses_examined"] >= 1
    assert payload["outcome"] == "gold_complete"


def test_an_unanswerable_item_cannot_have_gold_added_to_it(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding a clause that answers makes the item wrong, not extendable."""
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(
        tmp_path,
        xml,
        item,
        expected_refusal=True,
        gold_clause_ids=(),
        gold_section_paths=(),
        question_gold_jaccard=None,
        gold_origins=(),
    )
    answer(monkeypatch, "A")

    code = main(argv(tmp_path, item, manifest))

    assert code == 2
    assert capsys.readouterr().err == "invalid_choice\n"

    answer(monkeypatch, "wrong")
    main(argv(tmp_path, item, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "gold_wrong"
    assert payload["additional_gold_clause_count"] == 0


def test_a_flawed_question_is_a_recordable_finding(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    answer(monkeypatch, "flawed")

    main(argv(tmp_path, item, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "question_flawed"


def test_an_unreadable_answer_writes_nothing(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    answer(monkeypatch, "Z,Q")

    code = main(argv(tmp_path, item, manifest))

    assert code == 2
    assert capsys.readouterr().err == "invalid_choice\n"
    assert not (tmp_path / "findings").exists()


def test_an_item_that_was_never_annotated_is_refused(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    store_annotation(tmp_path, xml, sampled_item())
    answer(monkeypatch, "complete")

    code = main(argv(tmp_path, "l1-dev-999999", manifest))

    assert code == 2
    assert capsys.readouterr().err == "unknown_item\n"


def test_the_finding_holds_no_clause_text(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The section was printed. The record is locators and counts."""
    xml, manifest = source(tmp_path, workspace)
    item = sampled_item()
    store_annotation(tmp_path, xml, item)
    answer(monkeypatch, "complete")
    main(argv(tmp_path, item, manifest))
    capsys.readouterr()

    for path in (tmp_path / "findings").glob("*.json"):
        assert "header fields that would have been sent" not in path.read_text(
            encoding="utf-8"
        )
