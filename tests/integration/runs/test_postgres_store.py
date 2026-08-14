from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from specpilot.runs.contracts import (
    AgentName,
    AgentStepEvent,
    AgentStepPhase,
    PlanSummaryEvent,
    RunRecord,
    RunStatus,
    StateTransitionEvent,
    TerminalEvent,
    VerifierCheckSummary,
    VerifierSummaryEvent,
)
from specpilot.runs.postgres import (
    PostgresRunStore,
    RunStoreIntegrityError,
    RunStoreUnavailable,
    RunStoreValidationError,
)

pytestmark = pytest.mark.integration

_POLICY_HASH = "a" * 64
_CORPUS_MANIFEST_ID = "b" * 64


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


async def _seed_bindings(dsn: str) -> None:
    import psycopg

    ledger_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        await connection.execute(
            "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
            "VALUES (%s, 'egress-policy/v1')",
            (_POLICY_HASH,),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger "
            "(corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage, "
            "unique_excerpts, unique_tokens, unique_bytes) "
            "VALUES (%s, %s, %s, '{}', 0, 0, 0)",
            (ledger_id, _CORPUS_MANIFEST_ID, _POLICY_HASH),
        )
        await connection.execute(
            "INSERT INTO egress_corpus_ledger_head "
            "(corpus_manifest_id, corpus_ledger_id) VALUES (%s, %s)",
            (_CORPUS_MANIFEST_ID, ledger_id),
        )
        await connection.commit()


def _new_run(clock: Clock, *, session_id: str = "owner-a") -> RunRecord:
    return RunRecord(
        run_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=session_id,
        task_level="L1",
        profile="fixture",
        source_manifest_id="c" * 64,
        corpus_manifest_id=_CORPUS_MANIFEST_ID,
        policy_hash=_POLICY_HASH,
        configuration_hash="d" * 64,
        prompt_id="l1-answer-v1",
        prompt_hash="e" * 64,
        provider_id="provider-a",
        model_id="model-a",
        query_hash="f" * 64,
        status=RunStatus.QUEUED,
        terminal_reason=None,
        created_at=clock(),
        started_at=None,
        completed_at=None,
        lease_owner="caller-placeholder",
        lease_expires_at=clock() + timedelta(seconds=1),
        last_heartbeat_at=None,
    )


def _named_dsn(dsn: str, application_name: str) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(dsn, application_name=application_name)


async def _hold_run_row(
    dsn: str,
    run_id: uuid.UUID,
    locked: asyncio.Event,
    release: asyncio.Event,
    holder_pid: asyncio.Future[int],
) -> None:
    import psycopg

    async with (
        await psycopg.AsyncConnection.connect(dsn) as connection,
        connection.transaction(),
    ):
        row = await (
            await connection.execute(
                "SELECT run_id FROM specpilot_run WHERE run_id = %s FOR UPDATE",
                (run_id,),
            )
        ).fetchone()
        assert row == (run_id,)
        pid_row = await (
            await connection.execute("SELECT pg_backend_pid()")
        ).fetchone()
        assert pid_row is not None
        holder_pid.set_result(int(pid_row[0]))
        locked.set()
        await release.wait()


async def _wait_for_run_lock(
    dsn: str,
    application_name: str,
    task: asyncio.Task[Any],
    blocker_pids: set[int],
) -> int:
    import psycopg

    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True
    ) as connection:
        for _ in range(200):
            row = await (
                await connection.execute(
                    "SELECT pid, wait_event_type, query, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE application_name = %s "
                    "AND state = 'active'",
                    (application_name,),
                )
            ).fetchone()
            if (
                row is not None
                and row[1] == "Lock"
                and "specpilot_run" in str(row[2])
                and "FOR UPDATE" in str(row[2])
                and blocker_pids.intersection(row[3])
            ):
                assert not task.done()
                return int(row[0])
            if task.done():
                raise AssertionError(
                    f"{application_name} completed before reaching the held run lock"
                )
            await asyncio.sleep(0.01)
    raise AssertionError(f"{application_name} never waited on the held run lock")


async def _wait_for_application_to_disappear(
    dsn: str, application_name: str
) -> None:
    import psycopg

    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True
    ) as connection:
        for _ in range(200):
            count = await (
                await connection.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name = %s",
                    (application_name,),
                )
            ).fetchone()
            assert count is not None
            if count[0] == 0:
                return
            await asyncio.sleep(0.01)
    raise AssertionError(f"cancelled {application_name} connection remained open")


async def _force_run_schedule(
    dsn: str,
    run_id: uuid.UUID,
    first_application_name: str,
    first: Callable[[], Awaitable[Any]],
    second_application_name: str,
    second: Callable[[], Awaitable[Any]],
) -> tuple[object, object]:
    locked = asyncio.Event()
    release = asyncio.Event()
    holder_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    holder = asyncio.create_task(
        _hold_run_row(dsn, run_id, locked, release, holder_pid)
    )
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=2)
        blocker_pid = await asyncio.wait_for(holder_pid, timeout=2)
        first_task = asyncio.ensure_future(first())
        first_pid = await _wait_for_run_lock(
            dsn, first_application_name, first_task, {blocker_pid}
        )
        second_task = asyncio.ensure_future(second())
        await _wait_for_run_lock(
            dsn,
            second_application_name,
            second_task,
            {blocker_pid, first_pid},
        )
    except BaseException:
        release.set()
        await holder
        started = [task for task in (first_task, second_task) if task is not None]
        if started:
            await asyncio.gather(*started, return_exceptions=True)
        raise
    release.set()
    await holder
    assert first_task is not None and second_task is not None
    outcomes = await asyncio.gather(first_task, second_task, return_exceptions=True)
    return outcomes[0], outcomes[1]


async def _expire_while_waiting_for_run_lock(
    dsn: str,
    run_id: uuid.UUID,
    application_name: str,
    operation: Callable[[], Awaitable[Any]],
    clock: Clock,
    *,
    seconds: int,
) -> object:
    locked = asyncio.Event()
    release = asyncio.Event()
    holder_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    holder = asyncio.create_task(
        _hold_run_row(dsn, run_id, locked, release, holder_pid)
    )
    task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=2)
        blocker_pid = await asyncio.wait_for(holder_pid, timeout=2)
        task = asyncio.create_task(operation())
        await _wait_for_run_lock(dsn, application_name, task, {blocker_pid})
        clock.advance(seconds=seconds)
    finally:
        release.set()
        await holder
    assert task is not None
    return await task


@pytest.mark.anyio
async def test_create_assigns_queue_lease_and_owner_read_hides_foreign_run(
    clean_ledger: str,
) -> None:
    """Catches caller leases leaking through create or a run-id-only owner read."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)

    created = await store.create(_new_run(clock))

    assert created.lease_owner == "queue-delivery"
    assert created.lease_expires_at == clock() + timedelta(seconds=5)
    assert await store.read_owned(created.run_id, "owner-b") is None
    assert await store.read_owned(uuid.uuid4(), "owner-b") is None
    owned = await store.read_owned(created.run_id, "owner-a")
    assert owned is not None
    assert owned.status is RunStatus.QUEUED


@pytest.mark.anyio
async def test_two_claimers_have_exactly_one_winner(clean_ledger: str) -> None:
    """Catches a read-then-write claim race that starts one run twice."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    seed_store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    first_name = f"run-claim-first-{uuid.uuid4().hex}"
    second_name = f"run-claim-second-{uuid.uuid4().hex}"
    first_store = PostgresRunStore(
        _named_dsn(clean_ledger, first_name), clock=clock, queue_lease_seconds=5
    )
    second_store = PostgresRunStore(
        _named_dsn(clean_ledger, second_name), clock=clock, queue_lease_seconds=5
    )
    created = await seed_store.create(_new_run(clock))

    results = await _force_run_schedule(
        clean_ledger,
        created.run_id,
        first_name,
        lambda: first_store.claim(created.run_id, "worker-a", lease_seconds=30),
        second_name,
        lambda: second_store.claim(created.run_id, "worker-b", lease_seconds=30),
    )

    assert sorted(results) == [False, True]
    view = await seed_store.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert view.status is RunStatus.RUNNING
    assert [event.sequence for event in view.events] == [1, 2]
    assert view.events[1].status is RunStatus.RUNNING  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_concurrent_append_allocates_unique_gapless_sequences(
    clean_ledger: str,
) -> None:
    """Catches MAX(sequence)+1 allocation without holding the run-row lock."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    seed_store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    first_name = f"run-append-first-{uuid.uuid4().hex}"
    second_name = f"run-append-second-{uuid.uuid4().hex}"
    first_store = PostgresRunStore(
        _named_dsn(clean_ledger, first_name), clock=clock, queue_lease_seconds=5
    )
    second_store = PostgresRunStore(
        _named_dsn(clean_ledger, second_name), clock=clock, queue_lease_seconds=5
    )
    created = await seed_store.create(_new_run(clock))
    assert await seed_store.claim(created.run_id, "worker-a", lease_seconds=30)
    first_event = PlanSummaryEvent(
        sequence=1, plan_id="plan-a", step_count=1, max_tool_calls=2
    )
    second_event = PlanSummaryEvent(
        sequence=1, plan_id="plan-b", step_count=2, max_tool_calls=3
    )

    first, second = await _force_run_schedule(
        clean_ledger,
        created.run_id,
        first_name,
        lambda: first_store.append(
            created.run_id, "worker-a", first_event
        ),
        second_name,
        lambda: second_store.append(
            created.run_id, "worker-a", second_event
        ),
    )

    assert isinstance(first, PlanSummaryEvent)
    assert isinstance(second, PlanSummaryEvent)
    assert {first.sequence, second.sequence} == {3, 4}
    view = await seed_store.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert [event.sequence for event in view.events] == [1, 2, 3, 4]


@pytest.mark.anyio
async def test_audit_anchor_dedup_preserves_identical_checks_for_two_claims(
    clean_ledger: str,
) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)
    summary = VerifierSummaryEvent(
        sequence=1,
        checks=(
            VerifierCheckSummary(
                evidence_id="1" * 64, passed=True, fault_code=None
            ),
        ),
        duration_ms=0,
    )

    def identity(step_id: str) -> AgentStepEvent:
        return AgentStepEvent(
            sequence=1,
            agent=AgentName.VERIFIER,
            step_id=step_id,
            phase=AgentStepPhase.FINISHED,
            duration_ms=0,
            error_code=None,
        )

    first = identity("audit-" + "1" * 64)
    second = identity("audit-" + "2" * 64)
    assert len(
        await store.append_many_once(
            created.run_id, "worker-a", (first, summary), anchor=first
        )
    ) == 2
    assert await store.append_many_once(
        created.run_id, "worker-a", (first, summary), anchor=first
    ) == ()
    assert len(
        await store.append_many_once(
            created.run_id, "worker-a", (second, summary), anchor=second
        )
    ) == 2

    view = await store.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert sum(isinstance(event, VerifierSummaryEvent) for event in view.events) == 2


@pytest.mark.anyio
async def test_cancelled_lock_wait_closes_connection(clean_ledger: str) -> None:
    """Catches cancellation leaking a backend or a transaction waiting on a lock."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    seed_store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await seed_store.create(_new_run(clock))
    application_name = f"run-cancelled-claim-{uuid.uuid4().hex}"
    cancelled_store = PostgresRunStore(
        _named_dsn(clean_ledger, application_name),
        clock=clock,
        queue_lease_seconds=5,
    )
    locked = asyncio.Event()
    release = asyncio.Event()
    holder_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    holder = asyncio.create_task(
        _hold_run_row(clean_ledger, created.run_id, locked, release, holder_pid)
    )
    claim_task: asyncio.Task[bool] | None = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=2)
        blocker_pid = await asyncio.wait_for(holder_pid, timeout=2)
        claim_task = asyncio.create_task(
            cancelled_store.claim(
                created.run_id, "worker-cancelled", lease_seconds=30
            )
        )
        await _wait_for_run_lock(
            clean_ledger, application_name, claim_task, {blocker_pid}
        )
        claim_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await claim_task
        await _wait_for_application_to_disappear(clean_ledger, application_name)
    finally:
        if claim_task is not None and not claim_task.done():
            claim_task.cancel()
            await asyncio.gather(claim_task, return_exceptions=True)
        release.set()
        await holder

    assert await seed_store.claim(created.run_id, "worker-a", lease_seconds=30)


@pytest.mark.anyio
async def test_claim_rechecks_clock_after_waiting_for_lock(clean_ledger: str) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    seed = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await seed.create(_new_run(clock))
    name = f"run-stale-claim-{uuid.uuid4().hex}"
    contender = PostgresRunStore(_named_dsn(clean_ledger, name), clock=clock)
    result = await _expire_while_waiting_for_run_lock(
        clean_ledger,
        created.run_id,
        name,
        lambda: contender.claim(created.run_id, "worker-a", lease_seconds=30),
        clock,
        seconds=5,
    )
    assert result is False
    view = await seed.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert len(view.events) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["append", "complete", "heartbeat"])
async def test_append_complete_and_heartbeat_recheck_clock_after_lock(
    clean_ledger: str, operation: str
) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    seed = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await seed.create(_new_run(clock, session_id=f"owner-{operation}"))
    assert await seed.claim(created.run_id, "worker-a", lease_seconds=5)
    name = f"run-stale-{operation}-{uuid.uuid4().hex}"
    contender = PostgresRunStore(_named_dsn(clean_ledger, name), clock=clock)
    if operation == "append":
        call = lambda: contender.append(  # noqa: E731
            created.run_id,
            "worker-a",
            PlanSummaryEvent(
                sequence=1, plan_id="plan-a", step_count=1, max_tool_calls=1
            ),
        )
    elif operation == "complete":
        call = lambda: contender.complete(  # noqa: E731
            created.run_id,
            "worker-a",
            TerminalEvent(
                sequence=1, status=RunStatus.FAILED, reason="provider_timeout"
            ),
        )
    else:
        call = lambda: contender.heartbeat(  # noqa: E731
            created.run_id, "worker-a", lease_seconds=30
        )
    if operation == "append":
        with pytest.raises(RunStoreValidationError):
            await _expire_while_waiting_for_run_lock(
                clean_ledger, created.run_id, name, call, clock, seconds=5
            )
    else:
        assert (
            await _expire_while_waiting_for_run_lock(
                clean_ledger, created.run_id, name, call, clock, seconds=5
            )
            is False
        )
    view = await seed.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert len(view.events) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("previous", "status"),
    [
        (RunStatus.RUNNING, RunStatus.QUEUED),
        (RunStatus.RUNNING, RunStatus.ANSWERED),
        (RunStatus.QUEUED, RunStatus.RUNNING),
    ],
)
async def test_append_rejects_forged_state_transitions(
    clean_ledger: str, previous: RunStatus, status: RunStatus
) -> None:
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)
    event = StateTransitionEvent(
        sequence=1, previous_status=previous, status=status, reason=None
    )
    with pytest.raises(RunStoreValidationError, match="^invalid_run_data$"):
        await store.append(created.run_id, "worker-a", event)
    view = await store.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert len(view.events) == 2


@pytest.mark.anyio
async def test_invalid_public_identifiers_fail_before_sql(clean_ledger: str) -> None:
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock)
    invalid = "secret owner with spaces"
    event = PlanSummaryEvent(
        sequence=1, plan_id="plan-a", step_count=1, max_tool_calls=1
    )
    for operation in (
        lambda: store.claim(uuid.uuid4(), invalid, lease_seconds=30),
        lambda: store.heartbeat(uuid.uuid4(), invalid, lease_seconds=30),
        lambda: store.append(uuid.uuid4(), invalid, event),
        lambda: store.complete(
            uuid.uuid4(),
            invalid,
            TerminalEvent(
                sequence=1, status=RunStatus.FAILED, reason="provider_timeout"
            ),
        ),
    ):
        with pytest.raises(RunStoreValidationError, match="^invalid_run_data$"):
            await operation()


@pytest.mark.anyio
@pytest.mark.parametrize("wrapper", [" {}", "{} ", "\t{}", "{}\n"])
async def test_owner_authorization_identifiers_are_not_canonicalized(
    clean_ledger: str, wrapper: str
) -> None:
    """Catches whitespace normalization aliasing a distinct authorization ID."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    wrapped_session = wrapper.format(created.session_id)
    try:
        foreign = await store.read_owned(created.run_id, wrapped_session)
    except RunStoreValidationError as error:
        assert str(error) == "invalid_run_data"
        assert wrapped_session not in repr(error)
    else:
        assert foreign is None


@pytest.mark.anyio
@pytest.mark.parametrize("wrapper", [" {}", "{} ", "\t{}", "{}\n"])
async def test_lease_authorization_identifiers_are_not_canonicalized(
    clean_ledger: str, wrapper: str
) -> None:
    """Catches a normalized owner alias authorizing lease mutations."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    wrapped_queue_owner = wrapper.format("queue-delivery")
    try:
        claim = await store.claim(created.run_id, wrapped_queue_owner, lease_seconds=30)
    except RunStoreValidationError as error:
        assert str(error) == "invalid_run_data"
        assert wrapped_queue_owner not in repr(error)
    else:
        assert claim is False

    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)
    wrapped_worker = wrapper.format("worker-a")
    event = PlanSummaryEvent(
        sequence=1, plan_id="plan-a", step_count=1, max_tool_calls=1
    )
    operations = (
        lambda: store.heartbeat(created.run_id, wrapped_worker, lease_seconds=30),
        lambda: store.append(created.run_id, wrapped_worker, event),
        lambda: store.complete(
            created.run_id,
            wrapped_worker,
            TerminalEvent(
                sequence=1, status=RunStatus.FAILED, reason="provider_timeout"
            ),
        ),
    )
    for operation in operations:
        try:
            result = await operation()
        except RunStoreValidationError as error:
            assert str(error) == "invalid_run_data"
            assert wrapped_worker not in repr(error)
        else:
            assert result is False
    view = await store.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert view.status is RunStatus.RUNNING
    assert [event.sequence for event in view.events] == [1, 2]


@pytest.mark.anyio
async def test_owner_read_uses_one_repeatable_snapshot(
    clean_ledger: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches an old run row being combined with newly committed events."""
    import psycopg

    await _seed_bindings(clean_ledger)
    clock = Clock()
    seed = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await seed.create(_new_run(clock))
    read_name = f"run-snapshot-read-{uuid.uuid4().hex}"
    reader = PostgresRunStore(_named_dsn(clean_ledger, read_name), clock=clock)
    row_read = asyncio.Event()
    resume = asyncio.Event()
    original_execute = psycopg.AsyncConnection.execute

    async def paused_execute(
        connection: psycopg.AsyncConnection[Any],
        query: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool = False,
    ) -> Any:
        cursor = await original_execute(
            connection, query, params, prepare=prepare, binary=binary
        )
        if (
            "FROM specpilot_run WHERE run_id" in str(query)
            and "session_id" in str(query)
            and connection.info.parameter_status("application_name") == read_name
        ):
            row_read.set()
            await resume.wait()
        return cursor

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", paused_execute)
    read_task = asyncio.create_task(
        reader.read_owned(created.run_id, created.session_id)
    )
    try:
        await asyncio.wait_for(row_read.wait(), timeout=2)
        assert await seed.claim(created.run_id, "worker-a", lease_seconds=30)
        assert await seed.complete(
            created.run_id,
            "worker-a",
            TerminalEvent(
                sequence=1, status=RunStatus.FAILED, reason="provider_timeout"
            ),
        )
    finally:
        resume.set()
    view = await read_task
    assert view is not None
    assert view.status is RunStatus.QUEUED
    assert [event.sequence for event in view.events] == [1]


@pytest.mark.anyio
async def test_complete_is_atomic_and_terminal_state_is_immutable(
    clean_ledger: str,
) -> None:
    """Catches a terminal state update committed without its terminal event."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)

    assert await store.complete(
        created.run_id,
        "worker-a",
        TerminalEvent(
            sequence=1, status=RunStatus.REFUSED, reason="evidence_insufficient"
        ),
    )
    assert not await store.complete(
        created.run_id,
        "worker-a",
        TerminalEvent(sequence=1, status=RunStatus.FAILED, reason="provider_timeout"),
    )
    assert not await store.claim(created.run_id, "worker-b", lease_seconds=30)
    view = await store.read_owned(created.run_id, created.session_id)
    assert view is not None
    assert view.status is RunStatus.REFUSED
    assert view.reason == "evidence_insufficient"
    assert [event.sequence for event in view.events] == [1, 2, 3]


@pytest.mark.anyio
async def test_terminal_constraint_failure_rolls_back_state_and_event(
    clean_ledger: str,
) -> None:
    """Catches transaction handling that leaves state or an event committed alone."""
    import psycopg

    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "CREATE FUNCTION specpilot_test_reject_terminal_update() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.status NOT IN ('queued', 'running') THEN "
            "RAISE EXCEPTION 'forced terminal update failure' "
            "USING ERRCODE = 'check_violation'; END IF; RETURN NEW; END $$"
        )
        await connection.execute(
            "CREATE TRIGGER specpilot_test_reject_terminal_update "
            "BEFORE UPDATE ON specpilot_run FOR EACH ROW EXECUTE FUNCTION "
            "specpilot_test_reject_terminal_update()"
        )
        await connection.commit()

    try:
        with pytest.raises(RunStoreUnavailable, match="^run_store_unavailable$"):
            await store.complete(
                created.run_id,
                "worker-a",
                TerminalEvent(
                    sequence=1, status=RunStatus.FAILED, reason="provider_timeout"
                ),
            )
    finally:
        async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
            await connection.execute(
                "DROP TRIGGER specpilot_test_reject_terminal_update "
                "ON specpilot_run"
            )
            await connection.execute(
                "DROP FUNCTION specpilot_test_reject_terminal_update()"
            )
            await connection.commit()

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        row = await (
            await connection.execute(
                "SELECT status, completed_at FROM specpilot_run WHERE run_id = %s",
                (created.run_id,),
            )
        ).fetchone()
        terminal_count = await (
            await connection.execute(
                "SELECT count(*) FROM specpilot_run_event "
                "WHERE run_id = %s AND kind = 'terminal'",
                (created.run_id,),
            )
        ).fetchone()
    assert row == ("running", None)
    assert terminal_count == (0,)


@pytest.mark.anyio
async def test_stale_or_foreign_append_fails_with_stable_sanitized_error(
    clean_ledger: str,
) -> None:
    """Catches append accepting a foreign lease or exposing ownership details."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    assert await store.claim(created.run_id, "worker-a", lease_seconds=30)
    event = PlanSummaryEvent(
        sequence=1, plan_id="plan-a", step_count=1, max_tool_calls=2
    )

    with pytest.raises(RunStoreValidationError) as foreign:
        await store.append(created.run_id, "worker-b", event)
    clock.advance(seconds=30)
    with pytest.raises(RunStoreValidationError) as stale:
        await store.append(created.run_id, "worker-a", event)

    assert str(foreign.value) == str(stale.value) == "invalid_run_data"
    assert "worker-a" not in repr(stale.value)
    assert "worker-b" not in repr(foreign.value)


@pytest.mark.anyio
async def test_database_error_is_sanitized_and_plaintext_is_never_persisted(
    clean_ledger: str,
) -> None:
    """Catches raw psycopg detail or caller-only question text reaching storage."""
    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    run = _new_run(clock)
    await store.create(run)
    marker = "plaintext-question-must-not-appear"

    with pytest.raises(RunStoreUnavailable) as duplicate:
        await store.create(run)

    message = repr(duplicate.value)
    assert str(duplicate.value) == "run_store_unavailable"
    assert clean_ledger not in message
    assert str(run.run_id) not in message
    assert marker not in message

    import psycopg

    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        columns = await (
            await connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name IN ('specpilot_run', 'specpilot_run_event')"
            )
        ).fetchall()
        stored = await (
            await connection.execute(
                "SELECT row_to_json(r)::text FROM specpilot_run r WHERE run_id = %s",
                (run.run_id,),
            )
        ).fetchone()
    assert all("question" not in str(column[0]) for column in columns)
    assert stored is not None
    assert marker not in stored[0]


@pytest.mark.anyio
async def test_corrupted_event_metadata_fails_closed_without_raw_json(
    clean_ledger: str,
) -> None:
    """Catches trusting JSON payload metadata without Pydantic parity checks."""
    import psycopg
    from psycopg.types.json import Jsonb

    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    secret = "corrupt-json-secret"
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "ALTER TABLE specpilot_run_event "
            "DROP CONSTRAINT specpilot_run_event_payload_check"
        )
        await connection.execute(
            "UPDATE specpilot_run_event SET payload = %s WHERE run_id = %s",
            (Jsonb({"secret": secret}), created.run_id),
        )
        await connection.commit()

    try:
        with pytest.raises(RunStoreIntegrityError) as corrupted:
            await store.read_owned(created.run_id, created.session_id)
        assert str(corrupted.value) == "run_store_integrity"
        assert secret not in repr(corrupted.value)
    finally:
        async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
            await connection.execute(
                "DELETE FROM specpilot_run_event WHERE run_id = %s", (created.run_id,)
            )
            await connection.execute(
                "ALTER TABLE specpilot_run_event ADD CONSTRAINT "
                "specpilot_run_event_payload_check CHECK "
                "(specpilot_valid_run_event(kind, sequence, payload))"
            )
            await connection.commit()


@pytest.mark.anyio
async def test_corrupted_run_metadata_fails_closed_without_raw_row(
    clean_ledger: str,
) -> None:
    """Catches projecting a row that violates the typed run-state contract."""
    import psycopg

    await _seed_bindings(clean_ledger)
    clock = Clock()
    store = PostgresRunStore(clean_ledger, clock=clock, queue_lease_seconds=5)
    created = await store.create(_new_run(clock))
    secret = "corrupt-run-row-secret"
    async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
        await connection.execute(
            "ALTER TABLE specpilot_run "
            "DROP CONSTRAINT specpilot_run_state_metadata_check"
        )
        await connection.execute(
            "UPDATE specpilot_run SET lease_owner = NULL, profile = %s "
            "WHERE run_id = %s",
            (secret, created.run_id),
        )
        await connection.commit()

    try:
        with pytest.raises(RunStoreIntegrityError) as corrupted:
            await store.read_owned(created.run_id, created.session_id)
        assert str(corrupted.value) == "run_store_integrity"
        assert secret not in repr(corrupted.value)
    finally:
        async with await psycopg.AsyncConnection.connect(clean_ledger) as connection:
            await connection.execute(
                "DELETE FROM specpilot_run WHERE run_id = %s", (created.run_id,)
            )
            await connection.execute(
                "ALTER TABLE specpilot_run ADD CONSTRAINT "
                "specpilot_run_state_metadata_check CHECK ("
                "(status IN ('queued', 'running') "
                "AND terminal_reason IS NULL AND completed_at IS NULL "
                "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
                "OR (status IN ('answered', 'refused', 'egress_blocked', "
                "'failed', 'interrupted') AND ((status = 'answered' "
                "AND terminal_reason IS NULL) OR (status <> 'answered' "
                "AND terminal_reason IS NOT NULL)) AND completed_at IS NOT NULL "
                "AND lease_owner IS NULL AND lease_expires_at IS NULL "
                "AND last_heartbeat_at IS NULL))"
            )
            await connection.commit()
