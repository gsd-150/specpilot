from __future__ import annotations

import re

import pytest

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
        "one_more_token_in_an_excerpt": "excerpt_tokens_exceeded",
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
