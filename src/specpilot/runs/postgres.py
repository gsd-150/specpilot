"""Asynchronous PostgreSQL persistence for sanitized leased run traces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter, ValidationError

from specpilot.runs.contracts import (
    RunEvent,
    RunRecord,
    RunStatus,
    RunView,
    StateTransitionEvent,
    TerminalEvent,
    TraceIdentifier,
)

_QUEUE_LEASE_OWNER = "queue-delivery"
_LEASE_EXPIRED_REASON = "lease_expired"
_EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
_IDENTIFIER_ADAPTER: TypeAdapter[str] = TypeAdapter(TraceIdentifier)
_UUID_ADAPTER: TypeAdapter[UUID] = TypeAdapter(UUID)
_RUN_COLUMNS = (
    "run_id, request_id, session_id, task_level, evaluation_root_id, profile, "
    "source_manifest_id, "
    "corpus_manifest_id, policy_hash, configuration_hash, prompt_id, "
    "prompt_hash, compliance_prompt_hash, verifier_prompt_hash, provider_id, "
    "model_id, query_hash, status, terminal_reason, "
    "created_at, started_at, completed_at, lease_owner, lease_expires_at, "
    "last_heartbeat_at"
)


class RunStoreError(RuntimeError):
    """Stable, detail-free run-store failure."""


class RunStoreUnavailable(RunStoreError):
    def __init__(self) -> None:
        super().__init__("run_store_unavailable")


class RunStoreIntegrityError(RunStoreError):
    def __init__(self) -> None:
        super().__init__("run_store_integrity")


class RunStoreValidationError(RunStoreError):
    def __init__(self) -> None:
        super().__init__("invalid_run_data")


class PostgresRunStore:
    """Owner-scoped run storage whose only durable query value is its hash."""

    def __init__(
        self,
        conninfo: str,
        *,
        clock: Callable[[], datetime] | None = None,
        queue_lease_seconds: int = 30,
    ) -> None:
        if queue_lease_seconds <= 0:
            raise RunStoreValidationError()
        self._conninfo = conninfo
        self._clock = clock or _utc_now
        self._queue_lease = timedelta(seconds=queue_lease_seconds)

    async def create(self, run: RunRecord) -> RunRecord:
        now = self._now()
        try:
            queued = RunRecord.model_validate(
                {
                    **run.model_dump(),
                    "status": RunStatus.QUEUED,
                    "terminal_reason": None,
                    "created_at": now,
                    "started_at": None,
                    "completed_at": None,
                    "lease_owner": _QUEUE_LEASE_OWNER,
                    "lease_expires_at": now + self._queue_lease,
                    "last_heartbeat_at": None,
                }
            )
            event = StateTransitionEvent(
                sequence=1,
                previous_status=None,
                status=RunStatus.QUEUED,
                reason=None,
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise RunStoreValidationError() from None

        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                await connection.execute(
                    "INSERT INTO specpilot_run (" + _RUN_COLUMNS + ") VALUES ("
                    + ", ".join(["%s"] * 25)
                    + ")",
                    _run_values(queued),
                )
                await _insert_event(connection, queued.run_id, event, now)
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        return queued

    async def read_owned(self, run_id: UUID, session_id: str) -> RunView | None:
        validated_run_id = _validated_uuid(run_id)
        validated_session_id = _validated_identifier(session_id)
        connection = await self._connect()
        try:
            await connection.set_isolation_level(IsolationLevel.REPEATABLE_READ)
            async with connection, connection.transaction():
                row = await (
                    await connection.execute(
                        "SELECT " + _RUN_COLUMNS + " FROM specpilot_run "
                        "WHERE run_id = %s AND session_id = %s",
                        (validated_run_id, validated_session_id),
                    )
                ).fetchone()
                if row is None:
                    return None
                event_rows = await (
                    await connection.execute(
                        "SELECT sequence, kind, payload FROM specpilot_run_event "
                        "WHERE run_id = %s ORDER BY sequence",
                        (validated_run_id,),
                    )
                ).fetchall()
        except psycopg.Error:
            raise RunStoreUnavailable() from None

        try:
            record = RunRecord.model_validate(row)
            events = tuple(_event_from_row(event_row) for event_row in event_rows)
            expired = (
                record.status in {RunStatus.QUEUED, RunStatus.RUNNING}
                and record.lease_expires_at is not None
                and record.lease_expires_at <= self._now()
            )
            return RunView(
                run_id=record.run_id,
                request_id=record.request_id,
                task_level=record.task_level,
                profile=record.profile,
                corpus_manifest_id=record.corpus_manifest_id,
                status=RunStatus.INTERRUPTED if expired else record.status,
                reason=_LEASE_EXPIRED_REASON if expired else record.terminal_reason,
                created_at=record.created_at,
                started_at=record.started_at,
                completed_at=record.completed_at,
                events=events,
            )
        except (TypeError, ValueError, ValidationError):
            raise RunStoreIntegrityError() from None

    async def claim(
        self,
        run_id: UUID,
        lease_owner: str,
        *,
        lease_seconds: int,
    ) -> bool:
        validated_run_id = _validated_uuid(run_id)
        validated_owner = _validated_identifier(lease_owner)
        _validated_lease_seconds(lease_seconds)
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                row = await _lock_run(connection, validated_run_id)
                now = self._now()
                expiry = _lease_expiry(now, lease_seconds)
                if (
                    row is None
                    or row["status"] != RunStatus.QUEUED.value
                    or row["lease_expires_at"] <= now
                ):
                    return False
                sequence = await _next_sequence(connection, run_id)
                event = StateTransitionEvent(
                    sequence=sequence,
                    previous_status=RunStatus.QUEUED,
                    status=RunStatus.RUNNING,
                    reason=None,
                )
                updated = await connection.execute(
                    "UPDATE specpilot_run SET status = 'running', started_at = %s, "
                    "lease_owner = %s, lease_expires_at = %s, "
                    "last_heartbeat_at = %s WHERE run_id = %s AND status = 'queued'",
                    (now, validated_owner, expiry, now, validated_run_id),
                )
                if updated.rowcount != 1:
                    return False
                await _insert_event(connection, run_id, event, now)
                return True
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        except (TypeError, ValueError, ValidationError):
            raise RunStoreValidationError() from None

    async def heartbeat(
        self,
        run_id: UUID,
        lease_owner: str,
        *,
        lease_seconds: int,
    ) -> bool:
        validated_run_id = _validated_uuid(run_id)
        validated_owner = _validated_identifier(lease_owner)
        _validated_lease_seconds(lease_seconds)
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                row = await _lock_run(connection, validated_run_id)
                now = self._now()
                expiry = _lease_expiry(now, lease_seconds)
                if (
                    row is None
                    or row["status"] != RunStatus.RUNNING.value
                    or row["lease_owner"] != validated_owner
                    or row["lease_expires_at"] <= now
                ):
                    return False
                updated = await connection.execute(
                    "UPDATE specpilot_run SET "
                    "lease_expires_at = GREATEST(lease_expires_at, %s), "
                    "last_heartbeat_at = %s WHERE run_id = %s "
                    "AND status = 'running' AND lease_owner = %s "
                    "AND lease_expires_at > %s",
                    (expiry, now, validated_run_id, validated_owner, now),
                )
                return updated.rowcount == 1
        except psycopg.Error:
            raise RunStoreUnavailable() from None

    async def append(
        self,
        run_id: UUID,
        lease_owner: str,
        event: RunEvent,
    ) -> RunEvent:
        validated_run_id = _validated_uuid(run_id)
        validated_owner = _validated_identifier(lease_owner)
        validated = _validated_event(event)
        if isinstance(validated, (StateTransitionEvent, TerminalEvent)):
            raise RunStoreValidationError()
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                row = await _lock_run(connection, validated_run_id)
                now = self._now()
                if (
                    row is None
                    or row["status"] != RunStatus.RUNNING.value
                    or row["lease_owner"] != validated_owner
                    or row["lease_expires_at"] <= now
                ):
                    raise RunStoreValidationError()
                sequence = await _next_sequence(connection, validated_run_id)
                allocated = _allocated_event(validated, sequence)
                await _insert_event(connection, validated_run_id, allocated, now)
                return allocated
        except RunStoreValidationError:
            raise
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        except (TypeError, ValueError, ValidationError):
            raise RunStoreIntegrityError() from None

    async def complete(
        self,
        run_id: UUID,
        lease_owner: str,
        event: TerminalEvent,
    ) -> bool:
        validated_run_id = _validated_uuid(run_id)
        validated_owner = _validated_identifier(lease_owner)
        validated = _validated_terminal(event)
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                row = await _lock_run(connection, validated_run_id)
                now = self._now()
                if (
                    row is None
                    or row["status"] not in {
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                    }
                    or row["lease_owner"] != validated_owner
                    or row["lease_expires_at"] <= now
                ):
                    return False
                sequence = await _next_sequence(connection, validated_run_id)
                allocated_payload = validated.model_dump(mode="json")
                allocated_payload["sequence"] = sequence
                await connection.execute(
                    "INSERT INTO specpilot_run_event "
                    "(run_id, sequence, kind, payload, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        validated_run_id,
                        sequence,
                        validated.kind.value,
                        Jsonb(allocated_payload),
                        now,
                    ),
                )
                updated = await connection.execute(
                    "UPDATE specpilot_run SET status = %s, terminal_reason = %s, "
                    "completed_at = %s, lease_owner = NULL, "
                    "lease_expires_at = NULL, last_heartbeat_at = NULL "
                    "WHERE run_id = %s AND status IN ('queued', 'running')",
                    (
                        validated.status.value,
                        validated.reason,
                        now,
                        validated_run_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise RunStoreIntegrityError()
                return True
        except RunStoreIntegrityError:
            raise
        except psycopg.Error:
            raise RunStoreUnavailable() from None

    async def fail_delivery(self, run_id: UUID, event: TerminalEvent) -> bool:
        """Close one queued API delivery without exposing a generic lease owner."""
        validated_run_id = _validated_uuid(run_id)
        validated = _validated_terminal(event)
        if (
            validated.status is not RunStatus.INTERRUPTED
            or validated.reason != "queue_delivery_failed"
        ):
            raise RunStoreValidationError()
        return await self.complete(validated_run_id, _QUEUE_LEASE_OWNER, validated)

    async def reconcile_expired(self, now: datetime | None = None) -> int:
        effective_now = self._now() if now is None else _aware_utc(now)
        connection = await self._connect()
        changed = 0
        try:
            async with connection, connection.transaction():
                rows = await (
                    await connection.execute(
                        "SELECT run_id FROM specpilot_run WHERE "
                        "status IN ('queued', 'running') AND lease_expires_at <= %s "
                        "ORDER BY run_id FOR UPDATE SKIP LOCKED",
                        (effective_now,),
                    )
                ).fetchall()
                for row in rows:
                    run_id = row["run_id"]
                    sequence = await _next_sequence(connection, run_id)
                    event = TerminalEvent(
                        sequence=sequence,
                        status=RunStatus.INTERRUPTED,
                        reason=_LEASE_EXPIRED_REASON,
                    )
                    updated = await connection.execute(
                        "UPDATE specpilot_run SET status = 'interrupted', "
                        "terminal_reason = %s, completed_at = %s, "
                        "lease_owner = NULL, lease_expires_at = NULL, "
                        "last_heartbeat_at = NULL WHERE run_id = %s "
                        "AND status IN ('queued', 'running') "
                        "AND lease_expires_at <= %s",
                        (_LEASE_EXPIRED_REASON, effective_now, run_id, effective_now),
                    )
                    if updated.rowcount != 1:
                        continue
                    await connection.execute(
                        "UPDATE specpilot_run_attempt SET ended_at = %s, "
                        "end_reason = %s WHERE run_id = %s AND ended_at IS NULL",
                        (effective_now, _LEASE_EXPIRED_REASON, run_id),
                    )
                    await _insert_event(connection, run_id, event, effective_now)
                    changed += 1
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        except (TypeError, ValueError, ValidationError):
            raise RunStoreIntegrityError() from None
        return changed

    async def _connect(self) -> psycopg.AsyncConnection[dict[str, Any]]:
        try:
            return await psycopg.AsyncConnection.connect(
                self._conninfo,
                row_factory=dict_row,
            )
        except psycopg.Error:
            raise RunStoreUnavailable() from None

    def _now(self) -> datetime:
        try:
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
            return now.astimezone(UTC)
        except (AttributeError, TypeError, ValueError):
            raise RunStoreValidationError() from None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RunStoreValidationError()
    return value.astimezone(UTC)


def _lease_expiry(now: datetime, lease_seconds: int) -> datetime:
    _validated_lease_seconds(lease_seconds)
    return now + timedelta(seconds=lease_seconds)


def _validated_lease_seconds(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RunStoreValidationError()
    return value


def _validated_identifier(value: str) -> str:
    try:
        validated = _IDENTIFIER_ADAPTER.validate_python(value)
    except (TypeError, ValueError, ValidationError):
        raise RunStoreValidationError() from None
    if validated != value:
        raise RunStoreValidationError()
    return validated


def _validated_uuid(value: UUID) -> UUID:
    try:
        validated = _UUID_ADAPTER.validate_python(value, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise RunStoreValidationError() from None
    if validated != value:
        raise RunStoreValidationError()
    return validated


def _run_values(run: RunRecord) -> tuple[Any, ...]:
    data = run.model_dump()
    return tuple(data[column.strip()] for column in _RUN_COLUMNS.split(","))


async def _insert_event(
    connection: psycopg.AsyncConnection[dict[str, Any]],
    run_id: UUID,
    event: RunEvent,
    recorded_at: datetime,
) -> None:
    await connection.execute(
        "INSERT INTO specpilot_run_event "
        "(run_id, sequence, kind, payload, recorded_at) VALUES (%s, %s, %s, %s, %s)",
        (
            run_id,
            event.sequence,
            event.kind.value,
            Jsonb(event.model_dump(mode="json")),
            recorded_at,
        ),
    )


async def _lock_run(
    connection: psycopg.AsyncConnection[dict[str, Any]], run_id: UUID
) -> dict[str, Any] | None:
    return await (
        await connection.execute(
            "SELECT status, lease_owner, lease_expires_at FROM specpilot_run "
            "WHERE run_id = %s FOR UPDATE",
            (run_id,),
        )
    ).fetchone()


async def _next_sequence(
    connection: psycopg.AsyncConnection[dict[str, Any]], run_id: UUID
) -> int:
    row = await (
        await connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence "
            "FROM specpilot_run_event WHERE run_id = %s",
            (run_id,),
        )
    ).fetchone()
    if row is None or not isinstance(row["sequence"], int):
        raise RunStoreIntegrityError()
    return row["sequence"]


def _validated_event(event: RunEvent) -> RunEvent:
    try:
        return _EVENT_ADAPTER.validate_python(event.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise RunStoreValidationError() from None


def _validated_terminal(event: TerminalEvent) -> TerminalEvent:
    validated = _validated_event(event)
    if not isinstance(validated, TerminalEvent):
        raise RunStoreValidationError()
    return validated


def _allocated_event(event: RunEvent, sequence: int) -> RunEvent:
    payload = event.model_dump(mode="python")
    payload["sequence"] = sequence
    return _EVENT_ADAPTER.validate_python(payload)


def _event_from_row(row: Mapping[str, Any]) -> RunEvent:
    payload = row["payload"]
    if not isinstance(payload, dict):
        raise ValueError
    event = _EVENT_ADAPTER.validate_python(payload)
    if event.sequence != row["sequence"] or event.kind.value != row["kind"]:
        raise ValueError
    return event


__all__ = [
    "PostgresRunStore",
    "RunStoreError",
    "RunStoreIntegrityError",
    "RunStoreUnavailable",
    "RunStoreValidationError",
]
