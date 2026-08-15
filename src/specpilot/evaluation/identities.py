"""The twelve identity hashes a frozen evaluation run spec is made of.

The spec's whole content is a promise that the run it describes can be
reproduced. These hashes are that promise, so each has to bind bytes a later
reader can obtain and rehash for themselves.

The failure this file is shaped against is a digest that is well formed, stable,
and describes nothing. A hash of an absent file, of an empty list, or of the
interpreter that happened to run the command all look exactly like sound ones,
and the freeze gate cannot tell the difference: it stores what it is handed and
never recomputes any of them. Nothing downstream ever will either. So an
unavailable input refuses here rather than hashing whatever was at hand, and the
Python version is supplied rather than read from `sys` — a reader needs the
version the author intends, not the one that computed the file.

What each hash binds, and why that is the reproducible choice:

``source``       the frozen source manifest ids, which are content hashes of the
                 captured renditions — not the file paths, which move.
``corpus``       the corpus manifest's ``derived_corpus_sha256``: the clause
                 units as indexed, after exclusions and text policy.
``collection``   the manifest's ``inventory_root_sha256``, which covers the
                 vector inventory rather than the collection's name.
``sets``         the evaluation sets as stored — every annotation record and
                 every adversarial group, by content.
``scripts``      the source files that execute a run.
``prompts``      the source files that render what a model is shown. Separate
                 from ``scripts`` because a prompt edit changes results while
                 leaving orchestration identical.
``config``       the packaged configuration and the project definition.
``policy``       ``EgressPolicy.policy_hash()``, which already covers the caps
                 and their field names.
``provider``     the transport that is the only path outward.
``models``       the model identifiers the author names for the run.
``scoring``      the judge's prompt and scoring code. The author's *choice* of
                 route lives in the dev-scoring status; this binds what that
                 route would execute.
``environment``  the dependency lock plus the intended Python version.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


class IdentityUnavailableError(ValueError):
    """An input could not be bound. Never substituted with a digest of nothing."""


@dataclass(frozen=True, slots=True)
class IdentityInputs:
    repository: Path
    dependency_lock: Path
    corpus_manifest_id: str
    derived_corpus_sha256: str
    collection_inventory_sha256: str
    source_manifest_ids: tuple[str, ...]
    group_dir: Path
    annotation_dir: Path
    model_ids: tuple[str, ...]
    python_version: str
    extra_scoring_paths: tuple[Path, ...] = field(default_factory=tuple)


_SCRIPT_PATHS = (
    "src/specpilot/evaluation",
    "src/specpilot/agents",
    "src/specpilot/runs",
)
_PROMPT_PATHS = (
    "src/specpilot/answer/reply.py",
    "src/specpilot/contracts/answer.py",
    "src/specpilot/judge/prompt.py",
)
_CONFIG_PATHS = (
    "pyproject.toml",
    "src/specpilot/egress/policies",
)
_PROVIDER_PATHS = ("src/specpilot/providers",)
_SCORING_PATHS = ("src/specpilot/judge",)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_of_values(values: Iterable[str]) -> str:
    """Order-independent, and empty input is a refusal rather than a digest."""
    materialised = sorted(values)
    if not materialised:
        raise IdentityUnavailableError("nothing to bind")
    return _sha256("\n".join(materialised).encode("utf-8"))


def _digest_of_tree(root: Path, relatives: Iterable[str]) -> str:
    """Hash named files and directories by path and content, in path order.

    Each entry contributes its repository-relative path as well as its bytes, so
    moving a file changes the identity — a rename that leaves content untouched
    still changes which module runs.
    """
    entries: list[str] = []
    for relative in sorted(relatives):
        target = root / relative
        if not target.exists():
            raise IdentityUnavailableError(f"missing identity input: {relative}")
        if target.is_dir():
            files = sorted(
                path
                for path in target.rglob("*.py")
                if "__pycache__" not in path.parts
            )
            files += sorted(target.rglob("*.json"))
            if not files:
                raise IdentityUnavailableError(f"no files under {relative}")
        else:
            files = [target]
        for path in sorted(set(files)):
            entries.append(
                f"{path.relative_to(root)}:{_sha256(path.read_bytes())}"
            )
    return _digest_of_values(entries)


def _digest_of_store(directory: Path) -> str:
    if not directory.is_dir():
        raise IdentityUnavailableError(f"missing store: {directory.name}")
    records = sorted(directory.glob("*.json"))
    if not records:
        raise IdentityUnavailableError(f"empty store: {directory.name}")
    return _digest_of_values(
        f"{path.name}:{_sha256(path.read_bytes())}" for path in records
    )


def _policy_digest() -> str:
    from specpilot.egress.policy import EgressPolicy

    # A property, not a method. Calling it returns the str's __call__ and fails
    # loudly here; a double that got this backwards is one of the defects
    # AGENTS.md records, so the real object is used rather than a stand-in.
    return EgressPolicy.load().policy_hash


def build_identity_status(inputs: IdentityInputs) -> dict[str, str]:
    """Compute all twelve, refusing on any input that cannot be bound."""
    if not inputs.model_ids:
        raise IdentityUnavailableError("no model identifier was named")
    if not inputs.source_manifest_ids:
        raise IdentityUnavailableError("no source manifest was named")
    if not inputs.python_version.strip():
        raise IdentityUnavailableError("no Python version was named")
    try:
        lock_bytes = inputs.dependency_lock.read_bytes()
    except OSError as error:
        raise IdentityUnavailableError("dependency lock unavailable") from error
    if not lock_bytes.strip():
        raise IdentityUnavailableError("dependency lock is empty")

    root = inputs.repository
    return {
        "source_sha256": _digest_of_values(inputs.source_manifest_ids),
        "corpus_sha256": _digest_of_values(
            (inputs.corpus_manifest_id, inputs.derived_corpus_sha256)
        ),
        "collection_sha256": _digest_of_values(
            (inputs.collection_inventory_sha256,)
        ),
        "sets_sha256": _digest_of_values(
            (
                _digest_of_store(inputs.annotation_dir),
                _digest_of_store(inputs.group_dir),
            )
        ),
        "scripts_sha256": _digest_of_tree(root, _SCRIPT_PATHS),
        "prompts_sha256": _digest_of_tree(root, _PROMPT_PATHS),
        "config_sha256": _digest_of_tree(root, _CONFIG_PATHS),
        "policy_sha256": _policy_digest(),
        "provider_sha256": _digest_of_tree(root, _PROVIDER_PATHS),
        "models_sha256": _digest_of_values(inputs.model_ids),
        "scoring_sha256": _digest_of_tree(
            root,
            [*_SCORING_PATHS, *(str(p) for p in inputs.extra_scoring_paths)],
        ),
        "environment_sha256": _digest_of_values(
            (_sha256(lock_bytes), f"python:{inputs.python_version.strip()}")
        ),
    }
