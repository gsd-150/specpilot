from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from specpilot.cli import EXIT_IO, EXIT_REFUSED, EXIT_USAGE, main
from specpilot.egress.ledger import PolicyRebindAmbiguous
from specpilot.egress.policy import EgressPolicy
from specpilot.egress.postgres import PostgresEgressLedger
from tests.integration.egress.test_postgres_reservation import reservation_for
from tests.unit.egress.test_disclosure_caps import distinct_excerpt
from tests.unit.egress.test_policy_projection import (
    NOW,
    FixtureTokenCounter,
    fixture_policy,
    fixture_store,
)

pytestmark = pytest.mark.integration


def _write_policy(tmp_path: Path, policy: EgressPolicy) -> Path:
    policy_path = tmp_path / "changed-policy.json"
    policy_path.write_text(policy.model_dump_json(), encoding="utf-8")
    return policy_path


def _rebind_arguments(
    clean_ledger: str,
    tmp_path: Path,
    policy_path: Path,
    corpus_manifest_id: str,
    expected_policy_hash: str,
) -> list[str]:
    return [
        "egress",
        "rebind-policy",
        "--ledger-dsn",
        clean_ledger,
        "--manifest-dir",
        str(tmp_path / "manifests"),
        "--policy",
        str(policy_path),
        "--corpus-manifest-id",
        corpus_manifest_id,
        "--expected-policy-hash",
        expected_policy_hash,
    ]


def _seed_usage(clean_ledger: str, policy: EgressPolicy) -> str:
    request = reservation_for(distinct_excerpt(1))

    async def seed() -> None:
        ledger = PostgresEgressLedger(
            clean_ledger,
            policy=policy,
            manifests=fixture_store(),
            clock=lambda: NOW,
        )
        await ledger.check_and_reserve(
            request,
            FixtureTokenCounter(),
            idempotency_key="cli-rebind-seed",
        )

    asyncio.run(seed())
    return request.version.corpus_manifest_id


def test_rebind_policy_emits_only_sanitized_epoch_identifiers_and_totals(
    clean_ledger: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    corpus_id = _seed_usage(clean_ledger, old_policy)
    policy_path = _write_policy(tmp_path, new_policy)

    code = main(
        _rebind_arguments(
            clean_ledger,
            tmp_path,
            policy_path,
            corpus_id,
            old_policy.policy_hash,
        )
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload == {
        "status": "rebound",
        "corpus_manifest_id": corpus_id,
        "predecessor_ledger_id": payload["predecessor_ledger_id"],
        "successor_ledger_id": payload["successor_ledger_id"],
        "old_policy_hash": old_policy.policy_hash,
        "new_policy_hash": new_policy.policy_hash,
        "inherited_unique_excerpts": 1,
        "inherited_unique_tokens": 2,
        "inherited_unique_bytes": 16,
    }
    assert captured.err == ""
    assert uuid.UUID(payload["predecessor_ledger_id"])
    assert uuid.UUID(payload["successor_ledger_id"])


def test_rebind_policy_wrong_expected_hash_prints_only_the_conflict_code(
    clean_ledger: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_policy = fixture_policy()
    new_policy = old_policy.model_copy(update={"toc_per_run": 25})
    corpus_id = _seed_usage(clean_ledger, old_policy)
    policy_path = _write_policy(tmp_path, new_policy)
    wrong_hash = "0" * 64 if old_policy.policy_hash != "0" * 64 else "1" * 64

    code = main(
        _rebind_arguments(
            clean_ledger,
            tmp_path,
            policy_path,
            corpus_id,
            wrong_hash,
        )
    )
    captured = capsys.readouterr()

    assert code == EXIT_REFUSED
    assert captured.out == ""
    assert captured.err == "corpus_policy_rebind_conflict\n"


def test_rebind_policy_ambiguous_commit_prints_only_the_stable_io_code(
    clean_ledger: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = fixture_policy()
    policy_path = _write_policy(tmp_path, policy)

    async def raise_ambiguous(
        self: PostgresEgressLedger,
        corpus_manifest_id: str,
        *,
        expected_policy_hash: str,
    ) -> None:
        raise PolicyRebindAmbiguous()

    monkeypatch.setattr(PostgresEgressLedger, "rebind_policy", raise_ambiguous)

    code = main(
        _rebind_arguments(
            clean_ledger,
            tmp_path,
            policy_path,
            "c" * 64,
            policy.policy_hash,
        )
    )
    captured = capsys.readouterr()

    assert code == EXIT_IO
    assert captured.out == ""
    assert captured.err == "policy_rebind_ambiguous\n"


def test_rebind_policy_unreadable_policy_hides_the_local_path_and_skips_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_sentinel = "private-policy-path-sentinel"
    missing_policy = tmp_path / f"{path_sentinel}.json"

    def fail_if_database_is_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("database was constructed before policy loading finished")

    monkeypatch.setattr(
        "specpilot.egress.postgres.PostgresEgressLedger",
        fail_if_database_is_constructed,
    )

    code = main(
        _rebind_arguments(
            "postgresql://database-must-not-be-contacted",
            tmp_path,
            missing_policy,
            "c" * 64,
            "d" * 64,
        )
    )
    captured = capsys.readouterr()

    assert code == EXIT_IO
    assert captured.out == ""
    assert captured.err == "egress_policy_unavailable\n"
    assert path_sentinel not in captured.out + captured.err


def test_rebind_policy_invalid_policy_hides_input_and_skips_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_sentinel = "private-policy-content-sentinel"
    invalid_policy = tmp_path / "invalid-policy.json"
    invalid_policy.write_text(
        json.dumps({"private_field": content_sentinel}),
        encoding="utf-8",
    )

    def fail_if_database_is_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("database was constructed before policy loading finished")

    monkeypatch.setattr(
        "specpilot.egress.postgres.PostgresEgressLedger",
        fail_if_database_is_constructed,
    )

    code = main(
        _rebind_arguments(
            "postgresql://database-must-not-be-contacted",
            tmp_path,
            invalid_policy,
            "c" * 64,
            "d" * 64,
        )
    )
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "invalid_egress_policy\n"
    assert content_sentinel not in captured.out + captured.err
