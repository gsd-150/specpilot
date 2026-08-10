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

**Measured, for the first 20 L1 items** — these are the numbers that sentence
takes, not placeholders:

> 20 items drafted by `claude-opus-5` and adjudicated by one reviewer by forced
> choice against three structurally selected distractors. 19 accepted as
> proposed, 1 gold changed, 0 rejected, 0 drafted key points edited; acceptance
> rate 0.95. A pre-registered sample of 5 (rate 0.25, salt `r1-2026-08`,
> committed before the pass) was read against the full source: 5 of 5 confirmed
> the gold complete, 0 additional clauses found, median 91s and minimum 34s per
> read. **0 errors in 5 reads bounds the gold error rate below 45% at 95%
> one-sided confidence** — enough to exclude a badly wrong gold, not enough to
> call it verified. Gold origin chain for every answerable item:
> `model_proposal@claude-opus-5 > human_source_review`. No inter-annotator
> agreement exists for any item.

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

- `src/specpilot/contracts/annotation.py` — `ReviewDecision`. Not fields on
  L1/L2: see Task 1's correction.
- `src/specpilot/contracts/proposal.py` — the drafted item, which is a file and
  not a record. Not in the original file map; a validated input format needs a
  contract like any other.
- `src/specpilot/corpus/distractors.py` — structural distractor selection.
- `src/specpilot/annotation/review.py` — the review store, the pre-registered
  deep-review sample, and the acceptance statistics.
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

- [x] **Step 1: Write failing contract tests**

Assert a record whose `gold_origins` contain `model_proposal` requires a
`review`; that a human-only record does not; that `item_rejected` forbids gold
and `accepted_as_proposed` requires it; that `chose_proposal` is false whenever
the outcome is `gold_changed`; and that no review field can hold prose.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement, leaving v2 schema literals unchanged**

**Corrected during execution: the decision is a record beside the annotation,
not a field on it.** Adding `review` to the model was tried first and broke all
three stored records immediately. `canonical_sha256` hashes every field, so a
new field — even one defaulting to null — changes the canonical bytes and the
stored `annotation_id` no longer matches. Content addressing makes any field
addition a schema change, which the plan asserted it would not be.

Separating it turned out to be the better shape anyway, for a reason the schema
problem only surfaced: an annotation's identity should not move because somebody
reviewed it. The question, the gold, and the key points are what the item *is*;
a review is a later judgement about it by a different actor. Keeping them apart
also makes a re-review an additional record rather than an edit, so a change of
mind leaves both decisions behind.

One trap found on the way: `canonical_json` strips a fixed set of field names —
`manifest_id` and `annotation_id` — as a record's own content ID. A foreign key
spelled `annotation_id` is therefore dropped from both the hash and the file.
The field is `reviewed_annotation_id`, and the reason is a comment beside it.

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

- [x] **Step 1: Write failing selection tests**

Assert distractors never include the gold clause; that they come from the
nearest available structural tier and widen only when a tier is exhausted; that
selection is deterministic under a seed so a review can be reconstructed; and
that the function's signature admits no ranking, score, or query input at all —
the retriever must be unable to reach this even by mistake.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and verify against the frozen corpus**

Five tiers rather than three, because three could not be ordered consistently.
Written as "same section, sibling section, top-level section" they invert for a
gold in a top-level section: its own subsections are nearer than any sibling,
yet they land in the widest tier. The tiers are therefore nested scopes of the
source's own numbering — the gold's section, its parent, a higher ancestor, its
top-level section, the rest of the document — so each contains the one before it
and "widen only when exhausted" holds by construction rather than by care.
`same_section` includes the gold section's subsections: §5.6.2 contains §5.6.2.1
and a reader citing the outer number has cited both.

One rule the plan did not name and the corpus needs: a distractor must come from
the same document and version as the gold. A clause from the other RFC is not a
plausible wrong answer, it is a giveaway.

**Tier distribution, measured over every clause as gold in turn** — 1,559 golds
in RFC 9110 and 348 in RFC 9112, three distractors each, seed `r1-2026-08`:

| tier | RFC 9110 | RFC 9112 |
| --- | --- | --- |
| `same_section` | 4,355 (93.1%) | 961 (92.0%) |
| `same_parent` | 322 (6.9%) | 83 (8.0%) |
| `same_ancestor` / `same_top_level` / `same_document` | 0 | 0 |

No gold anywhere in either document produced a short set, and none had to widen
past its parent. The 7–8% that widen are the 48 single-clause sections. So the
forced choice is between clauses that sit within one section of each other in
every case — which is the point, and was not guaranteed before it was measured.

---

### Task 3: The pre-registered deep-review sample

**Files:**
- Create: `src/specpilot/annotation/review.py`
- Test: `tests/unit/annotation/test_review.py`

**Interfaces:**
- Produces: `deep_review_required(item_id, rate, salt) -> bool` and
  `ReviewStatistics`.

- [x] **Step 1: Write failing tests**

Assert the decision is a deterministic function of `item_id` and the salt, so it
is fixed before the item is seen and cannot be recomputed to a convenient
answer; that the realised rate over many ids approaches the configured one; and
that changing the salt changes the sample, so the salt has to be recorded with
the evaluation set.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement, with the rate and salt recorded, not hard-coded**

`deep_review_required` shipped with Task 4; `ReviewStatistics` followed with
Task 5, which is where the counting is consumed — written earlier it would have
been written against a guess at what the report needs.

Brought forward out of order because Task 4 could not meet its own acceptance
criterion without it: an item cannot be labelled for deep review before the
choice is taken if nothing can say which items are sampled. `--deep-review-rate`
and `--deep-review-salt` are required arguments with no defaults, and both are
echoed in the command's output — a sample that ran at a rate nobody chose, under
a salt nobody recorded, cannot be checked afterwards, which is the entire reason
the sample exists.

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

- [x] **Step 1: Write failing CLI tests**

Assert candidates are presented in a seeded order so the proposal is not always
first — a reviewer who learns that position A is always the proposal is back to
approving. Assert choosing the proposal yields `accepted_as_proposed` with
`chose_proposal` true; choosing another yields `gold_changed`; choosing none
yields `item_rejected` with no gold; that a deep-review item is labelled as such
before the choice is taken; and that the proposal file is never treated as a
record.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and verify GREEN**

Four decisions the plan left open, settled by writing it:

**A rejection points at nothing.** `ReviewDecision.reviewed_annotation_id` is
now null exactly when the outcome is `item_rejected`, enforced both ways by a
validator. The plan proposed storing a rejected item as a record "the same shape
an unanswerable L1 item already has", which would have been wrong: an
`expected_refusal` record asserts the *system* should refuse the question, and a
rejected draft asserts nothing of the kind. Filing one as the other would have
put discarded drafts into the evaluation set as unanswerable items. The
rejection lives in the review store, which is where the acceptance-rate
denominator is read from anyway.

**The drafter does not supply the distractors.** The proposal file names one
clause; the wrong answers are selected at review time from a seed given on the
command line. A drafter who supplied them could supply obviously wrong ones and
the forced choice would measure nothing.

**Edited key points are computed, not asked about.** The proposal carries
`drafted_key_points` and `key_points`, identical when written. The reviewer edits
the second. Whether they still match is a fact about two lists — asking "did you
edit them?" would be exactly the self-report this plan exists to remove.

**`content_origin` is `model`, not `mixed`.** The three existing records use
`mixed` because a human was in the question loop. Here the question is the
drafter's wording start to finish and the reviewer only accepts or rejects the
item carrying it. `label_origin` stays `mixed`, which is where the human's
judgement actually lands. Overstating the human's share of the text is the
easiest thing to get wrong here and the hardest to notice at W6.

Two properties worth recording. The command prints clause text — the only one
that does, because nobody can choose between four clauses without reading them —
and a test asserts the stored annotation and the stored decision both still hold
locators only. And the sheet is a pure function of the seed, so a mistyped answer
costs nothing: the reviewer runs the command again and sees the identical sheet.
That is why the choice is read once rather than in a retry loop.

**End-to-end against RFC 9110 §15.5.6** (405 Method Not Allowed), three
distractors, seed `r1-2026-08`: the selector offered §15.5.2 ¶1 (401, the other
status in that section carrying a MUST-send-this-header rule), §15.5.6 ¶2 (right
section, wrong aspect — cacheability), and §15.5.20 ¶1 (421). The gold was
presented third of four, not first.

---

### Task 5: Progress reports the review statistics

**Files:**
- Modify: `src/specpilot/annotation/progress.py`, `src/specpilot/cli.py`
- Test: `tests/unit/annotation/test_progress.py`,
  `tests/cli/test_annotation_progress.py`

- [x] **Step 1: Write failing progress tests**

Assert `proposal_acceptance_rate` counts rejected items in its denominator;
that rejected items do not count toward the §8.1 targets; that deep-review
coverage is reported against the configured rate so an under-sampled set is
visible; and that `key_points_edited` is counted. Assert none of these appear
anywhere near a retrieval or answer figure in the output.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and verify GREEN**

**The deep-review sample is recomputed, not believed.** Counting how many
decisions carry `deep_reviewed` would always agree with itself: the flag is set
by the same function that decides the sample, so it can only ever match. The
failure it needs to catch is a pass run under a rate or salt that is not the one
the evaluation set declares — `--deep-review-rate 0.0` records no deep reviews
and looks complete on its own terms. `review_statistics` therefore takes the
*declared* rate and salt and recomputes which reviewed items should have been
sampled, so that run reports coverage 0 of 10 instead of nothing at all.

**Every decision counts, including a second one about the same item.** The store
has no clock — files are content-addressed and read in hash order — so "the
latest decision" is not knowable from it. Counting all of them is well defined
and errs downward: a rejection later overturned still shows as a rejection,
which understates the acceptance rate rather than overstating it. `re_reviews`
makes the gap between decisions and items visible.

**`gold_review` is a top-level block, absent when reviews were not asked for.**
Not a field inside `l1`, where a reader finding an acceptance rate would take it
for a result about SpecPilot's answers, and not an empty block, which would read
as "no reviews happened" rather than "none were requested". The block carries
`measures: gold_quality`, and a test asserts no key in it contains a retrieval or
answer metric name.

`--review-dir` is optional, but supplying it makes `--deep-review-rate` and
`--deep-review-salt` required — argparse cannot express that, so the handler
refuses with `deep_review_sample_undeclared`. Either reviews go unreported, or
they are reported against a sample somebody declared.

---

### Task 6: The drafting pass

**Files:**
- Create (never committed): proposal files under `tmp/proposals/`

Drafted against the concrete parts of the corpus first — §15 status codes, §9
methods, §12 content negotiation, §13 conditional requests. The abstract parts
are where the three existing L2 records went and are the reason this plan
exists; §9's framing sections are not where a first pass should start.

- [x] **Step 1: Draft 20 L1 proposals across at least 8 distinct section
      families**, with the 60/40 clause-first / scenario-first mix §8.2.2
      requires and at least 3 deliberately unanswerable items.

| | drafted | required |
| --- | --- | --- |
| proposals | 20 | 20 |
| splits | dev 15, locked 5 | — |
| clause-first share | 0.60 exactly (12 / 8) | §8.2.2 0.60 |
| section families | 9 — §7, §8, §9, §10, §11, §12, §13, §14, §15 | ≥ 8 |
| unanswerable | dev 3, locked 2 | ≥ 3; §8.1 floors dev 3, locked 5 |

The locked floor of 5 unanswerable items is not met and cannot be by a 20-item
pass that is mostly dev. Three more locked refusals are owed to a later pass.

Gold is named in the drafting script by section number and paragraph ordinal and
the clause ID is looked up from the frozen document. A hand-pasted 64-hex digest
is one keystroke away from a proposal that points at the wrong clause and still
validates against everything.

**The five unanswerable items were each checked against both documents, not
assumed.** An item labelled unanswerable that some clause does answer is corrupt
gold, and it corrupts quietly — the system would be marked wrong for being
right. Two are unanswerable because the subject is absent from HTTP semantics
entirely (a status code for excessive request rates; a size limit on client-side
stored state). Three are the harder shape, where a topically adjacent clause
exists and still does not answer: the obligation to send a final status code
after 100 (Continue) carries no deadline; §11 defines the authentication
framework and not any scheme's cryptographic parameters; and §8.3.1 prints
`text/html;charset=utf-8` as a spelling example, which retrieval finds instantly
and which requires no encoding of anything.

- [x] **Step 2: Draft key points for each, marked as drafted**

Key points are the part the forced choice cannot check — there is nothing to
compare a criterion against. They are drafted for speed and the review records
whether they were edited, so "accepted verbatim" is a countable fact rather than
an invisible one.

- [x] **Step 3: Verify every proposal validates and none holds clause prose**

`tmp/verify_proposals.py` checks all six: the contract validates, the gold clause
exists in the named frozen document, no drafted key point restates its clause,
the structural selector can fill the candidate set, the direction mix and
unanswerable floors hold, and the families spread. All 20 pass.

Then every proposal was run through `annotation review` against the real RFC
9110, answering "none" each time so nothing was written to the real store: 20
sheets rendered, 0 annotations, 20 throwaway decisions, no refusals. All 45
distractors came from `same_section` — the hardest tier, where every candidate
is topically identical to the gold and only the specific requirement separates
them. Question-to-gold literal overlap spans 0.06 to 0.43, so §8.2.2's
stratification by overlap has both strata to work with.

---

### Task 7: The review pass — OWNER: the author

**Deliberately left unchecked, like W0 Task 8 Step 4 and W1 Task 6.**

#### The sample, registered before the pass

Chosen by the author on 2026-08-09, before any proposal was opened. Committed
here rather than noted afterwards, because that is what makes "pre-registered"
checkable — the timestamp on this commit precedes the first review record.

- **Rate:** 0.25 · **Salt:** `r1-2026-08` · **Seed:** `r1-2026-08`
- **Sampled, 5 of 20:** `l1-dev-007`, `l1-dev-008`, `l1-locked-001`,
  `l1-locked-002`, `l1-locked-004`

Recomputable by anyone with the salt, which is why `annotation progress` takes
the rate and salt rather than trusting what a run happened to record. Three of
the five are `locked` items, which is where a wrong gold is most expensive: the
locked split is not looked at again.

- [x] **Step 1: Review the 20 proposals and record the wall-clock time**

20 of 20 reviewed on 2026-08-09. 19 accepted as proposed, 1 gold changed, 0
rejected, 0 key points edited. L1 reaches 20 of 40: dev 15 of 15, locked 5 of 25.

**Throughput, the figure §11 said had never been measured: 22 seconds median per
item**, 8m11s for 19 items. The twentieth, `l1-dev-001`, recorded 2179s and is
the session where the runner was being debugged; it is excluded as an artifact
rather than averaged in, which would have reported 172s and described nothing
that happened. At ~25s per item the remaining 20 L1 items are well under an
hour of adjudication, so annotation throughput is no longer the schedule's
binding constraint. §11 needs rewriting around that.

**The one disagreement is a real find, and it is about the corpus rather than
the drafting.** `l1-dev-010` asks which header fields a 304 must carry over from
the 200 it replaces. The proposal named §15.4.5 ¶2, which states the obligation
and ends in a colon; the reviewer chose ¶3, which is the list the colon leads
into — `Content-Location, Date, ETag, and Vary`. Neither is wrong and neither is
sufficient: RFC 9110 splits the requirement across two paragraph anchors and a
clause is a paragraph, so the correct gold is the pair. A forced choice over one
clause cannot express that. The item now carries ¶3 alone at overlap 0.0, which
means a system that retrieves ¶2 and answers correctly scores as a retrieval
miss. Owed: a §8.2.3 adjudication adding ¶2, and a rule for requirements that
span consecutive paragraphs.

- [x] **Step 1a (new): give the deep read somewhere to land**

**`deep_review_coverage: 1.0` is not evidence that a deep review happened, and
that is a defect in this plan's own design.** Sampled items took a median of 24
seconds against 22 for the rest — indistinguishable. `l1-dev-007` sits in §14.2,
thirteen paragraphs long, and was answered in 14 seconds. Whatever the reviewer
did, it was not reading the section.

The cause is that `deep_reviewed` records that the banner was printed, not what a
deep read found. A reviewer who ignores it leaves a byte-identical record, and
the report then says coverage is complete. That is exactly the defect this plan
was written to fix — "the chain records that review happened and not what it
found" — reproduced one level down, by me, in the mechanism meant to be the
check on everything else. Step 2 below asks to record what the deep read found
"including finding nothing", and there is no field that holds it.

A boolean cannot be repaired into evidence. What the deep read has to produce is
a separate recorded act with a finding in it, and it has to be a separate pass:
asking someone to alternate between 20-second choices and 10-minute reads
guarantees the reads lose.

**Built.** `DeepReviewFinding` and `annotation deep-review`, with coverage now
computed from findings rather than from the flag. Re-running the report over the
same pass turns `deep_review_coverage: 1.0` into `0.0`, beside
`deep_review_flagged: 5` and `deep_review_recorded: 0` — the two numbers side by
side, because one cannot show a gap.

Four decisions in it:

- **The gold is labelled on the sheet, not hidden.** The forced choice is over
  and recorded; a second blind test would measure the same thing again. What is
  wanted now is the section's other clauses, so the question becomes "what else
  bears on this" rather than "which one".
- **Scope is the section for an item with gold, literal search for one without.**
  An unanswerable item has no section to read and its claim is about the whole
  document. §8.2.1 bars retrieval as a source of *initial* gold; this is §8.2.3's
  completeness audit, where pooling proposing candidates for a human to
  adjudicate is the sanctioned use, and any gold that comes of it records its
  retrieval origin.
- **Finding extra gold amends the item.** The deep read produces better gold, not
  just a record of having looked, so `gold_extended` appends through the existing
  add-only amendment with a `human_source_review` origin. The finding is written
  first: if the amendment fails it names exactly which clauses are still owed.
- **The duration is measured, not asked for.** A thirteen-paragraph section
  closed in twelve seconds was not read, and `deep_review_seconds_min` puts that
  in the report next to the median, which would hide it. This is not tamper-proof
  — a terminal can be left open — but skipping currently costs nothing at all,
  and that is the whole difference.

The sample now has findings, so the §8.1 disclosure may claim one — bounded as
Step 2 below bounds it.

The timings matter as much as the records. Product plan §11 now says completion
is determined entirely by annotation throughput and that the throughput has
never been measured. This pass measures the throughput of the workflow that will
actually be used, not of the one that produced nothing.

- [x] **Step 2: Deep-review the sampled subset against the frozen source**

All five, 13m41s. `gold_complete` on every one, no gold added, no question
flagged. `deep_review_coverage` is 1.0 and this time it is computed from
findings.

**Giving the read somewhere to land is what made it happen.** The same five
items, under the choice pass and then under the deep pass:

| item | scope | choice | deep | s/clause |
| --- | --- | ---: | ---: | ---: |
| `l1-dev-007` | §14.2, 13 ¶ | 14s | **483s** | 37.2 |
| `l1-dev-008` | §14.5, 4 ¶ | 15s | 163s | 40.8 |
| `l1-locked-004` | search, 8 hits | 24s | 91s | 11.4 |
| `l1-locked-001` | §15.5.17, 6 ¶ | 30s | 50s | 8.3 |
| `l1-locked-002` | §7.6.2, 5 ¶ | 38s | **34s** | 6.8 |

The largest section went from 14 seconds to eight minutes. Nothing about the
reviewer changed between the two passes; what changed is that the second one
could not be completed without producing a finding.

**The per-clause rate still spans six-fold, and the report should say so.** The
first two reads ran at ~38-41 s/clause and the rest at 7-11. Five points cannot
distinguish attention decay from those sections simply being shorter per
paragraph, and `l1-locked-002` at 34 seconds total is the one a reader should be
allowed to weigh. That is why `deep_review_seconds_min` is reported beside the
median rather than only the median.

**What 0 errors in 5 reads is worth, stated before anyone rounds it up.** The
95% one-sided upper bound on the gold error rate is **45%** (Clopper-Pearson;
the rule of three gives 60%). So this sample rules out a catastrophically wrong
gold and establishes nothing stronger. It moves the 0.95 acceptance rate from
uninterpretable to weakly supported — the deep reads did happen and found
nothing — and it does not make the gold verified. Widening that bound
meaningfully needs a bigger sample, not a better argument: 0 in 20 would bound
it at 14%.

---

---

### Task 8 (added): score the frozen retrieval protocol on the dev split

The annotation was the input to an evaluation that had never run. The corpus was
frozen, three routes existed, fifteen dev items were adjudicated and pooled — and
there was not one retrieval number in the project. This closes that.

- [x] **`src/specpilot/evaluation/retrieval.py` + `specpilot retrieval evaluate`**

The protocol is read from the frozen corpus manifest, not from flags: top-k per
route, the fusion constant and the final cut-off were bound at freeze time
precisely so an evaluation cannot quietly score a different retriever than the
one the corpus was frozen with. The run verified the BM25 fingerprint, the
embedding weights hash and the Qdrant point count against the manifest before
scoring anything.

**Dev split, N=12 answerable items, corpus `1abafff7…`, protocol 20/20 → RRF(60)
→ 5:**

| route | Macro-Recall@5 | Hit@5 | MRR | gold found |
| --- | ---: | ---: | ---: | ---: |
| bm25 | 0.792 | 0.833 (10/12) | 0.792 | 10/13 |
| dense | 0.875 | 0.917 (11/12) | 0.847 | 11/13 |
| rrf | 0.875 | 0.917 (11/12) | 0.856 | 11/13 |

**Stratified by question-to-gold literal overlap, median boundary 0.268** — this
is the finding §8.2.2 built the stratification to surface:

| route | low overlap (n=6) | high overlap (n=6) |
| --- | ---: | ---: |
| bm25 | 0.667 | 0.917 |
| dense | 0.833 | 0.917 |
| rrf | 0.833 | 0.917 |

Sparse and dense are indistinguishable where the question shares wording with its
clause, and sparse falls away where it does not. That is the expected shape, and
it is now measured on this corpus instead of assumed — which is the whole reason
the overlap figure is a required annotation field.

**What these numbers are not.** N=12, so one item is 8.3 points and every
percentage is descriptive; dense beating bm25 overall is a one-item difference
and RRF's MRR edge over dense is one rank position. The stratified gap is the
only comparison here with a mechanism behind it, and it rests on six items a
side. Dev split only — locked stays unread until W6, which is why `--split` is
required and echoed in the output.

**The two items that cost the points, both instructive:**

- `l1-dev-002` — the scenario-first proxy-retry question, gold §9.2.2 ¶7, literal
  overlap 0.065, the lowest in the set. Every route misses it at k=5; RRF puts it
  at rank 13. It is the entire gap between 11/12 and 12/12, and it is the item the
  set most needs: a question phrased the way a user would phrase it, sharing
  almost no vocabulary with the clause that answers it.
- `l1-dev-010` — recall 0.50 because the pooling audit gave it a second gold
  clause. This is the split-requirement case: §15.4.5 ¶2 states the obligation and
  ends in a colon, ¶3 is the list it introduces. The forced choice could only take
  one and took ¶3; **the completeness audit independently added ¶2 back**, with the
  chain recording exactly how it was found — `model_proposal >
  human_source_review > bm25_retrieval > dense_retrieval > human_source_review`.
  Retrieval now finds one of the two at k=5, which is the honest score for a
  requirement that spans two paragraph anchors.

**Three §8.4 metrics are deliberately absent, and the command says so in its own
output** rather than leaving a reader to notice: nDCG@10, which §8.4 excludes by
name because binary single-annotator labels cannot support graded gain; the
unanswerable false-trigger rate, which needs a frozen confidence threshold and
the deterministic answer path, neither of which exists; and the cross-reference
expansion hit rate, whose denominator is an annotation mark no field carries.

Raw output, per item: `artifacts/restricted/eval-retrieval-dev-2026-08-09.json`
(not committed).

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

---

### Task 9 (blocker found while wiring the slice): the RFC corpus cannot be authorized

`answer/run.py` drives the L1 path — reserve, send, record, verify — and its
tests pass against a mock transport and a fake ledger. It cannot run live, and
the reason is not a missing key or a stopped service.

**Both frozen RFC manifests are `cloud_egress_authorized=false` with
`provider_route_binding=null`, and `ManifestStore.create_successor` refuses any
predecessor that is not v1.** §3.2 makes source manifests default-deny and
authorization a recorded compliance decision expressed as a successor. That
successor path exists only for the DOCX family. So the RFC corpus — the only
corpus this project has — has no code path by which it could ever be authorized
to send.

This is why the slice's tests exercise the v1 family: it is the only one that
can hold an authorization today. The chain itself is family-blind, matching
`SourceManifestResolver`'s rule that authorization is a property of the
compliance decision rather than of the file format.

Owed, in order:

1. `create_source_v2`'s successor path, so an RFC manifest can carry a route
   binding and a compliance conclusion.
2. A recorded §3.2 assessment for RFC 9110 and 9112 against the main route —
   the IETF TLP terms, the provider's retention policy, and the per-document
   outbound caps already measured at one-fifth.
3. Only then the live run.

Until (1) and (2), `specpilot answer` against the real corpus fails closed at
`build_request`, which is the correct behaviour and not a bug to route around.

#### Task 9 resolved — the RFC corpus is authorized

**9.1** `create_successor_v2`. **9.2** the §3.2 assessment, researched by the
author on 2026-08-10 and recorded in both RFC manifests (local, gitignored).

Authorized for the online main route, expiring **2026-11-08**. Successors:
`ietf-rfc-9110 → c42813e7…`, `ietf-rfc-9112 → b74abd04…`.

**Researching the licence found a defect before it authorized anything, which is
the argument for doing it in that order.** TLP 5.0 §3 requires an excerpt to
identify its source. The outbound payload rendered `Evidence <hash[:12]>:
<quote>` — `document_id` and `document_version` sat on the payload object and
were never rendered into the bytes that left. The enforcer was satisfied and the
licence was not, and from inside the system those look identical: every cap
held, every disclosure was priced, and the quote left unattributed. Fixed and
tested before the decision was recorded.

Two TLP conditions turned out to be already met, one by accident worth naming:
non-code content may not be made into derivative works on TLP authority alone,
and §8.1's rule that no committable record may hold clause prose means no RFC
text is reproduced downstream. A rule written for evaluation hygiene happens to
discharge a licence condition.

**What is disclosed about the provider, as found:** trains on inputs with no
default opt-out (opt-out by request only); no unified retention period published
for API request data or logs; no complete named subprocessor list; data
processed in the PRC. The material sent is published specification text rather
than confidential or personal data, which is why these are disclosed rather than
disqualifying — **disclosed, not waived**, and they belong in §12.3's appendix.

**Two recorded uncertainties, which are the honest part:** whether sending
excerpts to a model API acting as a confidential processor is "publish",
"display" or "distribute" under TLP is undefined and may vary by jurisdiction —
the caps and the attribution line are set so the answer does not change the
outcome, which is not the same as having answered it. And retention is the least
determined of the four provider facts.

**The premise this decision rests on, recorded so it is re-checkable:** caps
below one fifth on every dimension. RFC 9110 — 314 / 18,412 / 76,113 against
1,571 / 92,064 / 380,569. RFC 9112 — 70 / 3,943 / 16,069 against 351 / 19,717 /
80,345. Raising `corpus_document_unique` above one fifth invalidates the premise
and the decision must be made again rather than inherited.

---

### Task 10 (open, needs a decision): the freeze gate and the runtime gate count differently

The live command refuses every real question with `excerpt_tokens_exceeded`.
Measured over the frozen corpus:

| | clauses over the 512 cap |
| --- | --- |
| `corpus qa`'s `excerpt_fit`, real BGE-M3 tokenizer | **0 of 1,907** |
| the runtime gate, `ByteUpperBoundCounter` | **160 of 1,907 (8.4%)** |

Worst clause: 952 bytes against 252 real tokens. So the corpus passes its own
blocking QA and is then structurally unable to answer from 8% of itself, and
nothing reports the disagreement because each gate is internally consistent.

`ByteUpperBoundCounter` is not wrong as a bound — a byte-level BPE never emits
more tokens than the text has UTF-8 bytes. It is wrong *against this cap*, which
was calibrated with the real tokenizer, and it is loose by about four times.

**The obvious fix does not work, and the reason is worth keeping.** Swapping in
the corpus's tokenizer produces `token_counter_incompatible`: the enforcer
requires `counter.provider_id == route.provider_id` and
`counter.model_id == request.model_id`. That check is correct — counting with
one model's tokenizer and sending to another records a number describing
nothing — so the two requirements genuinely conflict and one of them has to give.

Three ways out, none free:

1. **Treat bytes as the only real control and drop the token cap to match.**
   §12.3 already says bytes are load-bearing because they are exact and
   tokenizer-independent; tokens are secondary. Under a byte-bound counter the
   token cap is then a second, stricter, badly-scaled byte cap wearing a
   token's name. Cheapest and most honest about what is actually measured;
   costs the token dimension as an independent control.
2. **Obtain the provider's tokenizer** and count with it. Makes the number mean
   what it claims, and adds a per-provider dependency plus a new way for the
   freeze gate and the runtime gate to drift apart again.
3. **Recalibrate the excerpt token cap in byte-upper-bound units.** Keeps both
   gates and both counters; the cap stops corresponding to any real token count,
   so §12.3's appendix would have to say so plainly.

Recommendation is (1), because it makes the disclosure control exactly the thing
that is exactly measurable, and because §12.3 already commits to bytes being
load-bearing — but it changes a shipped policy file and the report's cap table,
so it is the author's call rather than a fix to apply quietly.

Until this is decided the answer path runs end to end and refuses at the gate,
which is correct behaviour for a system whose caps disagree with themselves.

#### Task 10 resolved — bytes are the control

Option (1). Every cap vector's `tokens` is now set equal to its `bytes`, so the
token check can never fire before the byte check. `corpus qa`'s `excerpt_fit`
and the runtime gate now agree, which is the property that was missing: what QA
clears at freeze time is what the enforcer prices at run time.

**The licence premise survives on the load-bearing dimension.** Caps are still
exactly one fifth: RFC 9110 76,113 / 380,569 = 20.0%, RFC 9112 16,069 / 80,345 =
20.0%. The recorded §3.2 assessment already named bytes as load-bearing because
they are exact and tokenizer-independent, so nothing in it needs restating.

Consequences worth naming rather than absorbing:

- **The token dimension is no longer an independent control.** The one-fifth
  guard test now asserts bytes and excerpts against the measurement and asserts
  `cap.tokens == cap.bytes` as an invariant, so a future edit that reintroduces a
  stricter token cap fails rather than silently refusing 8% of the corpus again.
- **Refusals now name bytes.** `excerpt_tokens_exceeded` became
  `excerpt_bytes_exceeded` and so on across the scopes, because bytes are what
  bind. The tests were updated to expect the dimension that actually stopped the
  request.
- **"One token over" and "one byte over" are the same case.** The duplicated
  parametrizations collapsed to one, with a note, rather than being left as two
  cases that look like two independent guards over one measurement.
- **The corpus ledger for a frozen corpus is bound to one policy.** Changing the
  caps makes the existing row unusable — `policy_snapshot_mismatch` — which is
  correct: a total spanning two cap sets describes neither. Re-opening the
  accounting for a corpus after a cap change is a deliberate act, not a
  migration to run quietly.

#### Task 11 (open): a corpus ledger bound to one policy has no way to be rebound

Changing the caps made the corpus ledger row for `1abafff7…` unusable —
`policy_snapshot_mismatch`, which is correct. Deleting it is refused too:
`egress_reservation.corpus_manifest_id` has a foreign key onto it, so the row
that records the accounting cannot be removed while anything that consumed it
exists.

Both behaviours are right on their own and together they are a dead end. The
system can enter a state — caps changed on a corpus that has usage — with no
exit that does not destroy audit rows. That is the third gap of this shape found
this session, after the RFC family having no authorization path and the two
gates counting differently: **a rule enforced in one direction with no
corresponding path in the other.**

The right fix matches what this project does everywhere else: immutable
successors rather than mutation. `egress_corpus_ledger` is a single mutable row
keyed by `corpus_manifest_id`, which is the anomaly — every manifest here is
content-addressed and superseded rather than edited. Reopening accounting after
a deliberate cap change should write a successor row carrying the new
`policy_hash` and a pointer to the one it supersedes, leaving the old totals
readable and attributable to the policy they were accumulated under.

Until that exists, a cap change on a corpus with recorded usage requires
rebuilding the ledger database, which is acceptable only because every row in it
today is from this session's own failed test sends.
