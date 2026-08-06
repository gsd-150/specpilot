from __future__ import annotations

import json

import pytest

from specpilot.cli import main

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("route", "expected_use"),
    [("main", "online_main"), ("judge", "offline_judge")],
)
def test_route_smoke_exercises_the_route_it_names(
    clean_ledger: str,
    capsys: pytest.CaptureFixture[str],
    route: str,
    expected_use: str,
) -> None:
    """--route must change the route, not just the label on the output.

    A judge smoke that quietly runs the online chain would be false evidence for
    the go/no-go checklist, which asks whether both routes were smoked.
    """
    code = main(
        [
            "provider",
            "route-smoke",
            "--fixture-only",
            "--route",
            route,
            "--ledger-dsn",
            clean_ledger,
        ]
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "passed"
    assert payload["route"] == route
    assert payload["provider_use"] == expected_use


def test_route_smoke_states_what_it_does_not_prove(
    clean_ledger: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(
        [
            "provider",
            "route-smoke",
            "--fixture-only",
            "--route",
            "main",
            "--ledger-dsn",
            clean_ledger,
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["adapter"] == "fixture"
    assert "does_not_prove" in payload, (
        "a fixture pass that does not say what it excludes will be read as "
        "evidence for route A"
    )
