"""The per-document corpus cap, and the measurement it is derived from.

Why this scope exists at all: the corpus-wide cap answers "how much source text
has left the machine in total", but the licence condition it is meant to respect
is written per document. TLP section 3.c.iii(y) attaches an extra attribution
obligation once a reproduction exceeds one fifth of *an* RFC, so a cap that
pools two documents cannot show the obligation never arises for either.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from specpilot.contracts.egress import (
    CorpusDocumentUsage,
    CorpusUsage,
    EgressRequest,
    ReservationOutcome,
    TaskLevel,
    VersionMetadata,
)
from specpilot.contracts.manifests import SourceManifest, SourceManifestDraft
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.enforcer import apply_reservation as apply_with_trusted_inputs
from specpilot.egress.policy import EgressPolicy
from tests.unit.egress.test_disclosure_caps import distinct_excerpt, sized_quote
from tests.unit.egress.test_policy_projection import (
    CORPUS_MANIFEST_ID,
    FIXTURE_DOCUMENT,
    NOW,
    OTHER_FIXTURE_DOCUMENT,
    FixtureTokenCounter,
    fixture_policy,
    fixture_store,
    l1_payload,
    online_route,
)
from tests.unit.manifests.test_source_manifest import assessment, initial_fields

# Measured on the frozen corpus at the current exclusion set, summing BGE-M3
# tokens and UTF-8 bytes over `iter_clause_texts(...)`. The denominator is the
# indexable clause text rather than the whole published file, which makes every
# derived cap strictly more conservative than measuring the document as
# distributed.
MEASURED_CORPUS = {
    "ietf-rfc-9110": {"units": 1559, "tokens": 87548, "bytes": 375367},
    "ietf-rfc-9112": {"units": 350, "tokens": 19531, "bytes": 81671},
}

GENEROUS = {"excerpts": 1024, "tokens": 524288, "bytes": 8388608}


def exact_policy(caps: dict[str, dict[str, int]]) -> EgressPolicy:
    """A policy whose per-document table is exactly ``caps`` and nothing else."""
    fields = EgressPolicy.load().model_dump(mode="json")
    fields["corpus_document_unique"] = caps
    return EgressPolicy.model_validate(fields)


def manifest_for(document_id: str) -> SourceManifest:
    store = fixture_store()
    fields = initial_fields()
    fields["document_id"] = document_id
    initial = store.create_source(SourceManifestDraft(**fields))
    binding = online_route()
    return store.create_successor(
        initial,
        assessment=assessment(
            provider_id=binding.provider_id,
            endpoint_purpose=binding.endpoint_purpose,
        ),
        route_binding=binding,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )


def prepare_from(
    document_id: str,
    case_id: str,
    *excerpts: object,
    policy: EgressPolicy | None = None,
):
    """One Evidence call naming one document, in its own evaluation root."""
    source = manifest_for(document_id)
    version = VersionMetadata(
        source_manifest_id=source.manifest_id,
        corpus_manifest_id=CORPUS_MANIFEST_ID,
        document_id=document_id,
        document_version="2026-edition",
    )
    request = EgressRequest(
        evaluation_root_id=case_id,
        run_id="run-1",
        task_level=TaskLevel.L1,
        version=version,
        stage="evidence",
        route=online_route(),
        model_id="fixture-model-v1",
        source_manifest=source,
        payload=l1_payload(evidence_excerpts=excerpts, version=version),
    )
    enforcer = EgressPolicyEnforcer(
        policy or fixture_policy(),
        manifests=fixture_store(),
        clock=lambda: NOW,
    )
    return enforcer.prepare(request, FixtureTokenCounter())


def apply_across_cases(
    corpus_usage: CorpusUsage | None,
    reservation,
    policy: EgressPolicy | None = None,
) -> ReservationOutcome:
    return apply_with_trusted_inputs(
        None,
        corpus_usage,
        reservation,
        policy or fixture_policy(),
        FixtureTokenCounter(),
        fixture_store(),
        clock=lambda: NOW,
    )


def account(usage: CorpusUsage, document_id: str) -> CorpusDocumentUsage | None:
    for item in usage.document_usage:
        if item.document_id == document_id:
            return item
    return None


def test_two_documents_in_one_corpus_keep_separate_accounts() -> None:
    first = apply_across_cases(
        None,
        prepare_from(FIXTURE_DOCUMENT, "case-1", distinct_excerpt(1)),
    )
    second = apply_across_cases(
        first.corpus_usage,
        prepare_from(OTHER_FIXTURE_DOCUMENT, "case-2", distinct_excerpt(2)),
    )

    assert second.corpus_usage.unique_tokens == 4, "the corpus total still pools both"
    assert account(second.corpus_usage, FIXTURE_DOCUMENT).unique_tokens == 2
    assert account(second.corpus_usage, OTHER_FIXTURE_DOCUMENT).unique_tokens == 2


def test_one_documents_budget_does_not_spend_anothers() -> None:
    """A small document must not be exhausted by traffic against a large one."""
    tiny = {"excerpts": 1, "tokens": 2, "bytes": 64}
    policy = fixture_policy(**{OTHER_FIXTURE_DOCUMENT: tiny})

    spent = prepare_from(
        FIXTURE_DOCUMENT,
        "case-1",
        distinct_excerpt(1),
        distinct_excerpt(2),
        policy=policy,
    )
    corpus = apply_across_cases(None, spent, policy).corpus_usage

    allowed = apply_across_cases(
        corpus,
        prepare_from(
            OTHER_FIXTURE_DOCUMENT, "case-2", distinct_excerpt(3), policy=policy
        ),
        policy,
    )
    assert account(allowed.corpus_usage, OTHER_FIXTURE_DOCUMENT).unique_tokens == 2

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_across_cases(
            allowed.corpus_usage,
            prepare_from(
                OTHER_FIXTURE_DOCUMENT, "case-3", distinct_excerpt(4), policy=policy
            ),
            policy,
        )

    assert caught.value.code == "corpus_document_unique_excerpts_exceeded"


def test_the_document_token_cap_is_enforced_independently_of_the_count() -> None:
    quote = sized_quote(tokens=512, byte_count=8192)
    capped = {"excerpts": 8, "tokens": 512, "bytes": 8388608}
    policy = fixture_policy(**{FIXTURE_DOCUMENT: capped})

    first = prepare_from(
        FIXTURE_DOCUMENT, "case-1", distinct_excerpt(1, quote), policy=policy
    )
    corpus = apply_across_cases(None, first, policy).corpus_usage

    with pytest.raises(EgressPolicyViolation) as caught:
        apply_across_cases(
            corpus,
            prepare_from(
                FIXTURE_DOCUMENT, "case-2", distinct_excerpt(2, quote), policy=policy
            ),
            policy,
        )

    assert caught.value.code == "corpus_document_unique_tokens_exceeded"


def test_a_document_the_policy_does_not_price_cannot_be_disclosed_at_all() -> None:
    """No measured denominator means no justified cap, so there is no cap to apply.

    Failing open here would let a document join the corpus and leave the machine
    under the pooled cap alone, which is the exact gap this scope closes.
    """
    policy = exact_policy({FIXTURE_DOCUMENT: GENEROUS})

    with pytest.raises(EgressPolicyViolation) as caught:
        prepare_from(
            OTHER_FIXTURE_DOCUMENT, "case-1", distinct_excerpt(1), policy=policy
        )

    assert caught.value.code == "corpus_document_cap_missing"


def test_a_missing_document_cap_is_refused_before_any_usage_is_written() -> None:
    policy = exact_policy({FIXTURE_DOCUMENT: GENEROUS})
    priced = prepare_from(
        FIXTURE_DOCUMENT, "case-1", distinct_excerpt(1), policy=policy
    )
    corpus = apply_across_cases(None, priced, policy).corpus_usage

    with pytest.raises(EgressPolicyViolation):
        prepare_from(
            OTHER_FIXTURE_DOCUMENT, "case-2", distinct_excerpt(2), policy=policy
        )

    assert account(corpus, OTHER_FIXTURE_DOCUMENT) is None
    assert corpus.unique_tokens == 2


@pytest.mark.parametrize("document_id", sorted(MEASURED_CORPUS))
def test_shipped_caps_stay_under_one_fifth_of_the_measured_document(
    document_id: str,
) -> None:
    """The guard on the number itself.

    The cap's justification is arithmetic, not judgement: below one fifth, TLP
    section 3.c.iii(y)'s additional obligation cannot be reached. Raising a cap
    without re-measuring would break that argument silently, so the argument is
    a test.
    """
    measured = MEASURED_CORPUS[document_id]
    cap = EgressPolicy.load().corpus_document_unique[document_id]

    assert cap.excerpts <= measured["units"] // 5
    assert cap.tokens <= measured["tokens"] // 5
    assert cap.bytes <= measured["bytes"] // 5


def test_every_frozen_document_is_priced_by_the_shipped_policy() -> None:
    priced = set(EgressPolicy.load().corpus_document_unique)

    assert set(MEASURED_CORPUS) <= priced, (
        "a frozen document with no cap entry cannot be disclosed at all"
    )
