"""`annotation review`: the only path by which a drafted proposal becomes gold.

The command presents the drafted question with its proposed clause hidden among
structurally near ones, takes one choice, and records what the choice was. A
reviewer who is not reading cannot score above chance, and disagreement with the
proposal is countable afterwards instead of being an unverifiable claim under
every downstream number.

This is the one command that prints clause text. It has to: nobody can choose
between four clauses without reading them. The text goes to a terminal and never
into a record — the stored annotation holds locators, as it always did.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pytest

from specpilot.annotation.review import ReviewStore
from specpilot.cli import main
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import Clause, ClauseLimits, build_clauses
from specpilot.manifests.store import ManifestStore
from tests.helpers import rfc_factory

REVIEW_RFC_XML = """<?xml version='1.0' encoding='utf-8'?>
<rfc number="9999" version="3">
  <front><title>Methods</title><date month="08" year="2026"/></front>
  <middle>
    <section anchor="methods" numbered="true">
      <name>Methods</name>
      <t>A server receiving an unrecognized method token responds with an
        error status rather than guessing what was meant.</t>
      <t>A method token is case sensitive, and the registry lists it in
        uppercase for that reason.</t>
      <section anchor="safe" numbered="true">
        <name>Safe Methods</name>
        <t>A safe method does not request a change of state on the origin
          server beyond what the request itself implies.</t>
        <t>Automatic retrieval software treats a safe method as harmless to
          follow without asking anyone first.</t>
      </section>
      <section anchor="idempotent" numbered="true">
        <name>Idempotent Methods</name>
        <t>An idempotent method may be repeated with no additional effect
          beyond that of the first attempt.</t>
        <t>A client may retry an idempotent request after a timeout without
          risking a duplicated outcome.</t>
      </section>
    </section>
    <section anchor="status" numbered="true">
      <name>Status Codes</name>
      <t>A status code of three digits describes the result of the request
        and the semantics of the response.</t>
    </section>
  </middle>
</rfc>
"""

QUESTION = "What does a server do with a method token it does not recognize?"
CANDIDATE = re.compile(r"^\s*\[([A-Z])\]\s+§(\S+)\s+¶(\d+)\s*$")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return directory


def source(tmp_path: Path, workspace: Path) -> tuple[Path, list[str]]:
    xml = rfc_factory.write(workspace, "rfc9999.xml", REVIEW_RFC_XML)
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


def gold_clause(xml: Path) -> Clause:
    """§1 ¶1 — the clause a drafted proposal would name for QUESTION."""
    return clauses_of(xml)[0]


def write_proposal(tmp_path: Path, xml: Path, **overrides: object) -> Path:
    path = tmp_path / "proposal.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "annotation-proposal/v1",
                "item_id": "l1-dev-001",
                "split": "dev",
                "question": QUESTION,
                "direction": "clause_first",
                "document_id": "ietf-rfc-9999",
                "document_version": "2026-08",
                "proposal_producer": "claude-opus-5",
                "proposed_gold_clause_id": gold_clause(xml).clause_id,
                "drafted_key_points": [
                    {"point_id": "kp-1", "criterion": "names an error status"}
                ],
                **overrides,
            }
        ),
        encoding="utf-8",
    )
    return path


def review_argv(
    tmp_path: Path,
    proposal: Path,
    manifest: list[str],
    *,
    seed: str = "r1-2026-08",
    rate: str = "0.0",
    distractors: str = "3",
) -> list[str]:
    return [
        "annotation", "review",
        "--proposal", str(proposal),
        "--annotation-dir", str(tmp_path / "annotations"),
        "--review-dir", str(tmp_path / "reviews"),
        "--reviewer", "chunxue",
        "--seed", seed,
        "--distractors", distractors,
        "--deep-review-rate", rate,
        "--deep-review-salt", "r1-2026-08",
        *manifest,
    ]


def offered(out: str) -> dict[str, tuple[str, int]]:
    """Map each printed letter to the locator it offers."""
    found = {}
    for line in out.splitlines():
        matched = CANDIDATE.match(line)
        if matched:
            found[matched.group(1)] = (matched.group(2), int(matched.group(3)))
    return found


def letter_of(out: str, clause: Clause) -> str:
    wanted = (clause.section_number, clause.ordinal)
    return next(letter for letter, at in offered(out).items() if at == wanted)


def answer(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{text}\n"))


def sheet_then_choose(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    choose: str = "gold",
    proposal_overrides: dict[str, object] | None = None,
    **argv_overrides: str,
) -> tuple[int, str, str]:
    """Run once to read the sheet, then again with the seed to answer it.

    The seed makes the second run's sheet identical to the first's, which is
    also why a mistyped answer costs a reviewer nothing.

    The probe run rejects, so it writes no annotation; its review decision goes
    to a throwaway directory so it does not show up as a real one.
    """
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml, **(proposal_overrides or {}))
    argv = review_argv(tmp_path, proposal, manifest, **argv_overrides)
    probe = list(argv)
    probe[probe.index("--review-dir") + 1] = str(tmp_path / "probe-reviews")

    answer(monkeypatch, "none")
    main(probe)
    sheet = capsys.readouterr().out
    if choose == "gold":
        choice = letter_of(sheet, gold_clause(xml))
    elif choose == "other":
        choice = next(
            letter
            for letter in offered(sheet)
            if letter != letter_of(sheet, gold_clause(xml))
        )
    else:
        choice = choose

    answer(monkeypatch, choice)
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_sheet_offers_the_gold_hidden_among_structural_neighbours(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    answer(monkeypatch, "none")

    main(review_argv(tmp_path, proposal, manifest))

    out = capsys.readouterr().out
    assert QUESTION in out
    assert len(offered(out)) == 4
    assert (
        gold_clause(xml).section_number,
        gold_clause(xml).ordinal,
    ) in offered(out).values()
    # The reviewer cannot choose without reading, so the text is on the sheet.
    assert "unrecognized method token" in out


def test_the_sheet_never_says_which_candidate_is_the_proposal(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A marked proposal is not a forced choice, it is a confirmation dialog."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    answer(monkeypatch, "none")

    main(review_argv(tmp_path, proposal, manifest))

    out = capsys.readouterr().out
    assert gold_clause(xml).clause_id not in out
    assert "proposed" not in out.lower()
    assert "claude-opus-5" not in out


def test_the_proposal_is_not_always_the_first_candidate(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reviewer who learns that position A is the proposal is back to approving."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)

    positions = set()
    for n in range(12):
        answer(monkeypatch, "none")
        main(review_argv(tmp_path, proposal, manifest, seed=f"seed-{n}"))
        positions.add(letter_of(capsys.readouterr().out, gold_clause(xml)))

    assert len(positions) > 1


def test_choosing_the_proposal_records_it_as_accepted(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, out, _ = sheet_then_choose(tmp_path, workspace, capsys, monkeypatch)

    payload = json.loads(out.splitlines()[-1])
    assert code == 0
    assert payload["outcome"] == "accepted_as_proposed"
    assert payload["chose_proposal"] is True
    assert payload["candidates_shown"] == 4
    assert len(payload["annotation_id"]) == 64

    stored = ReviewStore(tmp_path / "reviews").read_all()
    assert [item.outcome.value for item in stored] == ["accepted_as_proposed"]
    assert stored[0].reviewed_annotation_id == payload["annotation_id"]
    assert stored[0].reviewer_id == "chunxue"
    assert stored[0].proposal_producer == "claude-opus-5"


def test_choosing_another_candidate_changes_the_gold(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The disagreement is the whole point, so it is stored, not discarded."""
    code, out, _ = sheet_then_choose(
        tmp_path, workspace, capsys, monkeypatch, choose="other"
    )

    payload = json.loads(out.splitlines()[-1])
    assert code == 0
    assert payload["outcome"] == "gold_changed"
    assert payload["chose_proposal"] is False

    from specpilot.annotation.store import AnnotationStore

    record = AnnotationStore(tmp_path / "annotations").read(payload["annotation_id"])
    xml, _ = source(tmp_path, workspace)
    assert record.gold_clause_ids != (gold_clause(xml).clause_id,)
    assert len(record.gold_clause_ids) == 1


def test_choosing_none_rejects_the_item_and_writes_no_annotation(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejections are stored or the acceptance rate is 100% by construction."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    answer(monkeypatch, "none")

    code = main(review_argv(tmp_path, proposal, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 0
    assert payload["outcome"] == "item_rejected"
    assert payload["annotation_id"] is None
    assert not (tmp_path / "annotations").exists()

    stored = ReviewStore(tmp_path / "reviews").read_all()
    assert [item.outcome.value for item in stored] == ["item_rejected"]
    assert stored[0].reviewed_annotation_id is None


def test_a_sampled_item_is_labelled_before_the_choice_is_taken(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Told beforehand or the deep read happens after the mind is made up."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    answer(monkeypatch, "none")

    main(review_argv(tmp_path, proposal, manifest, rate="1.0"))

    out = capsys.readouterr().out
    assert "DEEP REVIEW" in out
    assert out.index("DEEP REVIEW") < out.index("[A]")
    assert json.loads(out.splitlines()[-1])["deep_reviewed"] is True


def test_an_unsampled_item_is_not_labelled(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    answer(monkeypatch, "none")

    main(review_argv(tmp_path, proposal, manifest, rate="0.0"))

    out = capsys.readouterr().out
    assert "DEEP REVIEW" not in out
    assert json.loads(out.splitlines()[-1])["deep_reviewed"] is False


def test_the_proposal_file_is_never_stored_as_a_record(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proposal is a file. Only a reviewed record enters the store."""
    _, out, _ = sheet_then_choose(tmp_path, workspace, capsys, monkeypatch)
    payload = json.loads(out.splitlines()[-1])

    written = list((tmp_path / "annotations").glob("*.json"))
    assert len(written) == 1
    stored = json.loads(written[0].read_text(encoding="utf-8"))
    assert stored["schema_version"] == "annotation-l1/v2"
    assert "proposal_producer" not in stored
    assert "drafted_key_points" not in stored
    assert "proposed_gold_clause_id" not in stored
    assert payload["annotation_id"] == written[0].stem


def test_the_stored_record_holds_no_clause_text(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sheet showed the text. The record is still locators only."""
    sheet_then_choose(tmp_path, workspace, capsys, monkeypatch)

    for path in (tmp_path / "annotations").glob("*.json"):
        assert "unrecognized method token" not in path.read_text(encoding="utf-8")
    for path in (tmp_path / "reviews").glob("*.json"):
        assert "unrecognized method token" not in path.read_text(encoding="utf-8")


def test_edited_key_points_are_computed_from_the_file_not_claimed(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking "did you edit them?" is the self-report this plan exists to remove.

    The drafted copy stays in the file untouched; the reviewer edits the other
    one; the difference is a fact about two lists.
    """
    _, untouched, _ = sheet_then_choose(tmp_path, workspace, capsys, monkeypatch)
    assert json.loads(untouched.splitlines()[-1])["key_points_edited"] is False

    _, edited, _ = sheet_then_choose(
        tmp_path,
        workspace,
        capsys,
        monkeypatch,
        proposal_overrides={
            "item_id": "l1-dev-002",
            "key_points": [
                {"point_id": "kp-1", "criterion": "names the 501 status"}
            ],
        },
    )
    assert json.loads(edited.splitlines()[-1])["key_points_edited"] is True


def test_an_unanswerable_proposal_is_confirmed_with_no_candidates(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirming that nothing answers a question is a different act, not a choice."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(
        tmp_path,
        xml,
        proposed_gold_clause_id=None,
        expected_refusal=True,
        drafted_key_points=[],
    )
    answer(monkeypatch, "confirm")

    code = main(review_argv(tmp_path, proposal, manifest))

    out = capsys.readouterr().out
    payload = json.loads(out.splitlines()[-1])
    assert code == 0
    assert offered(out) == {}
    assert payload["candidates_shown"] == 0
    assert payload["unanswerable"] is True
    assert payload["outcome"] == "accepted_as_proposed"

    from specpilot.annotation.store import AnnotationStore

    record = AnnotationStore(tmp_path / "annotations").read(payload["annotation_id"])
    assert record.expected_refusal is True
    assert record.gold_clause_ids == ()
    assert record.gold_origins == ()


def test_an_unanswerable_proposal_can_be_rejected_too(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewer who finds a clause that does answer it says so."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(
        tmp_path,
        xml,
        proposed_gold_clause_id=None,
        expected_refusal=True,
        drafted_key_points=[],
    )
    answer(monkeypatch, "none")

    code = main(review_argv(tmp_path, proposal, manifest))

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 0
    assert payload["outcome"] == "item_rejected"
    assert payload["unanswerable"] is True
    assert not (tmp_path / "annotations").exists()


def test_a_choice_the_sheet_did_not_offer_writes_nothing(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    answer(monkeypatch, "Z")

    code = main(review_argv(tmp_path, proposal, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "invalid_choice\n"
    assert not (tmp_path / "annotations").exists()
    assert not (tmp_path / "reviews").exists()


def test_a_proposed_clause_the_document_does_not_contain_is_refused(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml, proposed_gold_clause_id="f" * 64)
    answer(monkeypatch, "A")

    code = main(review_argv(tmp_path, proposal, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "unknown_gold_clause\n"


def test_a_proposal_naming_another_document_is_refused(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml, document_id="ietf-rfc-9110")
    answer(monkeypatch, "A")

    code = main(review_argv(tmp_path, proposal, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "document_id_mismatch\n"


def test_a_key_point_that_restates_its_clause_is_refused_here_too(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review writes through the same source-checked entry path as `add`."""
    xml, manifest = source(tmp_path, workspace)
    restating = (
        "A method token is case sensitive, and the registry lists it in "
        "uppercase for that reason."
    )
    proposal = write_proposal(
        tmp_path,
        xml,
        proposed_gold_clause_id=clauses_of(xml)[1].clause_id,
        drafted_key_points=[{"point_id": "kp-1", "criterion": restating}],
    )
    answer(monkeypatch, "none")
    main(review_argv(tmp_path, proposal, manifest))
    sheet = capsys.readouterr().out
    answer(monkeypatch, letter_of(sheet, clauses_of(xml)[1]))

    code = main(review_argv(tmp_path, proposal, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "key_point_restates_clause\n"
    assert not (tmp_path / "annotations").exists()


def test_a_proposal_the_contract_rejects_writes_nothing(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answerable proposal with no clause to propose is the mistake here."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml, proposed_gold_clause_id=None)
    answer(monkeypatch, "A")

    code = main(review_argv(tmp_path, proposal, manifest))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "invalid_proposal\n"


def test_the_run_records_what_it_would_take_to_reconstruct_the_sheet(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed, rate, and salt, because a sample nobody can recompute is a claim."""
    _, out, _ = sheet_then_choose(tmp_path, workspace, capsys, monkeypatch)

    payload = json.loads(out.splitlines()[-1])
    assert payload["seed"] == "r1-2026-08"
    assert payload["deep_review_salt"] == "r1-2026-08"
    assert payload["deep_review_rate"] == 0.0
    assert payload["distractor_tiers"] == {"same_section": 3}


def test_reviewing_the_same_item_twice_keeps_both_decisions(
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change of mind is a real event and losing the first would hide it."""
    xml, manifest = source(tmp_path, workspace)
    proposal = write_proposal(tmp_path, xml)
    argv = review_argv(tmp_path, proposal, manifest)

    answer(monkeypatch, "none")
    main(argv)
    sheet = capsys.readouterr().out
    answer(monkeypatch, letter_of(sheet, gold_clause(xml)))
    main(argv)
    capsys.readouterr()

    stored = ReviewStore(tmp_path / "reviews").read_all()
    assert {item.outcome.value for item in stored} == {
        "item_rejected",
        "accepted_as_proposed",
    }
