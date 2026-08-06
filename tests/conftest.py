from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "001_egress_ledger.sql"
)
_TABLES = (
    "egress_run_seal",
    "egress_attempt",
    "egress_route_disclosure",
    "egress_reservation_disclosure",
    "egress_reservation",
    "egress_evaluation_root",
    "egress_corpus_ledger",
    "egress_policy_snapshot",
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def ledger_dsn() -> str:
    """DSN of a throwaway PostgreSQL for the ledger tests.

    Skipping is loud on purpose. These tests are the only evidence that the
    caps hold under concurrency and survive a restart, so a silent skip would
    let W0's hard gate look satisfied when nothing was actually exercised.
    """
    dsn = os.environ.get("SPECPILOT_TEST_DSN")
    if not dsn:
        pytest.skip(
            "SPECPILOT_TEST_DSN is unset: ledger reservation, concurrency, and "
            "recovery evidence was NOT produced by this run"
        )
    return dsn


@pytest.fixture(scope="session")
def migrated_dsn(ledger_dsn: str) -> Iterator[str]:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    yield ledger_dsn


@pytest.fixture
def clean_ledger(migrated_dsn: str) -> Iterator[str]:
    """Sync on purpose: a TRUNCATE needs no event loop, and the CLI entry point
    is synchronous, so an async fixture would exclude it."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(migrated_dsn, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE"
        )
    yield migrated_dsn
