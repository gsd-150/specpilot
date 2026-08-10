from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
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
def qdrant_url() -> str:
    """URL of a throwaway Qdrant for the collection tests.

    Loud for the same reason as the ledger DSN. These tests are the only
    evidence that the collection schema, the point count, and the read-only
    posture after freezing behave against a real server, and §6.4 makes all
    three load-time refusal conditions. A silent skip would let the corpus
    manifest look verified when nothing was exercised.
    """
    url = os.environ.get("SPECPILOT_TEST_QDRANT_URL")
    if not url:
        pytest.skip(
            "SPECPILOT_TEST_QDRANT_URL is unset: collection schema, point "
            "count, and frozen-collection evidence was NOT produced by this run"
        )
    return url


@pytest.fixture(scope="session")
def migrated_dsn(ledger_dsn: str) -> Iterator[str]:
    """Every migration, in filename order.

    This named one file. A second migration therefore left the test schema a
    version behind production, and the suite still passed on any database that
    happened to have been migrated by hand — which is how the columns renamed in
    002 were exercised locally and would have failed on a fresh CI database.
    Discovering that needs a clean database, so the directory is read rather
    than a filename repeated.
    """
    psycopg = pytest.importorskip("psycopg")
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migrations:
        raise AssertionError(f"no migrations found in {MIGRATIONS_DIR}")
    with psycopg.connect(ledger_dsn, autocommit=True) as connection:
        for migration in migrations:
            connection.execute(migration.read_text(encoding="utf-8"))
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
