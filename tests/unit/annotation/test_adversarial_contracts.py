from __future__ import annotations

import pytest
from pydantic import ValidationError

from specpilot.contracts.annotation import Split, Verdict
from specpilot.contracts.l2_adv import AdversarialDimension, AdversarialGroup

_NEGATIVE = "a proxy must reject the request when the field is absent"
_POSITIVE = "an origin server must reject the request when the field is absent"


def _group(**overrides: object) -> AdversarialGroup:
    fields: dict[str, object] = {
        "group_id": "adv-dev-001",
        "family": "content-length-received",
        "split": Split.DEV,
        "dimension": AdversarialDimension.ROLE_ATTRIBUTION,
        "negative_claim_id": "adv-dev-001-neg",
        "negative_claim": _NEGATIVE,
        "distractor_clause_ids": ("a" * 64,),
        "positive_claim_id": "adv-dev-001-pos",
        "positive_claim": _POSITIVE,
        "supporting_clause_ids": ("b" * 64,),
        "proposed_verdict": Verdict.VIOLATING,
    }
    fields.update(overrides)
    return AdversarialGroup(**fields)  # type: ignore[arg-type]


def test_a_group_records_the_distractor_dimension_it_was_built_on() -> None:
    group = _group()

    assert group.dimension is AdversarialDimension.ROLE_ATTRIBUTION
    assert group.schema_version == "l2-adv-group/v1"


def test_the_five_rfc_distractor_dimensions_are_the_only_ones_offered() -> None:
    assert {dimension.value for dimension in AdversarialDimension} == {
        "request_vs_response",
        "role_attribution",
        "document_attribution",
        "normative_strength",
        "received_vs_generated",
    }


def test_the_negative_gold_verdict_cannot_be_written_as_anything_determinate() -> None:
    assert _group().negative_expected_verdict is Verdict.INSUFFICIENT_EVIDENCE

    with pytest.raises(ValidationError):
        _group(negative_expected_verdict=Verdict.VIOLATING)


def test_a_pair_built_from_one_claim_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="different"):
        _group(positive_claim=_NEGATIVE)

    with pytest.raises(ValidationError, match="different"):
        _group(positive_claim_id="adv-dev-001-neg")


def test_a_group_requires_evidence_on_both_sides() -> None:
    with pytest.raises(ValidationError):
        _group(distractor_clause_ids=())

    with pytest.raises(ValidationError):
        _group(supporting_clause_ids=())


def test_a_distractor_may_also_support_the_rewritten_positive_claim() -> None:
    """The natural construction, not an error.

    A minimally rewritten positive claim is often supported by the very clause
    that was the distractor for the negative one — rewriting the claim to match
    the distractor's applicability is how §8.1.1 isolates the semantic step.
    Forbidding the overlap would outlaw the subset's most direct construction.
    """
    shared = "c" * 64
    group = _group(distractor_clause_ids=(shared,), supporting_clause_ids=(shared,))

    assert group.distractor_clause_ids == group.supporting_clause_ids


def test_source_prose_does_not_fit_in_a_claim() -> None:
    with pytest.raises(ValidationError):
        _group(negative_claim="x" * 1_025)
