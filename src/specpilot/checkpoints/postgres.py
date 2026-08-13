"""Transactional persistence for sanitized L2 checkpoints and resume attempts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from specpilot.checkpoints.contracts import CheckpointStage, RunCheckpoint
from specpilot.runs.contracts import (
    CheckpointSummaryEvent,
    ResumeDisposition,
    ResumeSummaryEvent,
    RunRecord,
    RunStatus,
    StateTransitionEvent,
)
from specpilot.runs.postgres import (
    RunStoreIntegrityError,
    RunStoreUnavailable,
    RunStoreValidationError,
)


class CheckpointResumeResult:
    """Closed resume outcome; ordinary denials never become storage errors."""

    def __init__(
        self, disposition: ResumeDisposition, attempt: int | None = None
    ) -> None:
        self.disposition = disposition
        self.attempt = attempt


class PostgresCheckpointStore:
    def __init__(
        self, conninfo: str, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._conninfo = conninfo
        self._clock = clock or _utc_now

    def new_checkpoint(
        self, run: RunRecord, *, stage: CheckpointStage
    ) -> RunCheckpoint:
        """Build the initial safe envelope from frozen run bindings only."""
        if (
            run.task_level != "L2"
            or run.evaluation_root_id is None
            or run.compliance_prompt_hash is None
            or run.verifier_prompt_hash is None
        ):
            raise RunStoreValidationError()
        return RunCheckpoint(
            run_id=run.run_id,
            attempt=1,
            checkpoint_version=1,
            stage=stage,
            task_level="L2",
            query_hash=run.query_hash,
            evaluation_root_id=run.evaluation_root_id,
            source_manifest_id=run.source_manifest_id,
            corpus_manifest_id=run.corpus_manifest_id,
            policy_hash=run.policy_hash,
            configuration_hash=run.configuration_hash,
            compliance_prompt_hash=run.compliance_prompt_hash,
            verifier_prompt_hash=run.verifier_prompt_hash,
            provider_id=run.provider_id,
            model_id=run.model_id,
            plan_id=None,
            plan_hash=None,
            evidence=(),
            tool_attempts_used=0,
            reservation_ids=(),
            reconstruction_generations=(),
            recovery_attempted=False,
            recovery_reason=None,
            completed_claim_ids=(),
            completed_results=(),
            last_accessed_at=self._now(),
        )

    async def write(
        self, previous_version: int | None, checkpoint: RunCheckpoint
    ) -> RunCheckpoint:
        """CAS write a checkpoint and its trace event in one transaction."""
        connection = await self._connect()
        now = self._now()
        try:
            async with connection, connection.transaction():
                run = await self._lock_run(connection, checkpoint.run_id)
                if run is None or not _bindings_match(run, checkpoint):
                    raise RunStoreValidationError()
                await self._validate_reservations(connection, checkpoint)
                current = await (
                    await connection.execute(
                        "SELECT checkpoint_version FROM specpilot_run_checkpoint "
                        "WHERE run_id = %s FOR UPDATE",
                        (checkpoint.run_id,),
                    )
                ).fetchone()
                current_version = (
                    None if current is None else current["checkpoint_version"]
                )
                if current_version != previous_version:
                    raise RunStoreValidationError()
                if checkpoint.checkpoint_version != (previous_version or 0) + 1:
                    raise RunStoreValidationError()
                if current is None and checkpoint.stage is not CheckpointStage.PLANNED:
                    raise RunStoreValidationError()
                allocated = checkpoint.model_copy(update={"last_accessed_at": now})
                if current is None:
                    await connection.execute(
                        "INSERT INTO specpilot_run_checkpoint "
                        "(run_id, checkpoint_version, stage, payload, "
                        "last_accessed_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            allocated.run_id,
                            allocated.checkpoint_version,
                            allocated.stage.value,
                            Jsonb(allocated.model_dump(mode="json")),
                            now,
                        ),
                    )
                    await connection.execute(
                        "INSERT INTO specpilot_run_attempt "
                        "(run_id, attempt, resume_key_hash, started_at) "
                        "VALUES (%s, 1, NULL, %s) ON CONFLICT DO NOTHING",
                        (allocated.run_id, now),
                    )
                else:
                    old_payload = await (
                        await connection.execute(
                            "SELECT payload FROM specpilot_run_checkpoint "
                            "WHERE run_id = %s FOR UPDATE",
                            (checkpoint.run_id,),
                        )
                    ).fetchone()
                    if old_payload is None:
                        raise RunStoreIntegrityError()
                    from specpilot.checkpoints.contracts import validate_transition

                    validate_transition(
                        RunCheckpoint.model_validate(old_payload["payload"]), allocated
                    )
                    updated = await connection.execute(
                        "UPDATE specpilot_run_checkpoint SET checkpoint_version = %s, "
                        "stage = %s, payload = %s, last_accessed_at = %s "
                        "WHERE run_id = %s AND checkpoint_version = %s",
                        (
                            allocated.checkpoint_version,
                            allocated.stage.value,
                            Jsonb(allocated.model_dump(mode="json")),
                            now,
                            allocated.run_id,
                            previous_version,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise RunStoreValidationError()
                sequence = await self._next_sequence(connection, allocated.run_id)
                event = CheckpointSummaryEvent(
                    sequence=sequence,
                    stage=allocated.stage.value,
                    checkpoint_version=allocated.checkpoint_version,
                    tool_attempts_used=allocated.tool_attempts_used,
                    recovery_attempted=allocated.recovery_attempted,
                )
                await connection.execute(
                    "INSERT INTO specpilot_run_event "
                    "(run_id, sequence, kind, payload, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        allocated.run_id,
                        sequence,
                        event.kind.value,
                        Jsonb(event.model_dump(mode="json")),
                        now,
                    ),
                )
                return allocated
        except RunStoreValidationError:
            raise
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        except (TypeError, ValueError, ValidationError):
            raise RunStoreIntegrityError() from None

    async def read(self, run_id: UUID) -> RunCheckpoint | None:
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                row = await (
                    await connection.execute(
                        "SELECT payload FROM specpilot_run_checkpoint "
                        "WHERE run_id = %s",
                        (run_id,),
                    )
                ).fetchone()
                if row is None:
                    return None
                payload = row["payload"]
                if not isinstance(payload, dict):
                    raise RunStoreIntegrityError()
                return RunCheckpoint.model_validate(payload)
        except RunStoreIntegrityError:
            raise
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        except (TypeError, ValueError, ValidationError):
            raise RunStoreIntegrityError() from None

    async def begin_resume(
        self,
        run_id: UUID,
        session_id: str,
        query_hash: str,
        resume_key: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> CheckpointResumeResult:
        """Atomically replay or acquire one owner-bound interrupted L2 attempt."""
        if lease_seconds <= 0 or not resume_key:
            raise RunStoreValidationError()
        key_hash = hashlib.sha256(resume_key.encode("utf-8")).hexdigest()
        now = self._now()
        try:
            connection = await self._connect()
            async with connection, connection.transaction():
                run = await self._lock_run(connection, run_id)
                if run is None:
                    return CheckpointResumeResult(ResumeDisposition.NOT_FOUND)
                if run["session_id"] != session_id:
                    return CheckpointResumeResult(ResumeDisposition.NOT_OWNER)
                if run["query_hash"] != query_hash:
                    return CheckpointResumeResult(ResumeDisposition.QUERY_MISMATCH)
                replay = await (
                    await connection.execute(
                        "SELECT attempt FROM specpilot_run_attempt WHERE run_id = %s "
                        "AND resume_key_hash = %s",
                        (run_id, key_hash),
                    )
                ).fetchone()
                if replay is not None:
                    return CheckpointResumeResult(
                        ResumeDisposition.REPLAY, replay["attempt"]
                    )
                if run["status"] in {
                    RunStatus.QUEUED.value,
                    RunStatus.RUNNING.value,
                }:
                    return CheckpointResumeResult(ResumeDisposition.LEASED)
                if (
                    run["status"] != RunStatus.INTERRUPTED.value
                    or run["terminal_reason"] != "lease_expired"
                ):
                    return CheckpointResumeResult(ResumeDisposition.NOT_INTERRUPTED)
                checkpoint_row = await (
                    await connection.execute(
                        "SELECT payload FROM specpilot_run_checkpoint "
                        "WHERE run_id = %s "
                        "FOR UPDATE",
                        (run_id,),
                    )
                ).fetchone()
                if checkpoint_row is None:
                    return CheckpointResumeResult(ResumeDisposition.CHECKPOINT_MISSING)
                try:
                    checkpoint = RunCheckpoint.model_validate(checkpoint_row["payload"])
                except (TypeError, ValueError, ValidationError):
                    return CheckpointResumeResult(ResumeDisposition.CHECKPOINT_INVALID)
                if not _bindings_match(run, checkpoint):
                    return CheckpointResumeResult(ResumeDisposition.BINDING_MISMATCH)
                disposition = await self._resume_reservation_disposition(
                    connection, checkpoint
                )
                if disposition is not None:
                    return CheckpointResumeResult(disposition)
                latest = await (
                    await connection.execute(
                        "SELECT COALESCE(MAX(attempt), 0) AS attempt "
                        "FROM specpilot_run_attempt WHERE run_id = %s",
                        (run_id,),
                    )
                ).fetchone()
                if latest is None:
                    raise RunStoreIntegrityError()
                attempt = int(latest["attempt"]) + 1
                updated = await connection.execute(
                    "UPDATE specpilot_run SET status = 'running', "
                    "terminal_reason = NULL, "
                    "completed_at = NULL, started_at = COALESCE(started_at, %s), "
                    "lease_owner = %s, lease_expires_at = %s, last_heartbeat_at = %s "
                    "WHERE run_id = %s AND status = 'interrupted'",
                    (
                        now,
                        lease_owner,
                        now + timedelta(seconds=lease_seconds),
                        now,
                        run_id,
                    ),
                )
                if updated.rowcount != 1:
                    return CheckpointResumeResult(ResumeDisposition.LEASED)
                await connection.execute(
                    "INSERT INTO specpilot_run_attempt "
                    "(run_id, attempt, resume_key_hash, started_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (run_id, attempt, key_hash, now),
                )
                resumed_checkpoint = checkpoint.model_copy(
                    update={
                        "attempt": attempt,
                        "checkpoint_version": checkpoint.checkpoint_version + 1,
                        "last_accessed_at": now,
                    }
                )
                await connection.execute(
                    "UPDATE specpilot_run_checkpoint SET checkpoint_version = %s, "
                    "payload = %s, last_accessed_at = %s WHERE run_id = %s",
                    (
                        resumed_checkpoint.checkpoint_version,
                        Jsonb(resumed_checkpoint.model_dump(mode="json")),
                        now,
                        run_id,
                    ),
                )
                sequence = await self._next_sequence(connection, run_id)
                transition = StateTransitionEvent(
                    sequence=sequence,
                    previous_status=RunStatus.INTERRUPTED,
                    status=RunStatus.RUNNING,
                    reason=None,
                )
                await connection.execute(
                    "INSERT INTO specpilot_run_event "
                    "(run_id, sequence, kind, payload, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        run_id,
                        sequence,
                        transition.kind.value,
                        Jsonb(transition.model_dump(mode="json")),
                        now,
                    ),
                )
                resume_event = ResumeSummaryEvent(
                    sequence=sequence + 1,
                    attempt=attempt,
                )
                await connection.execute(
                    "INSERT INTO specpilot_run_event "
                    "(run_id, sequence, kind, payload, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        run_id,
                        resume_event.sequence,
                        resume_event.kind.value,
                        Jsonb(resume_event.model_dump(mode="json")),
                        now,
                    ),
                )
                return CheckpointResumeResult(ResumeDisposition.ACQUIRED, attempt)
        except RunStoreIntegrityError:
            raise
        except psycopg.Error:
            raise RunStoreUnavailable() from None

    async def compact(self, run_id: UUID) -> bool:
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                row = await (
                    await connection.execute(
                        "SELECT payload FROM specpilot_run_checkpoint "
                        "WHERE run_id = %s FOR UPDATE",
                        (run_id,),
                    )
                ).fetchone()
                if row is None:
                    return False
                checkpoint = RunCheckpoint.model_validate(row["payload"])
                if checkpoint.stage is not CheckpointStage.COMPLETED:
                    return False
                compacted = checkpoint.model_copy(
                    update={
                        "plan_id": None,
                        "plan_hash": None,
                        "evidence": (),
                        "tool_attempts_used": 0,
                        "reservation_ids": (),
                        "reconstruction_generations": (),
                        "recovery_reason": None,
                        "last_accessed_at": self._now(),
                    }
                )
                await connection.execute(
                    "UPDATE specpilot_run_checkpoint SET payload = %s, "
                    "last_accessed_at = %s WHERE run_id = %s",
                    (
                        Jsonb(compacted.model_dump(mode="json")),
                        compacted.last_accessed_at,
                        run_id,
                    ),
                )
                return True
        except psycopg.Error:
            raise RunStoreUnavailable() from None
        except (TypeError, ValueError, ValidationError):
            raise RunStoreIntegrityError() from None

    async def delete_expired(self, before: datetime) -> int:
        if before.tzinfo is None or before.utcoffset() is None:
            raise RunStoreValidationError()
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                deleted = await connection.execute(
                    "DELETE FROM specpilot_run_checkpoint WHERE stage <> 'completed' "
                    "AND last_accessed_at < %s",
                    (before.astimezone(UTC),),
                )
                return deleted.rowcount
        except psycopg.Error:
            raise RunStoreUnavailable() from None

    async def _connect(self) -> psycopg.AsyncConnection[dict[str, Any]]:
        try:
            return await psycopg.AsyncConnection.connect(
                self._conninfo, row_factory=dict_row
            )
        except psycopg.Error:
            raise RunStoreUnavailable() from None

    async def _lock_run(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], run_id: UUID
    ) -> dict[str, Any] | None:
        return await (
            await connection.execute(
                "SELECT run_id, session_id, task_level, evaluation_root_id, "
                "source_manifest_id, "
                "corpus_manifest_id, policy_hash, configuration_hash, provider_id, "
                "model_id, query_hash, terminal_reason, compliance_prompt_hash, "
                "verifier_prompt_hash, status "
                "FROM specpilot_run WHERE run_id = %s "
                "FOR UPDATE",
                (run_id,),
            )
        ).fetchone()

    async def _next_sequence(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], run_id: UUID
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

    async def _validate_reservations(
        self,
        connection: psycopg.AsyncConnection[dict[str, Any]],
        checkpoint: RunCheckpoint,
    ) -> None:
        for reservation_id in checkpoint.reservation_ids:
            row = await (
                await connection.execute(
                    "SELECT evaluation_root_id, run_id FROM egress_reservation "
                    "WHERE reservation_id = %s",
                    (reservation_id,),
                )
            ).fetchone()
            if (
                row is None
                or row["evaluation_root_id"] != checkpoint.evaluation_root_id
                or row["run_id"] != str(checkpoint.run_id)
            ):
                raise RunStoreValidationError()

    async def _resume_reservation_disposition(
        self,
        connection: psycopg.AsyncConnection[dict[str, Any]],
        checkpoint: RunCheckpoint,
    ) -> ResumeDisposition | None:
        """A process loss may not resume across an uncertain egress send."""
        for reservation_id in checkpoint.reservation_ids:
            row = await (
                await connection.execute(
                    "SELECT evaluation_root_id, run_id, state FROM egress_reservation "
                    "WHERE reservation_id = %s",
                    (reservation_id,),
                )
            ).fetchone()
            if (
                row is None
                or row["evaluation_root_id"] != checkpoint.evaluation_root_id
                or row["run_id"] != str(checkpoint.run_id)
                or row["state"] not in {"succeeded", "failed_known"}
            ):
                return ResumeDisposition.CHECKPOINT_INVALID
        return None

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RunStoreValidationError()
        return now.astimezone(UTC)


def _bindings_match(run: dict[str, Any], checkpoint: RunCheckpoint) -> bool:
    values = (
        ("task_level", checkpoint.task_level),
        ("evaluation_root_id", checkpoint.evaluation_root_id),
        ("source_manifest_id", checkpoint.source_manifest_id),
        ("corpus_manifest_id", checkpoint.corpus_manifest_id),
        ("policy_hash", checkpoint.policy_hash),
        ("configuration_hash", checkpoint.configuration_hash),
        ("compliance_prompt_hash", checkpoint.compliance_prompt_hash),
        ("verifier_prompt_hash", checkpoint.verifier_prompt_hash),
        ("provider_id", checkpoint.provider_id),
        ("model_id", checkpoint.model_id),
        ("query_hash", checkpoint.query_hash),
    )
    return all(
        isinstance(run.get(key), str) and run[key] == expected
        for key, expected in values
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["CheckpointResumeResult", "PostgresCheckpointStore"]
