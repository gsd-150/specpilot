# Independent L2 Annotation Design

## Goal

Create three formal L2 development annotations without promoting scenarios,
gold clauses, or verdicts that a model derived from a known clause.

## Provenance boundary

The existing `tmp/l2-dev-001.json`, `tmp/l2-dev-002.json`, and
`tmp/l2-dev-003.json` are contaminated drafts because their gold clause and
expected verdicts were fixed before their scenarios were written. They remain
outside the formal annotation store and are not repaired or promoted.

Each replacement item uses a fresh item identifier and follows a human-first
path:

1. The human author supplies a raw scenario and atomic claim without a clause
   identifier or verdict.
2. The human author independently navigates the frozen RFC source and records
   the selected gold paragraph and expected verdict.
3. The human author confirms the proposed-verdict/support pair.
4. Model assistance is limited to wording polish, non-label-bearing key-point
   phrasing, deterministic overlap calculation, and mechanical validation.

The annotation report must disclose that prose was model-assisted.

## Record construction

Each item is an `annotation-l2/v1` development record with a unique `item_id`
and `claim_id`. The record preserves the author's independent-path value,
document identity, chosen gold clause, expected verdict, proposed verdict, and
support relation.

Key points describe observable reasoning criteria. They must not contain a
verdict label, reproduce the gold paragraph as a sentence, or disclose clause
prose. The overlap value is computed by the project command against the frozen
source rather than estimated manually.

## Validation and entry

For each completed candidate:

1. Validate the JSON contract locally.
2. Recompute `question_gold_jaccard` from the frozen source.
3. Run source-aware annotation validation to check document identity, gold
   existence, and key-point containment.
4. Present the complete candidate to the human author for final correction.
5. Add it to `artifacts/restricted/annotations` only after explicit approval.

Formal progress remains unchanged until step 5 succeeds. Validation must not
create a persistent retrieval index or modify the frozen RFC source.

## Success criteria

- Three fresh, human-originated scenarios are accepted by the L2 schema and
  source-aware checks.
- Their gold clauses and verdict fields are human decisions, not model
  proposals.
- No contaminated draft enters the formal annotation directory.
- The annotation progress report counts exactly the records explicitly
  approved for entry.
