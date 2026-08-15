"""The lock is a committed file, so what goes in it is a durable decision.

The freeze refuses a dirty tree, which means this file is read from a commit
rather than regenerated at freeze time. Whatever it names is what a later reader
will install, so it has to be the runtime closure and nothing else: dev tooling
and the optional tensor runtime are absent from the packaged image, and naming
them would describe an environment the run never had.
"""

from __future__ import annotations

import pytest

from specpilot.evaluation.dependency_lock import (
    DependencyLockError,
    render_dependency_lock,
)

_INSTALLED = {
    "fastapi": "0.141.1",
    "pydantic": "2.13.4",
    "httpx": "0.28.1",
    "defusedxml": "0.7.1",
    "mcp": "1.29.0",
    "psycopg": "3.3.4",
    "qdrant-client": "1.12.2",
    "anyio": "4.14.2",
    "pytest": "8.3.3",
    "torch": "2.4.0",
}
_RUNTIME = ("fastapi", "pydantic", "httpx", "defusedxml", "mcp", "psycopg",
            "qdrant-client", "anyio")


def test_the_lock_pins_every_runtime_requirement_exactly() -> None:
    rendered = render_dependency_lock(_RUNTIME, _INSTALLED.__getitem__)

    for name in _RUNTIME:
        assert f"{name}=={_INSTALLED[name]}" in rendered


def test_dev_and_optional_extras_are_absent() -> None:
    """They are not in the packaged image, so they are not in its identity."""
    rendered = render_dependency_lock(_RUNTIME, _INSTALLED.__getitem__)

    assert "pytest==" not in rendered
    assert "torch==" not in rendered


def test_the_lock_is_sorted_so_the_hash_does_not_depend_on_walk_order() -> None:
    forward = render_dependency_lock(_RUNTIME, _INSTALLED.__getitem__)
    reversed_input = render_dependency_lock(
        tuple(reversed(_RUNTIME)), _INSTALLED.__getitem__
    )

    assert forward == reversed_input
    pins = [line for line in forward.splitlines() if "==" in line]
    assert pins == sorted(pins)


def test_an_uninstalled_requirement_refuses_rather_than_being_skipped() -> None:
    """A silently short lock installs a different environment than it claims."""

    def lookup(name: str) -> str:
        if name == "mcp":
            raise KeyError(name)
        return _INSTALLED[name]

    with pytest.raises(DependencyLockError, match="mcp"):
        render_dependency_lock(_RUNTIME, lookup)


def test_the_lock_says_what_it_is_and_how_to_regenerate_it() -> None:
    rendered = render_dependency_lock(_RUNTIME, _INSTALLED.__getitem__)

    assert rendered.startswith("#")
    assert "evaluation dependency-lock" in rendered
