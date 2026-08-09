# Assisted Annotation and Forced-Choice Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make gold annotation produce records at a rate the schedule can rely
on, by moving the author from authoring to adjudicating — and make that
adjudication a recorded, countable act rather than an unverifiable claim.

**Why this plan exists:** Two weeks of corpus produced 0 of 40 L1 items and 3 of
20 L2, all three anchored to one clause. The cause is not tooling: authoring a
question and locating its gold requires knowing HTTP semantics well enough to
say what a realistic question is and whether a clause answers it. Judging *which
of three clauses answers a given question* needs far less, and it is the part
that cannot be delegated anyway.

**What this plan does not do:** change the corpus. RFC 9110/9112 stays. The
alternative — moving to a corpus in the author's own field — was planned in
`2026-08-09-r1-iiif-corpus-foundation.md` and set aside, because it would have
traded machine-tagged normative keywords, an order of magnitude of
cross-references, and source-published paragraph identity for a problem that a
change of task solves without giving any of that up.

**Architecture:** Proposals are files, not records. A proposal carries a drafted
question, one proposed gold clause, and structurally selected distractors; it is
not gold and cannot become gold without a recorded human choice. `annotation
review` presents the choice, records what was chosen, and writes the final
record through the existing source-checked entry path. Nothing about the storage
discipline, the committable-field rule, or provenance v2 changes.

**Tech Stack:** Python 3.12+, Pydantic 2, argparse, pytest, Hypothesis, Ruff,
mypy. No new dependency.

## The problem this design is really solving

Provenance v2 already permits model-drafted gold and already records it:
`model_proposal@producer > human_source_review`, with `content_origin` and
`label_origin` as `mixed`. The three existing L2 records are exactly that shape.

**The gap is that the chain records that review happened and not what it found.**
A workflow whose reviewer approves everything and a workflow whose reviewer
catches real errors produce identical records. So "the author reviewed it" is
currently an unverifiable claim sitting underneath every downstream number, and
gold is the ruler: a wrong gold makes every metric wrong and nothing catches it.

Forced choice fixes this by construction. A reviewer who is not reading cannot
score above chance, and disagreement with the proposal is visible and countable.
The acceptance rate stops being a reassurance and becomes a statistic.

## Global Constraints

- **The corpus does not change.** RFC 9110/9112 stay frozen as they are, with
  their manifests, their caps, their QA lines, and their three L2 records intact.
- **Distractors are selected structurally, never by retrieval.** §8.2.1 forbids
  the system's own ranking as a source of initial gold, and a distractor set
  drawn from `search_clauses` would put the retriever back inside the gold path
  through the side door. Siblings and near-siblings by section structure only.
- **A proposal is not gold.** Proposal files live outside the annotation store,
  are never counted by `progress`, and cannot enter the store except through a
  recorded review decision.
- **Rejections are stored.** A store holding only accepted proposals reports 100%
  acceptance by construction. Rejected items are recorded, excluded from target
  counts, and included in the acceptance-rate denominator.
- **The deep-review sample is pre-registered and seeded, not chosen.** A reviewer
  who picks which items to check deeply will pick the easy ones. The rule is
  deterministic over `item_id` and fixed before any item is seen.
- The committable-field rule is untouched: no proposal, record, or review log may
  hold clause prose. Questions and criteria are authored text; quotations are not.
- No quality metric is produced by this plan. Acceptance rate and deep-sample
  error rate describe the *gold*, not the system, and must never be reported
  beside retrieval or answer metrics without that distinction.
- The store stays create-only, content-addressed, `0700`/`0600`, add-only for
  gold, with pooling amendment behaviour unchanged.

## What the report will have to disclose, decided now rather than at W6

§8.1 currently promises to disclose "单人标注、无标注者间一致性". Under this
workflow that sentence is no longer accurate and becomes:

> Gold was drafted by a model and adjudicated by a single human reviewer by
> forced choice. There is no inter-annotator agreement. Disclosed with it: the
> proposal acceptance rate, the number of proposals rejected outright, the
> deep-review sample size and the error rate found in it, whether drafted key
> points were edited, and the full `gold_origin_chains` distribution.

Two limits go in the same paragraph, not in a footnote:

1. **Different vendor is not independent error.** The drafter is
   `claude-opus-5`, the main chain is `deepseek-v4-flash`, the judge is
   `glm-5.2` — three vendors, which lowers the most direct correlation and
   proves nothing about shared bias. §8.3.3 says exactly this about the judge
   and the same sentence applies here.
2. **A high acceptance rate is ambiguous on its own.** It reads as "the
   proposals were good" and as "the review was shallow" equally well. Only the
   deep-review sample separates them, which is why the sample is not optional.

## File map

- `src/specpilot/contracts/annotation.py` — `ReviewDecision`, and the review
  fields on L1/L2.
- `src/specpilot/corpus/distractors.py` — structural distractor selection.
- `src/specpilot/annotation/review.py` — the pre-registered deep-review sample
  and the acceptance statistics.
- `src/specpilot/annotation/progress.py` — the new counts.
- `src/specpilot/cli.py` — `annotation review`.
- `tests/unit/annotation/`, `tests/unit/corpus/test_distractors.py`,
  `tests/cli/test_annotation_review.py`.

---

### Task 1: Record what the review decided, not that it happened

**Files:**
- Modify: `src/specpilot/contracts/annotation.py`
- Test: `tests/unit/annotation/test_annotation_contracts.py`

**Interfaces:**
- Produces: `ReviewDecision` with `outcome`, `candidates_shown`,
  `chose_proposal`, `key_points_edited`, `deep_reviewed`.
- Produces: `review: ReviewDecision | None` on both annotation models.

`outcome` is one of `accepted_as_proposed`, `gold_changed`, `item_rejected`. A
rejected item is a real record with no gold, no overlap figure, and no gold
origins — the same shape an unanswerable L1 item already has, distinguished by
its review outcome rather than by absence.

- [ ] **Step 1: Write failing contract tests**

Assert a record whose `gold_origins` contain `model_proposal` requires a
`review`; that a human-only record does not; that `item_rejected` forbids gold
and `accepted_as_proposed` requires it; that `chose_proposal` is false whenever
the outcome is `gold_changed`; and that no review field can hold prose.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement, leaving v2 schema literals unchanged**

The schema version does not move. `review` is an optional field on the same v2
models, so the three existing records stay valid and stay readable — they record
a review that happened under the older, weaker discipline, and that is exactly
what they should continue to say.

---

### Task 2: Structural distractors

**Files:**
- Create: `src/specpilot/corpus/distractors.py`
- Test: `tests/unit/corpus/test_distractors.py`

**Interfaces:**
- Produces: `select_distractors(clauses, gold_clause_id, count, seed)`.

A distractor has to be plausible or the choice is not a test. Plausible here
means structurally near: another clause in the same section, then a sibling
section under the same parent, then the same top-level section. Never the whole
document at random, and never anything a retriever ranked.

- [ ] **Step 1: Write failing selection tests**

Assert distractors never include the gold clause; that they come from the
nearest available structural tier and widen only when a tier is exhausted; that
selection is deterministic under a seed so a review can be reconstructed; and
that the function's signature admits no ranking, score, or query input at all —
the retriever must be unable to reach this even by mistake.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement and verify against the frozen corpus**

Report the tier distribution actually used across a sample of gold clauses.

---

### Task 3: The pre-registered deep-review sample

**Files:**
- Create: `src/specpilot/annotation/review.py`
- Test: `tests/unit/annotation/test_review.py`

**Interfaces:**
- Produces: `deep_review_required(item_id, rate, salt) -> bool` and
  `ReviewStatistics`.

- [ ] **Step 1: Write failing tests**

Assert the decision is a deterministic function of `item_id` and the salt, so it
is fixed before the item is seen and cannot be recomputed to a convenient
answer; that the realised rate over many ids approaches the configured one; and
that changing the salt changes the sample, so the salt has to be recorded with
the evaluation set.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement, with the rate and salt recorded, not hard-coded**

---

### Task 4: `annotation review`

**Files:**
- Modify: `src/specpilot/cli.py`
- Test: `tests/cli/test_annotation_review.py`

**Interfaces:**
- Produces: `specpilot annotation review --proposal <file> --annotation-dir ...
  --manifest ... --manifest-dir ... --xml ...`

The command prints the question and the candidates as locators and clause text
read locally, takes the choice, and writes the record through the same
source-checked entry path `annotation add` uses. Nothing bypasses those checks:
a chosen clause still has to exist in the named frozen document, key points
still cannot restate their clause, and the overlap figure is still computed
rather than supplied.

- [ ] **Step 1: Write failing CLI tests**

Assert candidates are presented in a seeded order so the proposal is not always
first — a reviewer who learns that position A is always the proposal is back to
approving. Assert choosing the proposal yields `accepted_as_proposed` with
`chose_proposal` true; choosing another yields `gold_changed`; choosing none
yields `item_rejected` with no gold; that a deep-review item is labelled as such
before the choice is taken; and that the proposal file is never treated as a
record.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement and verify GREEN**

---

### Task 5: Progress reports the review statistics

**Files:**
- Modify: `src/specpilot/annotation/progress.py`, `src/specpilot/cli.py`
- Test: `tests/unit/annotation/test_progress.py`,
  `tests/cli/test_annotation_progress.py`

- [ ] **Step 1: Write failing progress tests**

Assert `proposal_acceptance_rate` counts rejected items in its denominator;
that rejected items do not count toward the §8.1 targets; that deep-review
coverage is reported against the configured rate so an under-sampled set is
visible; and that `key_points_edited` is counted. Assert none of these appear
anywhere near a retrieval or answer figure in the output.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement and verify GREEN**

---

### Task 6: The drafting pass

**Files:**
- Create (never committed): proposal files under `tmp/proposals/`

Drafted against the concrete parts of the corpus first — §15 status codes, §9
methods, §12 content negotiation, §13 conditional requests. The abstract parts
are where the three existing L2 records went and are the reason this plan
exists; §9's framing sections are not where a first pass should start.

- [ ] **Step 1: Draft 20 L1 proposals across at least 8 distinct section
      families**, with the 60/40 clause-first / scenario-first mix §8.2.2
      requires and at least 3 deliberately unanswerable items.

- [ ] **Step 2: Draft key points for each, marked as drafted**

Key points are the part the forced choice cannot check — there is nothing to
compare a criterion against. They are drafted for speed and the review records
whether they were edited, so "accepted verbatim" is a countable fact rather than
an invisible one.

- [ ] **Step 3: Verify every proposal validates and none holds clause prose**

---

### Task 7: The review pass — OWNER: the author

**Deliberately left unchecked, like W0 Task 8 Step 4 and W1 Task 6.**

- [ ] **Step 1: Review the 20 proposals and record the wall-clock time**

The timings matter as much as the records. Product plan §11 now says completion
is determined entirely by annotation throughput and that the throughput has
never been measured. This pass measures the throughput of the workflow that will
actually be used, not of the one that produced nothing.

- [ ] **Step 2: Deep-review the sampled subset against the frozen source**

The sample is already chosen. Read the full section, not just the candidates,
and record what the deep read found — including finding nothing.

---

## Plan self-review record

- **Scope decision:** this plan changes how gold is produced and recorded. It
  does not change the corpus, the storage discipline, the committable-field
  rule, provenance v2, or anything provider-side.
- **The real defect named:** the existing chain records that review happened and
  not what it found, so the reviewer's diligence is currently an unverifiable
  claim under every downstream number. Forced choice makes it countable.
- **Retrieval kept out by signature, not by convention:** Task 2's selection
  function admits no query, score, or ranking argument, so §8.2.1's rule cannot
  be violated by a later caller who forgets it.
- **Rejections counted:** without storing them the acceptance rate is 100% by
  construction, which is the exact shape of a number that reassures and measures
  nothing.
- **The sample is seeded before the reviewer looks:** a self-selected deep review
  samples the easy items and estimates the wrong error rate.
- **Disclosure decided now:** the §8.1 sentence, its two limits, and the
  ambiguity of a high acceptance rate are written here rather than left to be
  worded under pressure at W6.
- **Author-owned steps marked:** Task 7 is the author's, and Task 6 Step 2 is
  explicit that drafted key points are the weakest link in the chain.
- **Placeholder scan:** every implementation step names concrete behaviour,
  files, and verification.
