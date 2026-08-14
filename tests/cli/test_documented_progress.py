"""Regression coverage for the dated W5 starting-state documentation."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CURRENT_STATE_HEADING = "## Current state — 2026-08-15"
CURRENT_DOCUMENTS = (
    ROOT / "docs/roadmaps/2026-08-06-specpilot-master-roadmap.md",
    ROOT / "docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md",
    ROOT / "SpecPilot_项目方案.md",
    ROOT / "docs/handoff/2026-08-15-codex-handoff.md",
)


REQUIRED_CURRENT_FACTS = (
    "L1 40/40",
    "L2 20/20",
    "deep review 12/12",
    "fixture-only",
    "1537 unit, 187 CLI",
    "1998 passed, 0 skipped",
    "b89339d",
    "does not end the pass",
)


def _current_state_block(document: Path) -> str:
    content = document.read_text(encoding="utf-8")
    assert content.count(CURRENT_STATE_HEADING) == 1, (
        f"{document.relative_to(ROOT)} must contain exactly one "
        "dated current-state block"
    )
    _, _, after_heading = content.partition(CURRENT_STATE_HEADING)
    next_heading = re.search(r"^#{1,6} ", after_heading, flags=re.MULTILINE)
    block = after_heading[: next_heading.start()] if next_heading else after_heading
    return " ".join(line.removeprefix("> ").strip() for line in block.splitlines())


def _assert_current_state_contract(block: str) -> None:
    for fact in REQUIRED_CURRENT_FACTS:
        assert fact in block, f"current-state block is missing {fact!r}"

    assert not re.search(
        r"\bfixture(?:-only)?\b[^.!?]*(?:\b(?:is|are|proves|establishes|demonstrates)\b)"
        r"(?!\s+(?:not|neither)\b)[^.!?]*\b(?:quality|live-provider)\b",
        block,
        flags=re.IGNORECASE,
    ), "fixture evidence must not be described as quality or live-provider evidence"
    assert not re.search(
        r"fixture-only[^。]*(?<!不)代表[^。]*(?:真实 provider|质量)",
        block,
    ), "fixture evidence must not be described as quality or live-provider evidence"
    assert not re.search(
        r"\bmistyped(?: pooling)? choice\b[^.!?]*(?<!does not )\bend(?:s)?\b",
        block,
        flags=re.IGNORECASE,
    ), "a mistyped pooling choice must not end the active pass"


def test_current_w5_documents_agree_on_completed_annotation_and_fixture_scope() -> None:
    """A stale progress snapshot must not become the active W5 starting point."""

    for document in CURRENT_DOCUMENTS:
        _assert_current_state_contract(_current_state_block(document))


@pytest.mark.parametrize(
    "contradiction",
    (
        "Fixture-only evidence is a quality metric.",
        "Fixture-only evidence proves live-provider acceptance.",
        "A mistyped pooling choice ends the pass.",
    ),
)
def test_current_state_contract_rejects_active_contradictions(
    contradiction: str,
) -> None:
    """The negative constraints must reject a contradictory current block."""
    complete_current_block = " ".join((*REQUIRED_CURRENT_FACTS, contradiction))

    with pytest.raises(AssertionError):
        _assert_current_state_contract(complete_current_block)
