"""What the twelve identity hashes are allowed to be computed from.

A frozen run spec is a promise that the run it describes can be reproduced. The
identity hashes are that promise's whole content, so each one has to bind bytes
that a later reader can obtain and rehash. A digest taken over ambient state —
an installed version, a wall clock, a working directory — reads exactly like a
sound one and reproduces nothing, and the freeze gate cannot tell them apart
because it never recomputes any of them.

So the rule enforced here is: every hash is a function of committed bytes,
recorded manifests, or values the author supplies explicitly. Nothing is read
from the environment the command happens to run in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specpilot.evaluation.identities import (
    IdentityInputs,
    IdentityUnavailableError,
    build_identity_status,
)

_ROOT = Path(__file__).resolve().parents[3]
_FIELDS = (
    "source_sha256",
    "corpus_sha256",
    "collection_sha256",
    "sets_sha256",
    "scripts_sha256",
    "prompts_sha256",
    "config_sha256",
    "policy_sha256",
    "provider_sha256",
    "models_sha256",
    "scoring_sha256",
    "environment_sha256",
)


def _inputs(tmp_path: Path, **overrides: object) -> IdentityInputs:
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2.13.4\n", encoding="utf-8")
    fields: dict[str, object] = {
        "repository": _ROOT,
        "dependency_lock": lock,
        "corpus_manifest_id": "a" * 64,
        "derived_corpus_sha256": "b" * 64,
        "collection_inventory_sha256": "c" * 64,
        "source_manifest_ids": ("d" * 64, "e" * 64),
        "group_dir": _ROOT / "artifacts/restricted/l2-adv",
        "annotation_dir": _ROOT / "artifacts/restricted/annotations",
        "model_ids": ("claude-opus-5",),
        "python_version": "3.12.11",
    }
    fields.update(overrides)
    return IdentityInputs(**fields)  # type: ignore[arg-type]


def test_all_twelve_fields_are_produced(tmp_path: Path) -> None:
    status = build_identity_status(_inputs(tmp_path))

    assert set(status) == set(_FIELDS)
    assert all(len(status[field]) == 64 for field in _FIELDS)


def test_the_status_is_json_serialisable_and_carries_no_prose(
    tmp_path: Path,
) -> None:
    status = build_identity_status(_inputs(tmp_path))
    rendered = json.dumps(status)

    assert set(json.loads(rendered)) == set(_FIELDS)
    for forbidden in ("question", "claim", "excerpt", "answer", "rationale"):
        assert forbidden not in rendered


def test_the_same_inputs_produce_the_same_hashes(tmp_path: Path) -> None:
    """Reproducibility is the property, so it is asserted rather than assumed."""
    first = build_identity_status(_inputs(tmp_path))
    second = build_identity_status(_inputs(tmp_path))

    assert first == second


def test_a_changed_dependency_lock_changes_the_environment_hash(
    tmp_path: Path,
) -> None:
    before = build_identity_status(_inputs(tmp_path))
    other = tmp_path / "other.lock"
    other.write_text("pydantic==2.13.5\n", encoding="utf-8")

    after = build_identity_status(_inputs(tmp_path, dependency_lock=other))

    assert after["environment_sha256"] != before["environment_sha256"]
    assert after["policy_sha256"] == before["policy_sha256"]


def test_a_changed_corpus_binding_changes_only_what_it_should(
    tmp_path: Path,
) -> None:
    before = build_identity_status(_inputs(tmp_path))
    after = build_identity_status(_inputs(tmp_path, derived_corpus_sha256="f" * 64))

    assert after["corpus_sha256"] != before["corpus_sha256"]
    assert after["collection_sha256"] == before["collection_sha256"]
    assert after["scripts_sha256"] == before["scripts_sha256"]


def test_a_missing_dependency_lock_refuses_rather_than_hashing_nothing(
    tmp_path: Path,
) -> None:
    """An absent input must not become a hash of the empty string.

    That digest is well formed, stable, and describes nothing — the exact shape
    of an identity that looks sound and reproduces nothing.
    """
    with pytest.raises(IdentityUnavailableError):
        build_identity_status(
            _inputs(tmp_path, dependency_lock=tmp_path / "absent.lock")
        )


def test_an_empty_model_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IdentityUnavailableError):
        build_identity_status(_inputs(tmp_path, model_ids=()))


def test_the_python_version_must_be_supplied_not_discovered(tmp_path: Path) -> None:
    """`sys.version` would bind the machine that ran the command.

    A reader reproducing the run needs the version the author intends, not the
    interpreter that happened to compute the identity file.
    """
    import inspect

    parameters = inspect.signature(build_identity_status).parameters
    assert set(parameters) == {"inputs"}
    assert "python_version" in IdentityInputs.__dataclass_fields__
