# Gold Provenance v2 Design

## Goal

Replace the independent-Gold admission gate with explicit, ordered provenance
auditing. Model- and retrieval-originated Gold may be stored, while reports
retain enough information to disclose circularity and same-model evaluation
risk.

This design supersedes
`docs/superpowers/specs/2026-08-08-independent-l2-annotation-design.md`.

## Scope

The change covers annotation contracts, storage dispatch, CLI templates and
entry, progress reporting, current annotation documentation, tests, and the
three local RFC 9112 L2 candidates. It does not add an automatic Gold proposal
command, weaken frozen-source checks, create a persistent retrieval index, or
change the RFC single-snapshot security work.

Historical implementation plans and completed reports remain historical. A
short supersession note may be added where a current reader would otherwise be
directed to the old gate; historical task text is not rewritten.

## Schema v2

New records use `annotation-l1/v2` or `annotation-l2/v2`. The
`IndependentPath` enum and `independent_path` field are removed. Records gain
an ordered `provenance` tuple of events.

Each event contains:

- `scopes`: a non-empty, duplicate-free tuple of affected annotation scopes;
- `origin`: one enumerated origin kind;
- `producer`: an optional identifier for the model route, retrieval system,
  or index that produced the event.

The scope enum contains:

- `question` for the scenario or query;
- `gold` for clause selection;
- `task_label` for `expected_refusal` or `expected_verdict`;
- `verifier_pair` for L2 `proposed_verdict` and `supports_verdict`;
- `key_points` for the scoring rubric.

Separating `task_label` from `verifier_pair` preserves the existing contract's
distinction between task-level Gold and the Verifier support pair.

The origin enum contains:

- `human_authored`;
- `human_source_review`;
- `source_text_navigation`;
- `literal_search`;
- `cross_reference_trace`;
- `terminology_index`;
- `model_proposal`;
- `model_polish`;
- `search_clauses`;
- `dense_retrieval`;
- `bm25_retrieval`;
- `hybrid_retrieval`.

`model_proposal`, `model_polish`, and every retrieval origin require a
non-empty `producer`. Human and source-navigation origins carry no producer.
The record order is the event order; a sequence number is not duplicated in
the payload. Exact duplicate events are refused, while a repeated origin with
a different scope or producer remains representable.

Every record must cover `question` and `task_label`. A record with Gold clause
IDs must cover `gold`. A record with key points must cover `key_points`. Every
L2 record must cover `verifier_pair`. A record without Gold may still record a
`gold`-scoped search or review event to explain how absence was established.

Provenance is audit data only. No permitted origin changes whether the record
may be admitted.

## Compatibility and storage

The formal annotation store is empty, so v1 compatibility and an automated
migration command are unnecessary. CLI entry rejects v1 and unknown schemas
with the stable code `unsupported_annotation_schema`. Direct store loading
raises a stable unsupported-schema error rather than interpreting an unknown
record as L1.

The three ignored local L2 drafts are rewritten as v2 candidates. Content
addressing includes the v2 schema and provenance events, so every resulting
annotation ID is newly computed. There is no v1/v2 dual write.

## CLI behavior

`annotation template` emits v2 and an empty `provenance` list. Like the current
empty question and Gold fields, that skeleton is deliberately invalid until an
author fills it.

`annotation add` accepts all provenance origins. It continues to verify the
record's schema, frozen source, document identity and version, Gold clause
existence, overlap requirements, and key-point containment before writing.
Source or validation failure writes nothing.

No command is added to originate a question, Gold clause, or verdict. External
models and existing retrieval commands may provide them, and the resulting
record declares that provenance.

## Preserved quality and safety gates

The change does not relax these invariants:

- Gold clause IDs must exist in the frozen source.
- Record, manifest, and XML document identity and publication version must
  agree.
- The manifest-authorized XML hash and safety boundary must pass.
- Answerable records require Gold and a computed overlap value.
- Key points cannot reproduce clause prose or hide verdict labels as factual
  values.
- Annotation records remain content-addressed and cannot silently replace an
  item lineage.
- Pooling remains an adjudicated, add-only Gold operation.

## Progress reporting

`independent_paths` is removed. Each L1 and L2 progress payload gains a
`provenance` object with:

- `origin_counts`: event counts by origin;
- `gold_origin_chains`: item counts keyed by ordered Gold event labels;
- `model_assisted_items`: records with any model event;
- `model_assisted_label_items`: records with a model event scoped to
  `task_label` or `verifier_pair`;
- `retrieval_originated_gold_items`: records with a retrieval event scoped to
  `gold`.

An event label is `origin@producer` when a producer exists and `origin`
otherwise. `origin_counts` counts events and therefore need not sum to the
number of records. `gold_origin_chains` counts records and preserves the order
of their Gold-scoped events.

Retrieval-originated Gold is admitted, but Recall measured on those items is
reported as diagnostic rather than as an unbiased headline metric. Reports
also disclose model-assisted task labels and Verifier pairs so same-model
self-evaluation risk remains visible.

## Current RFC 9112 candidates

The three user-authored scenarios remain local candidates until final review.
They use RFC 9112 section 6.3 paragraph `section-6.3-2.3` and clause ID
`817e50534fc9d2e00b485d0d445b95992b1fdc25ef354febcd87bfc1be60e7bb`.
Their task labels are, respectively, `violating`, `compliant`, and
`insufficient_evidence`.

Their initial provenance chain records:

1. `human_authored` for `question`;
2. `model_proposal` from `openai-codex` for `gold` and `task_label`;
3. `human_source_review` for `gold` and `task_label`;
4. `model_proposal` from `openai-codex` for `key_points` and `verifier_pair`;
5. `human_source_review` for `key_points` and `verifier_pair` after the user's
   final review.

The candidates receive polished English questions, non-label-bearing key
points, source-computed Jaccard values, and an initial Verifier pair matching
the task label with `supports_verdict=true`. That pair remains a candidate
until the user explicitly approves it.

Each record is first validated against a temporary annotation directory.
After the user reviews the complete records, an explicit approval is required
before the three records are added to `artifacts/restricted/annotations`.
Successful entry changes formal progress from zero to three items, with one of
each L2 verdict.

## Documentation

The current product plan section 8.2 is rewritten from circularity isolation to
Gold provenance auditing and bias disclosure. Because `SpecPilot_项目方案.md`
is an untracked user file, it is edited only within the authorized section and
is not added to Git without separate authorization.

The W1 annotation runbook documents v2 event construction, stable schema
refusal, accepted origins, and metric disclosure. The master roadmap replaces
the independent-path deliverable with provenance auditing. The historical W1
plan receives only a supersession note. The superseded independent-L2 design
remains in history with a visible pointer to this design.

## Verification

Contract tests cover valid ordered chains, required scopes, producer rules,
exact duplicate refusal, and acceptance of model and retrieval origins. CLI
tests cover the v2 template, successful model/retrieval-origin entry, stable v1
refusal, and preservation of all source-aware Gold failures. Progress tests
cover event counts, ordered Gold chains, model-assisted counts, and
retrieval-originated Gold counts.

The implementation runs focused annotation tests, Ruff, mypy, and the complete
suite. The three real RFC 9112 candidates are schema-validated, measured with
`corpus overlap`, and source-aware validated in a temporary directory. No
official annotation or persistent BM25/Qdrant index is created before the
explicit entry approval.

