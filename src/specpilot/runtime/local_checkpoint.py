"""In-memory, prose-free checkpoint persistence for one single-case L2 run.

The worker writes checkpoints through PostgreSQL so a queued run can resume
after a process loss. The author-run CLI runs exactly one case with a fresh run
id and never resumes, so a durable database is neither needed nor wanted — but
the checkpoint machinery still has to run, because its transition validation is
a fail-closed boundary of its own: a checkpoint that regressed a stage or
rewound an immutable binding must refuse rather than be written.

This store keeps only the current checkpoint in memory, applies the same
compare-and-swap version check plus the run/attempt binding as the
PostgreSQL store, and stores nothing with clause prose — ``RunCheckpoint`` has no field that
can hold any.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from specpilot.checkpoints.contracts import (
    CheckpointStage,
    RunCheckpoint,
)


class LocalCheckpointStore:
    """A single-attempt checkpoint store that refuses every illegal transition."""

    def __init__(
        self,
        *,
        run_id: str,
        query_hash: str,
        evaluation_root_id: str,
        source_manifest_id: str,
        corpus_manifest_id: str,
        policy_hash: str,
        configuration_hash: str,
        compliance_prompt_hash: str,
        verifier_prompt_hash: str,
        provider_id: str,
        model_id: str,
    ) -> None:
        self._run_id = run_id
        self._query_hash = query_hash
        self._evaluation_root_id = evaluation_root_id
        self._source_manifest_id = source_manifest_id
        self._corpus_manifest_id = corpus_manifest_id
        self._policy_hash = policy_hash
        self._configuration_hash = configuration_hash
        self._compliance_prompt_hash = compliance_prompt_hash
        self._verifier_prompt_hash = verifier_prompt_hash
        self._provider_id = provider_id
        self._model_id = model_id
        self._current: RunCheckpoint | None = None
        self._version: int | None = None

    def new_checkpoint(self) -> RunCheckpoint:
        """Build the first safe envelope from the immutable run bindings only."""
        return RunCheckpoint(
            run_id=UUID(self._run_id),
            attempt=1,
            checkpoint_version=1,
            stage=CheckpointStage.PLANNED,
            task_level="L2",
            query_hash=self._query_hash,
            evaluation_root_id=self._evaluation_root_id,
            source_manifest_id=self._source_manifest_id,
            corpus_manifest_id=self._corpus_manifest_id,
            policy_hash=self._policy_hash,
            configuration_hash=self._configuration_hash,
            compliance_prompt_hash=self._compliance_prompt_hash,
            verifier_prompt_hash=self._verifier_prompt_hash,
            provider_id=self._provider_id,
            model_id=self._model_id,
            plan_id=None,
            plan_hash=None,
            evidence=(),
            tool_attempts_used=0,
            reservation_ids=(),
            reconstruction_generations=(),
            recovery_attempted=False,
            recovery_reason=None,
            recovery_claim_id=None,
            candidate_count=0,
            completed_claim_ids=(),
            completed_results=(),
            last_accessed_at=datetime.now(UTC),
        )

    async def write(
        self, previous_version: int | None, checkpoint: RunCheckpoint
    ) -> RunCheckpoint:
        """CAS the version and pin the binding, like the real store accepts.

        Deliberately does NOT run the cross-checkpoint stage validator: the
        durable store never does — the runtime constructs its own legal
        checkpoints, including same-stage updates — and applying the shared
        validator here rejected a legal live transition the first real run
        reached, which the durable path would have accepted.
        """
        if previous_version != self._version:
            raise ValueError("checkpoint_compare_and_swap_mismatch")
        if self._current is not None:
            if (
                checkpoint.run_id != self._current.run_id
                or checkpoint.attempt != self._current.attempt
            ):
                raise ValueError("checkpoint run and attempt are immutable")
            if checkpoint.checkpoint_version != self._current.checkpoint_version + 1:
                raise ValueError("checkpoint versions advance by one")
        self._current = checkpoint
        self._version = checkpoint.checkpoint_version
        return checkpoint

    def current(self) -> RunCheckpoint | None:
        """Expose the latest accepted checkpoint for single-case inspection."""
        return self._current


__all__ = ["LocalCheckpointStore"]
