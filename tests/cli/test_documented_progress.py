"""Regression coverage for the dated W5 starting-state documentation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CURRENT_DOCUMENTS = (
    ROOT / "docs/roadmaps/2026-08-06-specpilot-master-roadmap.md",
    ROOT / "docs/superpowers/plans/2026-08-09-assisted-annotation-and-review.md",
    ROOT / "SpecPilot_项目方案.md",
    ROOT / "docs/handoff/2026-08-15-codex-handoff.md",
)


def test_current_w5_documents_agree_on_completed_annotation_and_fixture_scope() -> None:
    """A stale progress snapshot must not become the active W5 starting point."""
    required_current_facts = (
        "Current state — 2026-08-15",
        "L1 40/40",
        "L2 20/20",
        "deep review 12/12",
        "fixture-only",
        "1537 unit, 187 CLI",
        "1998 passed, 0 skipped",
        "b89339d",
        "does not end the pass",
    )

    for document in CURRENT_DOCUMENTS:
        content = " ".join(
            line.removeprefix("> ").strip()
            for line in document.read_text(encoding="utf-8").splitlines()
        )
        for fact in required_current_facts:
            assert fact in content, f"{document.relative_to(ROOT)} is missing {fact!r}"
