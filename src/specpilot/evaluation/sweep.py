"""Select the cases of one evaluation split, and refuse a sweep that is short.

The author's dev batches ran from `tmp/`, which is gitignored: `run_l1_dev.sh`,
`run_l2_dev.sh`, and `dump_dev_items.py` were never in the repository, carried no
test, and hardcoded `split == "dev"`. W6 spends fifty-seven live invocations
against a one-shot first-run boundary, so selection moves here first.

Three things changed in the move.

**Heads, not roots.** Both drivers selected on `predecessor_annotation_id is
None`, which is the *root* of an amendment chain — the original record. The
restricted store holds 81 amendments over 61 items, and nine items carry gold
their root does not, six of them locked L2. The sweeps survived it because they
read only `question` and `amend` cannot change a question. Anything reading gold
would not have, and W6's scoring reads gold.

**The count is checked.** `run_l1_dev.sh` computes the case count, prints it,
and never compares it to anything. A filter matching nothing prints `running 0
cases` and exits 0, which on a one-shot locked run is a failure shaped exactly
like a success.

**The split is required.** §8.5 keeps the locked splits unread until W6. A
parameter with a default is one that gets defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from specpilot.annotation.store import Annotation, AnnotationStore
from specpilot.contracts.annotation import L2Annotation, Split


class SweepLevel(StrEnum):
    L1 = "l1"
    L2 = "l2"
    L2_ADV = "l2-adv"


class SweepSelectionError(ValueError):
    """A selection that must not become a run."""


@dataclass(frozen=True)
class SweepCase:
    """One case, one provider invocation, one evaluation root.

    A root is one question (§3.2) and the ledger refuses a second question
    under a reused root, so the caller mints one per case rather than per
    sweep.
    """

    case_id: str
    question: str
    document_id: str
    expected_refusal: bool
    gold_clause_ids: tuple[str, ...]


def _level_of(record: Annotation) -> SweepLevel:
    # L2Annotation subclasses L1Annotation, so the L2 test comes first. Written
    # the other way round it pools both sets into one run and the count check
    # then confirms the wrong number.
    return SweepLevel.L2 if isinstance(record, L2Annotation) else SweepLevel.L1


def current_heads(store: AnnotationStore) -> tuple[Annotation, ...]:
    """The record that is current for each item, ordered by item id.

    Superseded records are dropped and retired items are removed entirely. An
    item owning two heads refuses: it means the chain forked, and picking
    either one silently would evaluate against a record nobody adjudicated.
    """
    records = tuple(store.iter_records())
    superseded = {
        record.predecessor_annotation_id
        for record in records
        if record.predecessor_annotation_id is not None
    }
    retired = {entry.item_id for entry in store.read_retirements()}
    by_item: dict[str, Annotation] = {}
    for record in records:
        if record.annotation_id in superseded or record.item_id in retired:
            continue
        if record.item_id in by_item:
            raise SweepSelectionError(
                f"sweep_ambiguous_head: item {record.item_id!r} owns two heads"
            )
        by_item[record.item_id] = record
    return tuple(by_item[item_id] for item_id in sorted(by_item))


def select_cases(
    store: AnnotationStore,
    *,
    level: SweepLevel,
    split: Split,
    expected: int,
    include_unanswerable: bool = False,
) -> tuple[SweepCase, ...]:
    """The cases of one level and split, or a refusal.

    ``expected`` is the count the caller believes it is about to run. It is
    required rather than inferred: inferring it would make every filter bug a
    silently smaller run, which is the one failure a first-run boundary cannot
    absorb.

    Expected-refusal items are excluded by default because the judge scores
    answered cases only. The locked L1 run needs all twenty-five and asks.
    """
    selected = tuple(
        record
        for record in current_heads(store)
        if _level_of(record) is level
        and record.split is split
        and (include_unanswerable or not record.expected_refusal)
    )
    if not selected:
        raise SweepSelectionError(
            f"sweep_empty_selection: no {level.value} case in split "
            f"{split.value}; a sweep of nothing exits successfully and "
            "proves nothing"
        )
    if len(selected) != expected:
        raise SweepSelectionError(
            f"sweep_count_mismatch: selected {len(selected)} "
            f"{level.value} case(s) in split {split.value}, expected {expected}"
        )
    return tuple(
        SweepCase(
            case_id=record.item_id,
            question=record.question,
            document_id=record.document_id,
            expected_refusal=record.expected_refusal,
            gold_clause_ids=tuple(record.gold_clause_ids),
        )
        for record in selected
    )
