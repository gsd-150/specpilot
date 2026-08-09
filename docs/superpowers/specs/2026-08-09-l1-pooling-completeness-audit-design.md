# L1 Pooling Completeness Audit Design

**Date:** 2026-08-09
**Status:** approved for implementation planning

## Goal

Run the one-time §8.2.3 completeness audit over all 20 existing L1
annotations. Every item receives an explicit adjudication. Missing gold may be
added through the existing append-only successor mechanism; existing gold is
never removed.

The audit must resolve the known `l1-dev-010` defect, where RFC 9110 §15.4.5
splits one requirement across consecutive paragraphs: paragraph 2 states the
obligation and paragraph 3 supplies the required field list. The current item
names only paragraph 3. The general mechanism must support any item whose
complete support spans more than one clause, rather than special-casing that
item.

## Scope

The run covers exactly the 20 L1 items currently in the formal annotation
store: 15 dev and 5 locked. It does not create new questions, change key
points, fill the remaining L1 or L2 target counts, compute retrieval quality,
or tune either retrieval route.

This is a completeness audit, not an evaluation. Candidate retrieval proposes
clauses for human review; it never creates gold by itself. The author owns every
adjudication.

## Candidate generation

Each item is queried once through two independent frozen routes:

- BM25-only top 5;
- dense-only top 5.

The candidate pool is their ordered union, deduplicated by `unit_id`. Hybrid,
RRF, fused, or reranked output is rejected as a pooling input. Existing gold is
shown beside the candidates for comparison but does not need to occur in either
top-5 list for the audit to proceed.

Every candidate record contains only:

- item ID;
- unit ID and locator metadata;
- originating route or routes and rank in each route;
- content hash;
- the frozen retrieval configuration identity.

Candidate records never contain clause prose. Full text is resolved locally
only while presenting the adjudication sheet.

## Run identity and one-time boundary

Before the first adjudication, the tool creates a content-addressed pooling run
record binding:

- the exact ordered list of 20 item IDs;
- source and corpus identities;
- BM25 parameters and frozen IDF identity;
- dense collection, embedding weights, vector schema, and point inventory
  identity;
- top-k of 5 for each route;
- the candidate unit IDs, route ranks, and content hashes for every item;
- creation time and author identity.

The run directory is create-only. A second run with the same registered item
set is refused, including after a partial adjudication. Resumption reads the
existing run and continues only its unadjudicated items; it never regenerates
candidates. This prevents a changed index from silently altering a half-finished
audit.

## Adjudication

For every item, the local review sheet shows the question, current gold, and
candidate clauses. The author selects one outcome:

1. `gold_complete`: current gold is complete;
2. `gold_extended`: one or more candidate clauses are additional gold;
3. `audit_blocked`: the candidate set or local source is insufficient to make
   an honest decision.

`gold_complete` records a pooling adjudication without changing the annotation.
`gold_extended` first records the adjudication and then calls
`AnnotationStore.amend` to create a successor containing the union of old and
new gold. It appends a `retrieval_pooling` origin followed by
`human_source_review`; it cannot remove or replace prior gold. If amendment
writing fails, the adjudication remains as an explicit record of the successor
that is still owed, and the run cannot be sealed.

`audit_blocked` records the reason and leaves the item incomplete. The command
returns non-zero when any item is blocked.

For `l1-dev-010`, the expected correction is to retain paragraph 3 and append
paragraph 2 if the author confirms that both are required. The tool must not
hard-code either clause ID or auto-select paragraph 2.

## Completion and reporting

The run seals only when all 20 registered items have one successful
adjudication and every `gold_extended` outcome has a corresponding stored
successor annotation.

`annotation progress` then reports:

- `awaiting_adjudication: 0` for L1;
- 20 pooling adjudications;
- counts of `gold_complete`, `gold_extended`, and blocked outcomes;
- the number of added gold clauses;
- retrieval-originated gold items separately from independently originated
  gold;
- the sealed pooling run ID.

These are process and gold-quality audit statistics. The command must not emit
Recall, MRR, Hit@k, answer accuracy, or any other system-quality metric.

## Failure handling

The audit fails closed when:

- a source, annotation head, candidate content hash, BM25 identity, dense
  collection identity, or point inventory no longer matches the registered
  run;
- a fused ranking is supplied;
- a candidate names a unit outside the frozen corpus;
- an amendment would remove gold or lacks a new provenance event;
- a second run attempts to replace the registered candidate pool;
- sealing is requested with missing, blocked, or unapplied adjudications.

All restricted candidate, decision, and successor records remain under
`artifacts/restricted/`, with directory mode `0700` and file mode `0600`. They
remain git-ignored. Git receives only code, tests, the design and plan, and a
sanitized aggregate completion report.

## Components

- `specpilot.retrieval.pooling`: immutable run and candidate contracts,
  independent-route pool construction, registration, resumption, and sealing.
- `specpilot.annotation.store`: existing append-only amendment path; no new
  mutation semantics.
- `specpilot.cli`: commands to register a run, adjudicate/resume it, and report
  status without printing source text in machine-readable output.
- `specpilot.annotation.progress`: aggregate pooling completion and provenance
  counts.

The pooling module accepts `RouteRanking` and cannot accept `FusedRanking` by
type. Candidate generation receives the two route results; it does not own or
reimplement BM25, dense search, clause loading, or annotation storage.

## Test strategy

Unit tests prove:

- the pool is exactly the union of BM25-only and dense-only top 5;
- fused route names and fused ranking types are refused;
- candidate hashes and route origins are stable and complete;
- run registration is content-addressed and create-only;
- resumption reuses registered candidates rather than rerunning retrieval;
- every outcome is represented and sealing refuses incomplete work;
- added gold is a strict superset and records both required origin events;
- candidate and aggregate records cannot contain source prose or quality
  metrics.

CLI tests prove an end-to-end fixture run can register, stop, resume, extend a
multi-clause item, seal, and make progress report zero awaiting adjudications.
The production run then uses the frozen RFC corpus and requires the author to
make each of the 20 choices interactively.

## Non-goals

- No corpus-manifest implementation is smuggled into this feature. Until W2
  Task 6 exists, the run binds the currently available source manifests and
  explicit retrieval identities; the future corpus manifest must be required
  before any evaluation run, but it does not retroactively change this audit.
- No automatic gold selection, LLM adjudication, question drafting, or key-point
  editing.
- No retrieval tuning after candidate inspection.
- No second pooling pass after the run is sealed.
