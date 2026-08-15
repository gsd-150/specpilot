"""Narrow views of the progress report, shaped for the freeze gate.

The gate reads three small files whose every number the progress report already
computes. Recomputing them here would create a second source of the same counts,
and the two would eventually disagree — surfacing as a freeze refusal citing a
figure nobody could reproduce from the store. So this projects; it never counts.

Projections are faithful, including when they are short. Rounding an incomplete
split up to its target would move the completeness decision out of the gate,
which records it, and into a helper that does not.

An absent input refuses rather than projecting a zero. A store that was never
pooled and a pooling audit that found nothing produce very different files here
and the same one at the gate, and only the refusal keeps them apart.
"""

from __future__ import annotations

from typing import Any

from specpilot.annotation.progress import ProgressReport, SetProgress


class FreezeStatusError(ValueError):
    """An input the gate requires is absent. Never projected as an empty one."""


def _level(progress: SetProgress) -> dict[str, int]:
    return {
        "target_total": progress.target_total,
        "completed_total": progress.completed_total,
        "target_dev": progress.target_dev,
        "completed_dev": progress.completed_dev,
        "target_locked": progress.target_locked,
        "completed_locked": progress.completed_locked,
    }


def build_progress_status(report: ProgressReport) -> dict[str, Any]:
    """The L1 and L2 split counts, and nothing else the gate does not read."""
    return {"l1": _level(report.l1), "l2": _level(report.l2)}


def build_deep_review_status(*, expected: int, recorded: int) -> dict[str, int]:
    """The pre-registered sample size and how much of it was done.

    ``expected`` comes from the declared rate and salt rather than from the
    findings on disk. Deriving it from what was recorded would make coverage
    trivially complete: the sample would be defined as whatever got reviewed,
    which is the one thing the pre-registration exists to prevent.
    """
    if expected <= 0:
        raise FreezeStatusError("deep review sample was never declared")
    return {"required": expected, "completed": recorded}


def build_pooling_status(report: ProgressReport) -> dict[str, Any]:
    """The audit totals, plus whether every run individually sealed.

    ``fully_sealed`` is the audit's own aggregate. ``all_runs_sealed`` is
    computed across runs because the gate checks both, and a later run left
    open is exactly the state the aggregate can be least specific about.
    """
    audit = report.pooling_audit
    if audit is None:
        raise FreezeStatusError("no pooling audit was recorded")
    if not audit.runs:
        raise FreezeStatusError("pooling audit has no runs")
    return {
        "registered_items": audit.registered_items,
        "adjudicated_items": audit.adjudicated_items,
        "blocked": audit.blocked,
        "fully_sealed": audit.fully_sealed,
        "all_runs_sealed": all(run.sealed for run in audit.runs),
    }
