# Gold Provenance v2 Design

## Goal

Replace the independent-Gold admission gate with a small provenance audit that
admits model and retrieval origins without weakening source correctness.

This design supersedes
`docs/superpowers/specs/2026-08-08-independent-l2-annotation-design.md`.

## Lean schema

New records use `annotation-l1/v2` or `annotation-l2/v2`. Remove
`IndependentPath` and `independent_path`. Add three fields:

- `content_origin`: whether the question and key points are `human`, `model`,
  or `mixed`;
- `label_origin`: whether task labels and the L2 Verifier pair are `human`,
  `model`, or `mixed`;
- `gold_origins`: an ordered tuple of Gold-origin events.

Each Gold-origin event has `origin` and optional `producer`. Origins are:

- `source_text_navigation`;
- `literal_search`;
- `cross_reference_trace`;
- `terminology_index`;
- `human_source_review`;
- `model_proposal`;
- `search_clauses`;
- `dense_retrieval`;
- `bm25_retrieval`;
- `hybrid_retrieval`.

Model and retrieval origins require a producer identifier. Human and source
origins carry no producer. Event order is preserved and repeated events remain
representable; this avoids a second event identity or scope system.

An answerable record with Gold must have at least one Gold-origin event. An L1
unanswerable record has no Gold IDs, paths, overlap, or Gold-origin events. L2
`insufficient_evidence` remains an answerable verdict and therefore retains its
Gold and Gold-origin chain.

Origin fields are audit data only. No allowed origin changes whether the record
may be admitted.

## Compatibility and storage

The formal annotation store is empty, so there is no migration command or
dual-write period. v1 and unknown schemas refuse with the stable code
`unsupported_annotation_schema`. The store and CLI share one explicit v2
schema dispatcher so unknown records can never fall back to L1.

Content addressing includes the v2 schema and origin fields. A pooling
successor contains the full predecessor Gold-origin tuple followed by the new
origin events. Adding Gold requires at least one appended Gold-origin event.
Existing adjudication and add-only Gold behavior remains.

## Preserved gates

This change does not relax:

- frozen-source hash and XML safety checks;
- document identity and publication-version matching;
- Gold clause existence;
- answerability and Jaccard requirements;
- key-point clause-restatement and verdict-label checks;
- content-addressed private storage and item lineage;
- add-only, adjudicated pooling.

No automatic Gold-proposal command or persistent retrieval index is added.

## Progress reporting

Remove `independent_paths`. Each L1/L2 progress payload adds:

```json
{
  "provenance": {
    "content_origins": {},
    "label_origins": {},
    "gold_origins": {},
    "gold_origin_chains": {},
    "retrieval_originated_gold_items": 0
  },
  "verdict_counts": {}
}
```

The three origin maps count item-level content/label summaries and Gold events.
Gold chains preserve event order and include `@producer` when present.
`retrieval_originated_gold_items` counts items whose Gold chain contains
`search_clauses`, dense, BM25, or hybrid retrieval. `verdict_counts` is empty
for L1 and reports all three expected verdicts for L2.

Retrieval-originated Gold is allowed, but Recall on those items is diagnostic,
not an unbiased headline metric. `model` and `mixed` label counts disclose
same-model evaluation risk.

## Current RFC 9112 candidates

The three user-authored scenarios use RFC 9112 §6.3 paragraph
`section-6.3-2.3`, clause
`817e50534fc9d2e00b485d0d445b95992b1fdc25ef354febcd87bfc1be60e7bb`,
and task labels `violating`, `compliant`, and `insufficient_evidence`.

Because the English wording and key points are model-assisted, each candidate
uses `content_origin: mixed`. Because the model proposed labels and Verifier
pairs before human confirmation, each uses `label_origin: mixed`. Its ordered
Gold chain is:

```json
[
  {"origin": "model_proposal", "producer": "openai-codex"},
  {"origin": "human_source_review"}
]
```

Candidates are overlap-measured and source-aware validated in a temporary
annotation directory. They enter `artifacts/restricted/annotations` only after
the user reviews the complete records and explicitly approves formal entry.

## Documentation and verification

The live runbook, master roadmap, and product-plan Gold section change from an
independence gate to provenance and bias disclosure. Historical plans receive
only a supersession note. The untracked user-owned `SpecPilot_项目方案.md` is not
added to Git without separate authorization.

Tests cover the lean field rules, producer requirements, explicit v2 dispatch,
pooling append behavior, CLI templates/entry, provenance progress, verdict
counts, and preservation of source-aware failures. Verification runs focused
tests, Ruff, mypy, the full suite, real overlap measurement, and temporary
source-aware candidate entry. No official annotation or persistent index is
created before the final approval gate.

