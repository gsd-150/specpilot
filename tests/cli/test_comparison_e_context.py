"""The A' stratification command refuses everything that must not run.

The heavy path -- corpus, weights, encoder, Qdrant -- is exercised live, the
same way 'retrieval evaluate' always has been. What a CLI test can pin is the
two refusals that must fire before any of it: locked is off-limits until W6
executes it, and a miscounted selection refuses instead of reporting a short
set. Both are the same discipline the sweep driver applies.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from specpilot.cli import EXIT_REFUSED, EXIT_USAGE, _comparison_e_context, _parser


def test_locked_split_is_refused_before_any_io() -> None:
    arguments = Namespace(split="locked")

    assert _comparison_e_context(arguments) == EXIT_USAGE


def test_the_parser_requires_split_and_expected() -> None:
    parser = _parser()

    def find_subparser(parent: object, name: str):
        for action in getattr(parent, "_actions", ()):  # noqa: SLF001
            choices = getattr(action, "choices", None)
            if choices and name in choices:
                return choices[name]
        raise AssertionError(f"subcommand {name} not registered")

    comparison = find_subparser(parser, "comparison")
    e_context = find_subparser(comparison, "e-context")
    required = {action.dest: action.required for action in e_context._actions}  # noqa: SLF001
    assert required["split"] is True
    assert required["expected"] is True
    assert required["annotation_dir"] is True
    assert required["corpus_manifest"] is True


def test_a_missing_manifest_refuses_without_touching_retrieval() -> None:
    arguments = Namespace(
        split="dev",
        expected=12,
        corpus_manifest="a" * 64,
        corpus_manifest_dir=Path("/nonexistent"),
        annotation_dir=Path("/nonexistent"),
        manifest_dir=Path("/nonexistent"),
        manifest=[],
        xml=[],
        model_dir=Path("/nonexistent"),
        device="cpu",
        qdrant_url="http://127.0.0.1:1",
    )

    assert _comparison_e_context(arguments) == EXIT_REFUSED
