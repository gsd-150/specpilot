"""Registration for the §8.1.1 adversarial subset.

The freeze gate checks two things about this subset: that the split sizes are
six and ten, and that dev and locked share no item id and no family. Section
8.1.1 requires four axes to be disjoint, not two — claims and distractor clauses
as well. Those two are unenforceable at the gate because the status file it
reads deliberately carries no claim text, so they are enforced here, at the one
point in the pipeline where the material is still in front of the author.

That gap is not academic. A locked group that restates a dev claim under a fresh
id and a fresh family satisfies every check `evaluation/freeze.py` performs, and
removes the isolation the locked split exists to provide — a subset that no
longer measures what the report will say it measures, with nothing anywhere
recording that it stopped.

The overlap report is diagnostic and the status is fail-closed. Building a
report on groups that overlap is how the author finds out what to fix, so that
step reports rather than raises; turning that report into a registration status
refuses, because a status is an input to a freeze.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from specpilot.contracts.annotation import Split
from specpilot.contracts.l2_adv import AdversarialGroup
from specpilot.manifests.canonical import canonical_json, canonical_sha256

_DEV_REQUIRED = 6
_LOCKED_REQUIRED = 10


class AdversarialRegistrationError(ValueError):
    """A closed refusal carrying one stable reason."""


class AdversarialGroupExistsError(ValueError):
    """A stored group is never silently replaced.

    Overwriting is how a locked group quietly becomes a different one after the
    split was frozen, which no downstream check could detect: the status the
    gate reads carries identifiers, and the identifier would not have changed.
    """


class AdversarialGroupStore:
    """One JSON record per group, written once."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create(self, group: AdversarialGroup) -> AdversarialGroup:
        record = group.model_copy(update={"group_record_id": None})
        stored = record.model_copy(
            update={"group_record_id": canonical_sha256(record)}
        )
        self._directory.mkdir(parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        path = self._directory / f"{stored.group_id}.json"
        data = canonical_json(stored)
        if path.exists():
            if path.read_bytes() != data:
                raise AdversarialGroupExistsError(stored.group_id)
            return stored
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return stored

    def iter_groups(self) -> Iterator[AdversarialGroup]:
        if not self._directory.exists():
            return
        for path in sorted(self._directory.glob("*.json")):
            yield AdversarialGroup.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )

    def read_all(self) -> tuple[AdversarialGroup, ...]:
        return tuple(self.iter_groups())


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DisjointCheck(_FrozenModel):
    """One axis of §8.1.1 mutual exclusion.

    ``shared`` holds identifiers and hex digests only. A claim is compared by
    the SHA-256 of its normalized text so that the report can attest the claim
    axis without ever carrying a claim — the report is hashed into a run spec,
    and §8.1 keeps authored answer material out of records that travel.
    """

    disjoint: bool
    shared: tuple[str, ...] = ()


class AdversarialOverlapReport(_FrozenModel):
    schema_version: Literal["l2-adv-overlap/v1"] = "l2-adv-overlap/v1"
    registration_sha256: str
    dev_count: int
    locked_count: int
    checks: dict[str, DisjointCheck]
    dimension_counts: dict[str, int]

    @property
    def clean(self) -> bool:
        return all(check.disjoint for check in self.checks.values())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _claim_digests(group: AdversarialGroup) -> frozenset[str]:
    return frozenset(
        {
            _sha256(group.negative_claim.strip()),
            _sha256(group.positive_claim.strip()),
        }
    )


def _positive_pair_digest(group: AdversarialGroup) -> frozenset[str]:
    """The pair identity, so a locked group cannot reuse a dev pair wholesale.

    Rewriting only the negative half still hands the Verifier a positive item it
    has already been developed against, which is why §8.1.1 lists the positive
    pair as its own axis rather than folding it into the claim axis.
    """
    joined = "\n".join(
        [group.positive_claim.strip(), *sorted(group.supporting_clause_ids)]
    )
    return frozenset({_sha256(joined)})


_AXES: dict[str, Callable[[AdversarialGroup], Iterable[str]]] = {
    "group_id": lambda group: (group.group_id,),
    "family": lambda group: (group.family,),
    "claim": _claim_digests,
    "distractor_clause": lambda group: group.distractor_clause_ids,
    "positive_pair": _positive_pair_digest,
}


def _split_groups(
    groups: tuple[AdversarialGroup, ...],
) -> tuple[tuple[AdversarialGroup, ...], tuple[AdversarialGroup, ...]]:
    dev = tuple(group for group in groups if group.split is Split.DEV)
    locked = tuple(group for group in groups if group.split is Split.LOCKED)
    return dev, locked


def _registration_digest(groups: tuple[AdversarialGroup, ...]) -> str:
    """Binds a report to the exact groups it was computed over.

    Without it a stale clean report could be presented alongside a later,
    overlapping registration and the status would build.
    """
    return _sha256("\n".join(sorted(canonical_sha256(group) for group in groups)))


def build_overlap_report(
    groups: tuple[AdversarialGroup, ...],
) -> AdversarialOverlapReport:
    """Report §8.1.1 mutual exclusion across all four axes, plus identifiers."""
    dev, locked = _split_groups(groups)
    checks: dict[str, DisjointCheck] = {}
    for axis, extract in _AXES.items():
        dev_values = {value for group in dev for value in extract(group)}
        locked_values = {value for group in locked for value in extract(group)}
        shared = dev_values & locked_values
        checks[axis] = DisjointCheck(
            disjoint=not shared, shared=tuple(sorted(shared))
        )
    return AdversarialOverlapReport(
        registration_sha256=_registration_digest(groups),
        dev_count=len(dev),
        locked_count=len(locked),
        checks=checks,
        dimension_counts=dict(
            Counter(group.dimension.value for group in groups)
        ),
    )


def overlap_report_sha256(report: AdversarialOverlapReport) -> str:
    return canonical_sha256(report)


def build_registration_status(
    groups: tuple[AdversarialGroup, ...],
    report: AdversarialOverlapReport,
) -> dict[str, Any]:
    """Produce the `l2-adv-registration/v1` status the freeze reads.

    Identifiers and families only. The claim and distractor axes are attested by
    having refused to build unless the report says they are disjoint, not by
    shipping the text for the gate to re-check — the gate is downstream of the
    only place that text is allowed to be.
    """
    if report.registration_sha256 != _registration_digest(groups):
        raise AdversarialRegistrationError(
            "overlap report was computed over different groups"
        )

    dev, locked = _split_groups(groups)
    if len(dev) != _DEV_REQUIRED or len(locked) != _LOCKED_REQUIRED:
        raise AdversarialRegistrationError(
            "l2_adv cardinality mismatch: "
            f"{len(dev)} dev and {len(locked)} locked, "
            f"required {_DEV_REQUIRED} and {_LOCKED_REQUIRED}"
        )
    if not report.clean:
        failed = sorted(
            axis for axis, check in report.checks.items() if not check.disjoint
        )
        raise AdversarialRegistrationError(
            f"dev and locked overlap on {', '.join(failed)}"
        )

    return {
        "schema_version": "l2-adv-registration/v1",
        "dev": {
            "item_ids": [group.group_id for group in dev],
            "families": [group.family for group in dev],
        },
        "locked": {
            "item_ids": [group.group_id for group in locked],
            "families": [group.family for group in locked],
        },
        "overlap_report_sha256": overlap_report_sha256(report),
    }
