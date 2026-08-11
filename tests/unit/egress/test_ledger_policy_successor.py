from __future__ import annotations

import pytest
from pydantic import ValidationError

from specpilot.contracts.egress import CorpusDocumentUsage, CorpusUsage
from specpilot.egress.ledger import (
    PolicyRebindAmbiguous,
    PolicyRebindConflict,
    PolicyRebindResult,
    successor_corpus_usage,
)


def test_successor_changes_only_the_policy_binding() -> None:
    old = CorpusUsage(
        corpus_manifest_id="a" * 64,
        policy_hash="b" * 64,
        disclosure_ids=("c" * 64, "d" * 64),
        unique_tokens=13,
        unique_bytes=89,
        document_usage=(
            CorpusDocumentUsage(
                document_id="rfc-9110",
                disclosure_ids=("c" * 64,),
                unique_tokens=5,
                unique_bytes=34,
            ),
        ),
    )

    successor = successor_corpus_usage(old, "e" * 64)

    assert successor == old.model_copy(update={"policy_hash": "e" * 64})
    assert old.policy_hash == "b" * 64
    assert successor.document_usage == old.document_usage


def test_successor_rejects_the_same_policy_hash() -> None:
    old = CorpusUsage(corpus_manifest_id="a" * 64, policy_hash="b" * 64)

    with pytest.raises(ValueError, match="different policy hash"):
        successor_corpus_usage(old, old.policy_hash)


def test_successor_rejects_an_invalid_new_policy_hash() -> None:
    old = CorpusUsage(corpus_manifest_id="a" * 64, policy_hash="b" * 64)

    with pytest.raises(ValidationError):
        successor_corpus_usage(old, "invalid")


@pytest.mark.parametrize(
    "field_name",
    [
        "inherited_unique_excerpts",
        "inherited_unique_tokens",
        "inherited_unique_bytes",
    ],
)
def test_policy_rebind_result_rejects_negative_inherited_total(
    field_name: str,
) -> None:
    values = {
        "corpus_manifest_id": "a" * 64,
        "predecessor_ledger_id": "predecessor",
        "successor_ledger_id": "successor",
        "old_policy_hash": "b" * 64,
        "new_policy_hash": "c" * 64,
        "inherited_unique_excerpts": 1,
        "inherited_unique_tokens": 2,
        "inherited_unique_bytes": 3,
    }
    values[field_name] = -1

    with pytest.raises(ValidationError):
        PolicyRebindResult.model_validate(values)


def test_policy_rebind_errors_expose_stable_codes() -> None:
    assert PolicyRebindConflict().code == "corpus_policy_rebind_conflict"
    assert PolicyRebindAmbiguous().code == "policy_rebind_ambiguous"
