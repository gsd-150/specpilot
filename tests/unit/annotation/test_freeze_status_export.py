"""Projecting the progress report onto what the freeze gate reads.

The gate's three status files are narrow views of numbers `annotation progress`
already computes. Recomputing them separately would create a second source that
can disagree with the first, and the disagreement would surface as a freeze
refusal citing a count nobody could reproduce from the store — so these project,
they do not recount.

Asserted against the gate's own readers and its own predicates rather than
against expectations written here. A projection that satisfies a local
assertion and trips `incomplete_l1` has tested nothing.
"""

from __future__ import annotations

import pytest

from specpilot.annotation.freeze_status import (
    FreezeStatusError,
    build_deep_review_status,
    build_pooling_status,
    build_progress_status,
)
from specpilot.annotation.progress import (
    PoolingAuditProgress,
    PoolingRunProgress,
    ProgressReport,
    ProvenanceProgress,
    SetProgress,
)
from specpilot.evaluation import freeze as freeze_module


def _set_progress(name: str, total: int, dev: int, locked: int) -> SetProgress:
    return SetProgress(
        set_name=name,
        target_total=total,
        target_dev=dev,
        target_locked=locked,
        completed_total=total,
        completed_dev=dev,
        completed_locked=locked,
        unanswerable_dev=3,
        unanswerable_locked=5,
        unanswerable_floor_dev=3,
        unanswerable_floor_locked=5,
        clause_first=0,
        scenario_first=total,
        clause_first_target=None,
        provenance=ProvenanceProgress(
            content_origins={},
            label_origins={},
            gold_origins={},
            gold_origin_chains={},
            retrieval_originated_gold_items=0,
        ),
        verdict_counts={},
        awaiting_adjudication=0,
        gold_clauses=total,
        pooled_gold_clauses=0,
    )


def _audit(*, sealed: bool) -> PoolingAuditProgress:
    run = PoolingRunProgress(
        run_id="a" * 64,
        registered_items=60,
        adjudicated_items=60,
        gold_complete=52,
        gold_extended=8,
        blocked=0,
        added_gold_clauses=10,
        sealed=sealed,
    )
    return PoolingAuditProgress(
        registered_items=60,
        adjudicated_items=60,
        gold_complete=52,
        gold_extended=8,
        blocked=0,
        added_gold_clauses=10,
        fully_sealed=sealed,
        runs=(run,),
    )


def _report(**overrides: object) -> ProgressReport:
    fields: dict[str, object] = {
        "l1": _set_progress("l1", 40, 15, 25),
        "l2": _set_progress("l2", 20, 8, 12),
        "annotated_items": 60,
        "superseded_count": 0,
        "pooling_audit": _audit(sealed=True),
    }
    fields.update(overrides)
    return ProgressReport(**fields)  # type: ignore[arg-type]


def test_the_progress_projection_clears_the_gate_predicates() -> None:
    status = build_progress_status(_report())
    parsed = freeze_module._ProgressStatus.model_validate(status)

    assert parsed.l1 == freeze_module._LevelProgress(
        target_total=40,
        completed_total=40,
        target_dev=15,
        completed_dev=15,
        target_locked=25,
        completed_locked=25,
    )
    assert parsed.l2 == freeze_module._LevelProgress(
        target_total=20,
        completed_total=20,
        target_dev=8,
        completed_dev=8,
        target_locked=12,
        completed_locked=12,
    )


def test_the_deep_review_projection_clears_the_gate_predicate() -> None:
    status = build_deep_review_status(expected=12, recorded=12)
    parsed = freeze_module._DeepReviewStatus.model_validate(status)

    assert parsed.required == 12
    assert parsed.completed == parsed.required


def test_the_pooling_projection_clears_the_gate_predicate() -> None:
    status = build_pooling_status(_report())
    parsed = freeze_module._PoolingStatus.model_validate(status)

    assert parsed.fully_sealed
    assert parsed.all_runs_sealed
    assert parsed.blocked == 0
    assert parsed.adjudicated_items == parsed.registered_items


def test_incomplete_progress_is_projected_faithfully_not_rounded_up() -> None:
    """The projection reports the store, and the gate decides.

    Massaging a short count into a passing one here would move the decision to
    the one place with no record of having made it.
    """
    short = _report(l1=_set_progress("l1", 40, 15, 25))
    object.__setattr__(short.l1, "completed_total", 39)
    object.__setattr__(short.l1, "completed_locked", 24)

    status = build_progress_status(short)

    assert status["l1"]["completed_total"] == 39
    assert status["l1"]["completed_locked"] == 24


def test_a_report_without_a_pooling_audit_refuses(
) -> None:
    """Absent is not zero. A store that was never audited must not project as
    an audit that found nothing."""
    with pytest.raises(FreezeStatusError):
        build_pooling_status(_report(pooling_audit=None))


def test_an_unsealed_run_makes_all_runs_sealed_false() -> None:
    unsealed = _report(pooling_audit=_audit(sealed=False))

    status = build_pooling_status(unsealed)

    assert status["all_runs_sealed"] is False


def test_no_status_carries_a_key_the_freeze_forbids() -> None:
    rendered = repr(
        [
            build_progress_status(_report()),
            build_deep_review_status(expected=12, recorded=12),
            build_pooling_status(_report()),
        ]
    )

    for forbidden in ("question", "claim", "excerpt", "answer", "rationale"):
        assert forbidden not in rendered
