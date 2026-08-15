"""The pinned runtime closure the frozen run spec binds.

This file is committed, because the freeze refuses a dirty tree and therefore
reads the lock from a commit rather than regenerating it. Whatever it names is
what a later reader installs, so it names the runtime closure and nothing else.
Dev tooling and the optional tensor runtime are absent from the packaged image;
listing them would describe an environment the run never had.

An uninstalled requirement refuses rather than being dropped. A lock that is
quietly one line short still hashes cleanly and still looks like a complete
description of the environment — and the run it describes is not the run that
happened.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

_HEADER = (
    "# SpecPilot runtime dependency lock.\n"
    "#\n"
    "# The runtime closure only: no dev tooling, no optional extras. The\n"
    "# packaged image installs exactly this set, so the evaluation run spec's\n"
    "# environment identity is computed over these bytes.\n"
    "#\n"
    "# Regenerate with:\n"
    "#   python -m specpilot.cli evaluation dependency-lock --out requirements.lock\n"
    "# Committing the result is part of the freeze: a dirty tree is refused.\n"
)


class DependencyLockError(ValueError):
    """A requirement could not be pinned. Never silently omitted."""


def render_dependency_lock(
    requirements: Iterable[str],
    installed_version: Callable[[str], str],
) -> str:
    """Render `name==version` for every requirement, sorted, with a header.

    Sorted so the bytes — and so the environment identity — do not depend on the
    order the requirements were discovered in.
    """
    pins: list[str] = []
    missing: list[str] = []
    for name in sorted(set(requirements)):
        try:
            version = installed_version(name)
        except (KeyError, LookupError):
            missing.append(name)
            continue
        if not version:
            missing.append(name)
            continue
        pins.append(f"{name}=={version}")
    if missing:
        raise DependencyLockError(
            "requirements are not installed and cannot be pinned: "
            + ", ".join(sorted(missing))
        )
    if not pins:
        raise DependencyLockError("no requirements to pin")
    return _HEADER + "\n".join(sorted(pins)) + "\n"
