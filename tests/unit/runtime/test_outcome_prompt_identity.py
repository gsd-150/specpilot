"""Recording which prompt a run actually used.

Which prompt produced a result had to be inferred from a commit timestamp and a
file mtime, because nothing in the artifact said. That worked only because the
edit happened to land twenty minutes before the run; an edit made minutes
before, or made and not committed, leaves the question unanswerable — and the
dev evidence a freeze rests on then cannot say which prompt produced it.

The hashes are taken over the same module constants the wire renders, never a
copy. A second copy is the defect class AGENTS.md records: a value present in
the code and absent from the bytes that left, indistinguishable afterwards.
"""

from __future__ import annotations

import hashlib

from specpilot.providers.http import COMPLIANCE_REPLY_INSTRUCTIONS
from specpilot.runtime.outcome_capture import (
    build_prompt_identity,
    validate_l2_outcome,
)


def test_the_identity_names_both_l2_stages() -> None:
    identity = build_prompt_identity()

    assert set(identity) == {"compliance_prompt_sha256", "verifier_prompt_sha256"}
    assert all(len(value) == 64 for value in identity.values())


def test_the_hash_is_of_the_instruction_the_wire_renders() -> None:
    """Asserted against the imported constant, so a copy cannot drift from it."""
    identity = build_prompt_identity()

    assert identity["compliance_prompt_sha256"] == hashlib.sha256(
        COMPLIANCE_REPLY_INSTRUCTIONS.encode("utf-8")
    ).hexdigest()


def test_the_two_stages_are_not_the_same_prompt() -> None:
    """§8.1's separation is the point: one prompt for both would erase it."""
    identity = build_prompt_identity()

    assert (
        identity["compliance_prompt_sha256"] != identity["verifier_prompt_sha256"]
    )


def test_an_outcome_carrying_the_identity_validates() -> None:
    payload = {
        "schema_version": "l2-outcome/v1",
        "case_id": "l2-dev-001",
        "design_description": "a scenario",
        "candidates": [],
        "results": [],
        "provider_error": None,
        "parse_fault": None,
        **build_prompt_identity(),
    }

    assert validate_l2_outcome(payload) is payload


def test_a_malformed_prompt_hash_is_refused() -> None:
    """A field nobody validates is a field that can hold anything.

    An identity that is present and wrong is worse than one that is absent: the
    absent one prompts the question this exists to answer.
    """
    import pytest

    payload = {
        "schema_version": "l2-outcome/v1",
        "case_id": "l2-dev-001",
        "design_description": "a scenario",
        "candidates": [],
        "results": [],
        "provider_error": None,
        "parse_fault": None,
        "compliance_prompt_sha256": "not-a-digest",
        "verifier_prompt_sha256": "f" * 64,
    }

    with pytest.raises(ValueError, match="prompt"):
        validate_l2_outcome(payload)
