from __future__ import annotations

import re
from pathlib import Path

import pytest

from specpilot.cli import main
from tests.cli.conftest import parse_stdout

# CI and smoke output must never look like a quality result. W0 measures nothing.
QUALITY_WORDS = re.compile(
    r"\b(recall|precision|accuracy|f1|macro|kappa|score|correct)\b",
    re.IGNORECASE,
)


def test_envelope_smoke_reports_the_maximum_legal_case_and_the_refusals(
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    code, out, err = run_cli(["egress", "envelope-smoke"], capsys)

    assert code == 0, err
    payload = parse_stdout(out)
    assert payload["status"] == "passed"
    assert payload["root_transmitted_tokens"] == 29_696
    assert payload["root_transmitted_bytes"] == 475_136
    assert payload["unique_disclosures"] == 17


def test_envelope_smoke_proves_one_over_each_limit_is_refused(
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    _, out, _ = run_cli(["egress", "envelope-smoke"], capsys)

    refusals = parse_stdout(out)["refusals"]

    assert isinstance(refusals, dict)
    assert refusals == {
        "one_more_excerpt": "root_unique_excerpts_exceeded",
        "one_more_toc_node": "toc_run_exceeded",
        "one_more_token_in_an_excerpt": "excerpt_bytes_exceeded",
        "one_more_byte_in_an_excerpt": "excerpt_bytes_exceeded",
    }, "the smoke must show each boundary refusing, not just the envelope passing"


def test_smoke_output_contains_nothing_that_reads_as_a_quality_metric(
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    _, out, err = run_cli(["egress", "envelope-smoke"], capsys)

    assert not QUALITY_WORDS.search(out), f"smoke output looks like a result: {out}"
    assert not QUALITY_WORDS.search(err)


def test_route_smoke_without_a_ledger_reports_blocked_rather_than_passing(
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    code, out, err = run_cli(
        ["provider", "route-smoke", "--fixture-only", "--route", "main"],
        capsys,
    )

    assert code != 0
    assert out == ""
    assert err.strip() == "ledger_not_configured", (
        "a missing dependency is a documented blocked result, never a quiet pass"
    )


def test_route_smoke_refuses_to_run_without_the_fixture_only_flag(
    capsys: pytest.CaptureFixture[str],
    run_cli,
) -> None:
    with pytest.raises(SystemExit):
        run_cli(["provider", "route-smoke", "--route", "main"], capsys)


# --- provider route-smoke mode selection ---------------------------------


def test_route_smoke_requires_an_explicit_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Neither mode may be the default.

    A fixture smoke proves nothing about a real route; a live smoke reaches a
    third party and costs money. Which one ran has to be a word someone typed.
    """
    with pytest.raises(SystemExit) as caught:
        main(["provider", "route-smoke", "--route", "main"])

    assert caught.value.code != 0


def test_route_smoke_refuses_both_modes_at_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "provider",
                "route-smoke",
                "--route",
                "main",
                "--fixture-only",
                "--live",
            ]
        )

    assert caught.value.code != 0


def test_live_without_a_key_refuses_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Named, because the operator has to know what to export.

    It stops before the ledger, so an absent key costs no reservation.
    """
    monkeypatch.delenv("SPECPILOT_MAIN_API_KEY", raising=False)

    exit_code = main(
        ["provider", "route-smoke", "--live", "--route", "main", "--ledger-dsn", ""]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err.strip() == "credential_missing:SPECPILOT_MAIN_API_KEY"
    assert captured.out == ""


def test_live_judge_names_its_own_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two routes must not share a key, so they must not share a variable."""
    monkeypatch.delenv("SPECPILOT_JUDGE_API_KEY", raising=False)

    exit_code = main(
        ["provider", "route-smoke", "--live", "--route", "judge", "--ledger-dsn", ""]
    )

    assert exit_code == 2
    assert (
        capsys.readouterr().err.strip()
        == "credential_missing:SPECPILOT_JUDGE_API_KEY"
    )


def test_a_live_route_can_only_ever_disclose_the_synthetic_document() -> None:
    """The boundary that makes a live smoke safe to run before any assessment.

    `--live` mints an authorized manifest so the transport has one, and that
    manifest names a real provider. What it cannot name is a real document: it
    is bound to `synthetic-fixture-spec`, the enforcer requires the request's
    `document_id` to equal the stored manifest's, and every excerpt must belong
    to the request's corpus manifest. Reaching RFC text needs a manifest for
    `ietf-rfc-9110`, which this command has no path to create.
    """
    from specpilot.cli import _fixture_manifest

    _, manifest = _fixture_manifest(provider_id="deepseek")

    assert manifest.document_id == "synthetic-fixture-spec"
    assert manifest.provider_route_binding.provider_id == "deepseek"
    assert manifest.cloud_egress_authorized is True, "the fixture route is self-signed"


def test_no_real_document_has_an_authorized_manifest_anywhere_in_the_repository(
    tmp_path,
) -> None:
    """The other half: nothing has authorized the real corpus, and nothing can.

    `manifest authorize` needs a completed assessment file, and the two frozen
    RFCs use `source-manifest/v2`, for which no successor path exists at all.
    """
    from specpilot.contracts.manifests import RfcSourceManifest
    from specpilot.manifests.store import ManifestStore

    store = ManifestStore(Path("manifests/local/r0/source"))
    for manifest_id in (
        "af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691",
        "3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2",
    ):
        stored = store.read_source(manifest_id)
        assert isinstance(stored, RfcSourceManifest)
        assert stored.cloud_egress_authorized is False
