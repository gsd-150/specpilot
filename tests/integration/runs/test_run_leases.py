from __future__ import annotations

import asyncio
import uuid
from asyncio import gather
from datetime import timedelta

import pytest

from specpilot.runs.contracts import RunStatus, TerminalEvent
from specpilot.runs.postgres import PostgresRunStore
from tests.integration.runs.test_postgres_store import Clock, _new_run, _seed_bindings

pytestmark = pytest.mark.integration


@pytest.mark.anyio
async def test_expired_queue_lease_projects_interrupted_at_exact_boundary(
    clean_ledger: str,
) -> None:
    """Catches using `< now` instead of `<= now` or mutating during a read."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    clock.advance(seconds=5)

    projected = await store.read_owned(created.run_id, created.session_id)

    assert projected is not None
    assert projected.status is RunStatus.INTERRUPTED
    assert projected.reason == "lease_expired"
    assert projected.completed_at is None
    assert projected.events[0].status is RunStatus.QUEUED  # type: ignore[union-attr]
    assert created.lease_expires_at == created.created_at + timedelta(seconds=5)

    import psycopg

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        row = await (
            await connection.execute(
                "SELECT status, completed_at FROM specpilot_run WHERE run_id = %s",
                (created.run_id,),
            )
        ).fetchone()
    assert row == ("queued", None)


@pytest.mark.anyio
async def test_expired_running_lease_projects_interrupted(clean_ledger: str) -> None:
    """Catches expiry projection being limited to queue-delivery failures."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)
    clock.advance(seconds=30)

    projected = await store.read_owned(created.run_id, created.session_id)

    assert projected is not None
    assert projected.status is RunStatus.INTERRUPTED
    assert projected.reason == "lease_expired"
    assert projected.started_at == created.created_at
    assert projected.completed_at is None


@pytest.mark.anyio
async def test_heartbeat_is_owner_scoped_active_and_monotonic(
    clean_ledger: str,
) -> None:
    """Catches stale/foreign heartbeats or a short refresh shrinking a lease."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)

    clock.advance(seconds=5)
    assert not await store.heartbeat(
        created.run_id, "worker-b", lease_seconds=120
    )
    assert await store.heartbeat(created.run_id, "worker-a", lease_seconds=1)

    import psycopg

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        row = await (
            await connection.execute(
                "SELECT lease_owner, lease_expires_at, last_heartbeat_at "
                "FROM specpilot_run WHERE run_id = %s",
                (created.run_id,),
            )
        ).fetchone()
    assert row == (
        "worker-a",
        created.created_at + timedelta(seconds=30),
        clock(),
    )
    clock.advance(seconds=25)
    assert not await store.heartbeat(created.run_id, "worker-a", lease_seconds=30)


@pytest.mark.anyio
async def test_reconcile_expired_is_concurrent_idempotent_and_skips_terminal(
    clean_ledger: str,
) -> None:
    """Catches duplicate terminal events or reconciliation rewriting terminals."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    queued = await store.create(_new_run(clock, session_id="owner-queued"))
    running = await store.create(_new_run(clock, session_id="owner-running"))
    terminal = await store.create(_new_run(clock, session_id="owner-terminal"))
    assert await store.claim(running.run_id, "worker-a", lease_seconds=5)
    assert await store.complete(
        terminal.run_id,
        "queue-delivery",
        TerminalEvent(
            sequence=1, status=RunStatus.FAILED, reason="queue_delivery_failed"
        ),
    )
    clock.advance(seconds=5)

    results = await gather(
        store.reconcile_expired(clock()),
        store.reconcile_expired(clock()),
    )
    third = await store.reconcile_expired(clock())

    assert sum(results) == 2
    assert third == 0
    for created in (queued, running):
        view = await store.read_owned(created.run_id, created.session_id)
        assert view is not None
        assert view.status is RunStatus.INTERRUPTED
        assert view.reason == "lease_expired"
        assert [event.kind.value for event in view.events].count("terminal") == 1
    terminal_view = await store.read_owned(terminal.run_id, terminal.session_id)
    assert terminal_view is not None
    assert terminal_view.status is RunStatus.FAILED
    assert terminal_view.reason == "queue_delivery_failed"


@pytest.mark.anyio
async def test_reconcile_skips_locked_row_then_next_invocation_persists_it(
    clean_ledger: str,
) -> None:
    """Catches treating SKIP LOCKED as permanent reconciliation."""
    import psycopg

    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    clock.advance(seconds=5)
    locked = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with (
            await psycopg.AsyncConnection.connect(clean_ledger) as connection,
            connection.transaction(),
        ):
            row = await (
                await connection.execute(
                    "SELECT run_id FROM specpilot_run WHERE run_id = %s FOR UPDATE",
                    (created.run_id,),
                )
            ).fetchone()
            assert row == (created.run_id,)
            locked.set()
            await release.wait()

    holder = asyncio.create_task(hold(), name=f"reconcile-holder-{uuid.uuid4().hex}")
    try:
        await asyncio.wait_for(locked.wait(), timeout=2)
        assert await store.reconcile_expired(clock()) == 0
    finally:
        release.set()
        await holder

    projected = await store.read_owned(created.run_id, created.session_id)
    assert projected is not None
    assert projected.status is RunStatus.INTERRUPTED
    assert projected.completed_at is None
    assert await store.reconcile_expired(clock()) == 1
    persisted = await store.read_owned(created.run_id, created.session_id)
    assert persisted is not None
    assert persisted.status is RunStatus.INTERRUPTED
    assert persisted.completed_at == clock()
    assert [event.kind.value for event in persisted.events].count("terminal") == 1
