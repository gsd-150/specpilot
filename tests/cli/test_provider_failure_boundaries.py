from __future__ import annotations

import pytest

import specpilot.cli as cli
from specpilot.answer.run import AnswerOutcome
from specpilot.contracts.answer import AnswerVerdict, RefusalReason, VerifiedAnswer
from specpilot.contracts.manifests import ProviderUse
from specpilot.egress.enforcer import EgressPolicyViolation
from specpilot.providers.transport import ProviderAttemptError, TransportReplayError


def test_answer_projection_prioritizes_provider_error_over_verdict() -> None:
    outcome = AnswerOutcome(
        verified=VerifiedAnswer(
            verdict=AnswerVerdict.REFUSED,
            refusal_reason=RefusalReason.EVIDENCE_INSUFFICIENT,
        ),
        reservation_id="res-1",
        replayed=False,
        request_size=None,
        provider_error="provider_timeout",
        parse_fault=None,
    )

    projected = cli._answer_outcome_projection(outcome)

    assert projected == {
        "status": "failed",
        "refusal_reason": None,
        "citation_faults": [],
        "provider_error": "provider_timeout",
    }


def test_answer_route_is_checked_against_manifest_before_adapter_selection() -> None:
    _, manifest = cli._fixture_manifest(
        provider_id="deepseek",
        use=ProviderUse.ONLINE_MAIN,
        endpoint_purpose="live-main",
    )

    with pytest.raises(EgressPolicyViolation) as caught:
        cli._authorized_answer_endpoint("judge", manifest)

    assert caught.value.code == "route_unauthorized"


def test_route_smoke_reports_provider_failure_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(*args: object, **kwargs: object) -> object:
        raise ProviderAttemptError("provider_timeout", "res-1", False)

    monkeypatch.setattr("specpilot.providers.transport.PolicyBoundTransport.send", fail)

    code = cli.main(
        [
            "provider",
            "route-smoke",
            "--fixture-only",
            "--route",
            "main",
            "--ledger-dsn",
            "postgresql://unused",
        ]
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_IO
    assert captured.out == ""
    assert captured.err.strip() == "failed:provider_timeout"


def test_route_smoke_reports_gate_rejection_as_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def reject(*args: object, **kwargs: object) -> object:
        raise EgressPolicyViolation("route_unauthorized", "closed gate")

    monkeypatch.setattr(
        "specpilot.providers.transport.PolicyBoundTransport.send", reject
    )

    code = cli.main(
        [
            "provider",
            "route-smoke",
            "--fixture-only",
            "--route",
            "main",
            "--ledger-dsn",
            "postgresql://unused",
        ]
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert captured.out == ""
    assert captured.err.strip() == "blocked:route_unauthorized"


def test_route_smoke_reports_closed_replay_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def replay(*args: object, **kwargs: object) -> object:
        raise TransportReplayError("res-1")

    monkeypatch.setattr(
        "specpilot.providers.transport.PolicyBoundTransport.send", replay
    )

    code = cli.main(
        [
            "provider",
            "route-smoke",
            "--fixture-only",
            "--route",
            "main",
            "--ledger-dsn",
            "postgresql://unused",
        ]
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_IO
    assert captured.out == ""
    assert captured.err.strip() == "failed:transport_replay_refused"
