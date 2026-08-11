from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from specpilot.contracts.egress import (
    CorpusUsage,
    ReservationOutcome,
    ReservationRequest,
    SourceManifestResolver,
    TokenCounter,
    UsageSnapshot,
)
from specpilot.contracts.manifests import ProviderRouteBinding
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.ledger import (
    Attempt,
    AttemptOutcome,
    LedgerUnavailable,
    PolicyRebindAmbiguous,
    PolicyRebindConflict,
    PolicyRebindResult,
    RequestSize,
    Reservation,
    ReservationAmbiguous,
    ReservationState,
    RunSealed,
    successor_corpus_usage,
)
from specpilot.egress.policy import EgressPolicy


@dataclass(frozen=True)
class _LockedCorpus:
    corpus_ledger_id: str
    usage: CorpusUsage | None


class PostgresEgressLedger:
    """Durable, atomic check-and-reserve over the pure enforcer.

    The transaction locks the corpus head, then its active epoch, and then the
    evaluation-root row -- always in that order, because every reservation
    touches all three and a fixed order is what prevents deadlock -- re-runs the
    enforcer against the stored state, and writes both scopes before committing.

    Cap arithmetic is never reimplemented here. A second implementation would be
    free to drift from the enforcer, and drift in this direction is a silently
    raised ceiling.
    """

    def __init__(
        self,
        conninfo: str,
        *,
        policy: EgressPolicy,
        manifests: SourceManifestResolver,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._conninfo = conninfo
        self._policy = policy
        self._enforcer = EgressPolicyEnforcer(
            policy,
            manifests=manifests,
            clock=clock,
        )

    async def check_and_reserve(
        self,
        request: ReservationRequest,
        counter: TokenCounter,
        *,
        idempotency_key: str,
    ) -> Reservation:
        policy_hash = self._policy.policy_hash
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                return await self._reserve(
                    connection,
                    request,
                    counter,
                    policy_hash,
                    idempotency_key,
                )
        except EgressPolicyViolation:
            raise
        except psycopg.OperationalError as error:
            # The connection dropped. Whether the transaction committed is not
            # knowable from here, so the caller must not send and must not
            # reuse this key until the state is reconciled.
            raise ReservationAmbiguous() from error
        except psycopg.Error as error:
            raise LedgerUnavailable() from error

    async def rebind_policy(
        self,
        corpus_manifest_id: str,
        *,
        expected_policy_hash: str,
    ) -> PolicyRebindResult:
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                await _record_policy(connection, self._policy, self._policy.policy_hash)
                return await _rebind_policy(
                    connection,
                    corpus_manifest_id,
                    expected_policy_hash=expected_policy_hash,
                    new_policy_hash=self._policy.policy_hash,
                )
        except PolicyRebindConflict:
            raise
        except psycopg.OperationalError as error:
            raise PolicyRebindAmbiguous() from error
        except psycopg.Error as error:
            raise LedgerUnavailable() from error

    async def record_attempt(
        self,
        reservation_id: str,
        route: ProviderRouteBinding,
        request_size: RequestSize,
        outcome: AttemptOutcome,
        *,
        duration_ms: int,
        public_error_code: str | None = None,
    ) -> Attempt:
        attempt_id = str(uuid.uuid4())
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                await connection.execute(
                    """
                        INSERT INTO egress_attempt (
                            attempt_id, reservation_id, provider_id,
                            endpoint_purpose, outcome, request_tokens,
                            request_bytes, duration_ms, public_error_code
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                    (
                        attempt_id,
                        reservation_id,
                        route.provider_id,
                        route.endpoint_purpose,
                        outcome.value,
                        request_size.request_tokens,
                        request_size.request_bytes,
                        duration_ms,
                        public_error_code,
                    ),
                )
                await connection.execute(
                    "UPDATE egress_reservation SET state = %s "
                    "WHERE reservation_id = %s",
                    (
                        ReservationState.SUCCEEDED.value
                        if outcome is AttemptOutcome.SUCCEEDED
                        else ReservationState.FAILED_KNOWN.value,
                        reservation_id,
                    ),
                )
        except psycopg.OperationalError as error:
            raise ReservationAmbiguous() from error
        except psycopg.Error as error:
            raise LedgerUnavailable() from error
        return Attempt(
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            route=route,
            outcome=outcome,
            request_size=request_size,
            duration_ms=duration_ms,
            public_error_code=public_error_code,
        )

    async def seal_run(
        self,
        evaluation_root_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        connection = await self._connect()
        try:
            async with connection, connection.transaction():
                await connection.execute(
                    "INSERT INTO egress_run_seal (evaluation_root_id, run_id, reason) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (evaluation_root_id, run_id, reason),
                )
        except psycopg.Error as error:
            raise LedgerUnavailable() from error

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        try:
            return await psycopg.AsyncConnection.connect(
                self._conninfo,
                row_factory=tuple_row,
            )
        except psycopg.Error as error:
            raise LedgerUnavailable() from error

    async def _reserve(
        self,
        connection: psycopg.AsyncConnection[Any],
        request: ReservationRequest,
        counter: TokenCounter,
        policy_hash: str,
        idempotency_key: str,
    ) -> Reservation:
        corpus_manifest_id = request.version.corpus_manifest_id
        await _record_policy(connection, self._policy, policy_hash)

        # Locks first, replay lookup second. The other order leaves a window in
        # which concurrent holders of one idempotency key all read "no such
        # reservation" and then race to insert it; only one can win the unique
        # constraint and the rest fail for a reason that has nothing to do with
        # their budget. Holding both locks makes the lookup authoritative.
        locked_corpus = await _lock_corpus(connection, corpus_manifest_id, policy_hash)
        usage = await _lock_root(
            connection,
            request,
            policy_hash,
            corpus_manifest_id,
            locked_corpus.corpus_ledger_id,
        )

        await _require_unsealed(connection, request)

        replay = await _find_replay(connection, request, policy_hash, idempotency_key)
        if replay is not None:
            return replay

        outcome = self._enforcer.apply_reservation(
            usage, locked_corpus.usage, request, counter
        )

        reservation_id = str(uuid.uuid4())
        await _write_scopes(
            connection,
            request,
            outcome,
            corpus_manifest_id,
            locked_corpus.corpus_ledger_id,
        )
        await _write_reservation(
            connection,
            reservation_id,
            request,
            policy_hash,
            corpus_manifest_id,
            locked_corpus.corpus_ledger_id,
            idempotency_key,
        )
        return Reservation(
            reservation_id=reservation_id,
            idempotency_key=idempotency_key,
            evaluation_root_id=request.evaluation_root_id,
            run_id=request.run_id,
            policy_hash=policy_hash,
            corpus_manifest_id=corpus_manifest_id,
            route=request.route,
            state=ReservationState.RESERVED,
            usage=outcome.usage,
            corpus_usage=outcome.corpus_usage,
        )


async def _require_unsealed(
    connection: psycopg.AsyncConnection[Any],
    request: ReservationRequest,
) -> None:
    """Refuse a sealed run inside the same transaction that checks every cap."""
    row = await (
        await connection.execute(
            "SELECT reason FROM egress_run_seal "
            "WHERE evaluation_root_id = %s AND run_id = %s",
            (request.evaluation_root_id, request.run_id),
        )
    ).fetchone()
    if row is not None:
        raise RunSealed()


async def _record_policy(
    connection: psycopg.AsyncConnection[Any],
    policy: EgressPolicy,
    policy_hash: str,
) -> None:
    await connection.execute(
        "INSERT INTO egress_policy_snapshot (policy_hash, schema_version) "
        "VALUES (%s, %s) ON CONFLICT (policy_hash) DO NOTHING",
        (policy_hash, policy.schema_version),
    )


async def _rebind_policy(
    connection: psycopg.AsyncConnection[Any],
    corpus_manifest_id: str,
    *,
    expected_policy_hash: str,
    new_policy_hash: str,
) -> PolicyRebindResult:
    """Move one locked corpus head to an auditable successor epoch.

    Copying usage deliberately does not ask the new policy whether the inherited
    totals fit. The enforcer owns cap arithmetic and will refuse the next
    reservation if a tighter policy is already spent at either corpus scope.
    """
    head = await (
        await connection.execute(
            "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
            "WHERE corpus_manifest_id = %s FOR UPDATE",
            (corpus_manifest_id,),
        )
    ).fetchone()
    if head is None or head[0] is None:
        raise PolicyRebindConflict()

    active_ledger_id = str(head[0])
    active = await (
        await connection.execute(
            "SELECT policy_hash, corpus_usage, predecessor_ledger_id "
            "FROM egress_corpus_ledger WHERE corpus_ledger_id = %s FOR UPDATE",
            (active_ledger_id,),
        )
    ).fetchone()
    if active is None or active[1] is None:
        raise PolicyRebindConflict()

    active_policy_hash = str(active[0])
    active_usage = CorpusUsage.model_validate(active[1])
    predecessor_ledger_id = None if active[2] is None else str(active[2])

    if active_policy_hash == expected_policy_hash == new_policy_hash:
        return _policy_rebind_result(
            corpus_manifest_id,
            predecessor_ledger_id=active_ledger_id,
            successor_ledger_id=active_ledger_id,
            old_policy_hash=active_policy_hash,
            new_policy_hash=active_policy_hash,
            usage=active_usage,
            rebound=False,
        )

    if active_policy_hash != expected_policy_hash:
        if active_policy_hash != new_policy_hash or predecessor_ledger_id is None:
            raise PolicyRebindConflict()
        predecessor = await (
            await connection.execute(
                "SELECT policy_hash, corpus_usage FROM egress_corpus_ledger "
                "WHERE corpus_ledger_id = %s AND corpus_manifest_id = %s",
                (predecessor_ledger_id, corpus_manifest_id),
            )
        ).fetchone()
        if predecessor is None or predecessor[1] is None:
            raise PolicyRebindConflict()
        predecessor_usage = CorpusUsage.model_validate(predecessor[1])
        if (
            str(predecessor[0]) != expected_policy_hash
            or predecessor_usage.policy_hash != expected_policy_hash
            or predecessor_usage.corpus_manifest_id != corpus_manifest_id
        ):
            raise PolicyRebindConflict()
        return _policy_rebind_result(
            corpus_manifest_id,
            predecessor_ledger_id=predecessor_ledger_id,
            successor_ledger_id=active_ledger_id,
            old_policy_hash=expected_policy_hash,
            new_policy_hash=active_policy_hash,
            usage=predecessor_usage,
            rebound=False,
        )

    successor_usage = successor_corpus_usage(active_usage, new_policy_hash)
    successor_ledger_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO egress_corpus_ledger (
            corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage,
            unique_excerpts, unique_tokens, unique_bytes, predecessor_ledger_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            successor_ledger_id,
            corpus_manifest_id,
            new_policy_hash,
            successor_usage.model_dump_json(),
            len(successor_usage.disclosure_ids),
            successor_usage.unique_tokens,
            successor_usage.unique_bytes,
            active_ledger_id,
        ),
    )
    updated = await connection.execute(
        "UPDATE egress_corpus_ledger_head "
        "SET corpus_ledger_id = %s, updated_at = now() "
        "WHERE corpus_manifest_id = %s AND corpus_ledger_id = %s",
        (successor_ledger_id, corpus_manifest_id, active_ledger_id),
    )
    if updated.rowcount != 1:
        raise PolicyRebindConflict()
    return _policy_rebind_result(
        corpus_manifest_id,
        predecessor_ledger_id=active_ledger_id,
        successor_ledger_id=successor_ledger_id,
        old_policy_hash=active_policy_hash,
        new_policy_hash=new_policy_hash,
        usage=successor_usage,
        rebound=True,
    )


def _policy_rebind_result(
    corpus_manifest_id: str,
    *,
    predecessor_ledger_id: str,
    successor_ledger_id: str,
    old_policy_hash: str,
    new_policy_hash: str,
    usage: CorpusUsage,
    rebound: bool,
) -> PolicyRebindResult:
    return PolicyRebindResult(
        corpus_manifest_id=corpus_manifest_id,
        predecessor_ledger_id=predecessor_ledger_id,
        successor_ledger_id=successor_ledger_id,
        old_policy_hash=old_policy_hash,
        new_policy_hash=new_policy_hash,
        inherited_unique_excerpts=len(usage.disclosure_ids),
        inherited_unique_tokens=usage.unique_tokens,
        inherited_unique_bytes=usage.unique_bytes,
        rebound=rebound,
    )


async def _lock_corpus(
    connection: psycopg.AsyncConnection[Any],
    corpus_manifest_id: str,
    policy_hash: str,
) -> _LockedCorpus:
    """Lock the corpus head, then its active epoch, before the root lock."""
    await connection.execute(
        """
        INSERT INTO egress_corpus_ledger_head (
            corpus_manifest_id, corpus_ledger_id
        ) VALUES (%s, NULL)
        ON CONFLICT (corpus_manifest_id) DO NOTHING
        """,
        (corpus_manifest_id,),
    )
    head = await (
        await connection.execute(
            "SELECT corpus_ledger_id FROM egress_corpus_ledger_head "
            "WHERE corpus_manifest_id = %s FOR UPDATE",
            (corpus_manifest_id,),
        )
    ).fetchone()
    if head is None:
        raise LedgerUnavailable("corpus ledger head disappeared while locked")

    corpus_ledger_id = str(uuid.uuid4()) if head[0] is None else str(head[0])
    if head[0] is None:
        await connection.execute(
            """
            INSERT INTO egress_corpus_ledger (
                corpus_ledger_id, corpus_manifest_id, policy_hash, corpus_usage,
                unique_excerpts, unique_tokens, unique_bytes
            ) VALUES (%s, %s, %s, %s, 0, 0, 0)
            """,
            (corpus_ledger_id, corpus_manifest_id, policy_hash, "null"),
        )
        await connection.execute(
            """
            UPDATE egress_corpus_ledger_head
            SET corpus_ledger_id = %s, updated_at = now()
            WHERE corpus_manifest_id = %s
            """,
            (corpus_ledger_id, corpus_manifest_id),
        )

    epoch = await (
        await connection.execute(
            "SELECT corpus_usage FROM egress_corpus_ledger "
            "WHERE corpus_ledger_id = %s FOR UPDATE",
            (corpus_ledger_id,),
        )
    ).fetchone()
    if epoch is None:
        raise LedgerUnavailable("active corpus ledger epoch is unavailable")
    usage = None if epoch[0] is None else CorpusUsage.model_validate(epoch[0])
    return _LockedCorpus(corpus_ledger_id=corpus_ledger_id, usage=usage)


async def _lock_root(
    connection: psycopg.AsyncConnection[Any],
    request: ReservationRequest,
    policy_hash: str,
    corpus_manifest_id: str,
    corpus_ledger_id: str,
) -> UsageSnapshot | None:
    await connection.execute(
        """
        INSERT INTO egress_evaluation_root (
            evaluation_root_id, policy_hash, task_level,
            corpus_manifest_id, corpus_ledger_id, usage_snapshot
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (evaluation_root_id) DO NOTHING
        """,
        (
            request.evaluation_root_id,
            policy_hash,
            request.task_level.value,
            corpus_manifest_id,
            corpus_ledger_id,
            "null",
        ),
    )
    row = await (
        await connection.execute(
            "SELECT usage_snapshot, corpus_ledger_id FROM egress_evaluation_root "
            "WHERE evaluation_root_id = %s FOR UPDATE",
            (request.evaluation_root_id,),
        )
    ).fetchone()
    if row is None:
        return None
    if str(row[1]) != corpus_ledger_id:
        raise EgressPolicyViolation(
            "policy_snapshot_mismatch",
            "evaluation root belongs to another corpus ledger epoch",
        )
    if row[0] is None:
        return None
    return UsageSnapshot.model_validate(row[0])


async def _write_scopes(
    connection: psycopg.AsyncConnection[Any],
    request: ReservationRequest,
    outcome: ReservationOutcome,
    corpus_manifest_id: str,
    corpus_ledger_id: str,
) -> None:
    corpus_usage = outcome.corpus_usage
    usage = outcome.usage
    await connection.execute(
        """
        UPDATE egress_corpus_ledger
        SET corpus_usage = %s, unique_excerpts = %s, unique_tokens = %s,
            unique_bytes = %s, updated_at = now()
        WHERE corpus_ledger_id = %s
        """,
        (
            corpus_usage.model_dump_json(),
            len(corpus_usage.disclosure_ids),
            corpus_usage.unique_tokens,
            corpus_usage.unique_bytes,
            corpus_ledger_id,
        ),
    )
    await connection.execute(
        "UPDATE egress_evaluation_root SET usage_snapshot = %s, updated_at = now() "
        "WHERE evaluation_root_id = %s",
        (usage.model_dump_json(), request.evaluation_root_id),
    )
    for fact in request.disclosures:
        await connection.execute(
            """
            INSERT INTO egress_route_disclosure (
                corpus_manifest_id, provider_id, endpoint_purpose, disclosure_id
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                corpus_manifest_id,
                request.route.provider_id,
                request.route.endpoint_purpose,
                fact.disclosure_id,
            ),
        )


async def _write_reservation(
    connection: psycopg.AsyncConnection[Any],
    reservation_id: str,
    request: ReservationRequest,
    policy_hash: str,
    corpus_manifest_id: str,
    corpus_ledger_id: str,
    idempotency_key: str,
) -> None:
    await connection.execute(
        """
        INSERT INTO egress_reservation (
            reservation_id, idempotency_key, evaluation_root_id, run_id,
            policy_hash, corpus_manifest_id, corpus_ledger_id, stage, provider_id,
            endpoint_purpose, provider_use, model_id, state
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            reservation_id,
            idempotency_key,
            request.evaluation_root_id,
            request.run_id,
            policy_hash,
            corpus_manifest_id,
            corpus_ledger_id,
            request.stage.value,
            request.route.provider_id,
            request.route.endpoint_purpose,
            request.route.use.value,
            request.model_id,
            ReservationState.RESERVED.value,
        ),
    )
    for fact in request.disclosures:
        await connection.execute(
            """
            INSERT INTO egress_reservation_disclosure (
                reservation_id, disclosure_id, token_count, byte_count
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (reservation_id, fact.disclosure_id, fact.token_count, fact.byte_count),
        )


async def _find_replay(
    connection: psycopg.AsyncConnection[Any],
    request: ReservationRequest,
    policy_hash: str,
    idempotency_key: str,
) -> Reservation | None:
    """Return the stored reservation for a repeated key, without re-applying caps.

    A replay is a request that never reached a provider, so charging it
    transmitted usage again would spend budget on nothing.
    """
    row = await (
        await connection.execute(
            """
            SELECT r.reservation_id, r.state, e.usage_snapshot, c.corpus_usage
            FROM egress_reservation r
            JOIN egress_evaluation_root e
              ON e.evaluation_root_id = r.evaluation_root_id
            JOIN egress_corpus_ledger c
              ON c.corpus_ledger_id = r.corpus_ledger_id
            WHERE r.evaluation_root_id = %s AND r.run_id = %s
              AND r.policy_hash = %s AND r.idempotency_key = %s
            """,
            (
                request.evaluation_root_id,
                request.run_id,
                policy_hash,
                idempotency_key,
            ),
        )
    ).fetchone()
    if row is None:
        return None
    reservation_id, state, usage_snapshot, corpus_usage = row
    return Reservation(
        reservation_id=str(reservation_id),
        idempotency_key=idempotency_key,
        evaluation_root_id=request.evaluation_root_id,
        run_id=request.run_id,
        policy_hash=policy_hash,
        corpus_manifest_id=request.version.corpus_manifest_id,
        route=request.route,
        state=ReservationState(state),
        usage=UsageSnapshot.model_validate(usage_snapshot),
        corpus_usage=CorpusUsage.model_validate(corpus_usage),
        replayed=True,
    )


__all__ = ["PostgresEgressLedger"]
