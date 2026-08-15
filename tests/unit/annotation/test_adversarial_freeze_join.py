"""The join between registration and the freeze gate.

Every defect this project found by running the real path had the same shape: a
value present in the code and absent from the bytes that actually left. A status
builder tested only against its own assertions is exactly that shape — it can
satisfy every expectation written next to it and still produce a file the gate
refuses, and nothing would say so until a freeze was attempted.

So this reads the file the builder writes with the reader the gate uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.annotation.adversarial import (
    build_overlap_report,
    build_registration_status,
)
from specpilot.contracts.annotation import (
    AnnotationOrigin,
    GoldOrigin,
    GoldOriginEvent,
    Split,
    Verdict,
)
from specpilot.contracts.l2_adv import AdversarialDimension, AdversarialGroup
from specpilot.evaluation import freeze as freeze_module

_DIMENSIONS = tuple(AdversarialDimension)
_REVIEWED = (GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),)


def _group(index: int, split: Split) -> AdversarialGroup:
    tag = f"{split.value}-{index:03d}"
    base = index + (0 if split is Split.DEV else 1_000)
    return AdversarialGroup(
        group_id=f"adv-{tag}",
        family=f"family-{tag}",
        split=split,
        dimension=_DIMENSIONS[index % len(_DIMENSIONS)],
        negative_claim_id=f"adv-{tag}-neg",
        negative_claim=f"the proxy must reject request {tag}",
        distractor_clause_ids=(f"{base:064x}",),
        positive_claim_id=f"adv-{tag}-pos",
        positive_claim=f"the origin server must reject request {tag}",
        supporting_clause_ids=(f"{base + 100:064x}",),
        proposed_verdict=Verdict.VIOLATING,
        content_origin=AnnotationOrigin.HUMAN,
        label_origin=AnnotationOrigin.HUMAN,
        construction_origins=_REVIEWED,
    )


def _registered_status_path(tmp_path: Path) -> Path:
    groups = tuple(
        [_group(index, Split.DEV) for index in range(6)]
        + [_group(index, Split.LOCKED) for index in range(10)]
    )
    status = build_registration_status(groups, build_overlap_report(groups))
    path = tmp_path / "l2-adv-status.json"
    path.write_text(json.dumps(status), encoding="utf-8")
    return path


def test_the_gate_reader_accepts_what_the_builder_writes(tmp_path: Path) -> None:
    advanced = freeze_module._read_status(
        _registered_status_path(tmp_path),
        freeze_module._AdvancedStatus,
        "invalid_l2_adv_status",
    )

    assert len(advanced.dev.item_ids) == 6
    assert len(advanced.locked.item_ids) == 10
    assert not set(advanced.dev.item_ids) & set(advanced.locked.item_ids)
    assert not set(advanced.dev.families) & set(advanced.locked.families)


def test_the_written_status_clears_the_gate_cardinality_and_overlap_rules(
    tmp_path: Path,
) -> None:
    """The three refusals `freeze.py` raises for this subset, asserted here.

    `l2_adv_cardinality_mismatch`, `l2_adv_id_overlap`, and
    `l2_adv_family_overlap` are the gate's whole view of the subset. A status
    that trips any of them is a wasted freeze attempt.
    """
    advanced = freeze_module._read_status(
        _registered_status_path(tmp_path),
        freeze_module._AdvancedStatus,
        "invalid_l2_adv_status",
    )

    assert (len(advanced.dev.item_ids), len(advanced.locked.item_ids)) == (6, 10)
    assert not set(advanced.dev.item_ids) & set(advanced.locked.item_ids)
    assert not set(advanced.dev.families) & set(advanced.locked.families)


def test_the_gate_refuses_a_status_carrying_claim_text(tmp_path: Path) -> None:
    """Proves the forbidden-key rule is real, not assumed.

    The builder omits claim text by construction; this shows what would happen
    if it did not, so the omission is load-bearing rather than incidental.
    """
    path = _registered_status_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dev"]["claim"] = "the proxy must reject the request"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(freeze_module.EvaluationFreezeError):
        freeze_module._read_status(
            path, freeze_module._AdvancedStatus, "invalid_l2_adv_status"
        )
