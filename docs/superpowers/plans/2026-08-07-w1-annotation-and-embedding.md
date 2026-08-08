# W1: Annotation Workflow and Embedding Throughput Implementation Plan

> **Supersession note — 2026-08-08:** Gold provenance v2 supersedes this
> plan's `IndependentPath` admission gate. The current contract records ordered
> human, model, and retrieval origins for audit while preserving source checks,
> add-only pooling, and time locking. This historical plan is otherwise
> unchanged.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the author everything needed to annotate gold against the frozen
RFC corpus without contaminating it, and measure real local embedding
throughput — so that W2's retrieval work starts from measured numbers and a
protocol that cannot quietly be violated.

**Why this shape:** Product plan §12 calls W1 the annotation-heavy week: the
engineering is light because safety and the gate moved into W0, and the largest
single work item in the whole project is the author's own gold annotation.
Tasks 1–5 build scaffolding and the gate. Task 6 is the author's, and no tool
in this repository can do it for them.

**Architecture:** A clause model sits on top of R0's extracted sections and
resolves the unit mismatch — the source numbers sections while the outbound caps
count tokens. Annotation records are typed so that §8.1's committable-field rule
is mechanically enforced rather than remembered. Embedding throughput is
measured on a sample and cached by content, with no weights in Git.

**Tech Stack:** Python 3.12+, Pydantic 2, pytest, Hypothesis, Ruff, mypy.
BGE-M3 via a local runtime for Task 5 only; no model weights enter the
repository and no new runtime dependency is added for Tasks 1–4.

## Global Constraints

- **§8.2.1's independent path, translated for RFC.** Initial gold may come only
  from paths unrelated to the system's retriever: navigating the frozen `.txt`
  rendition or the raw `.xml`, literal string search over the frozen bytes,
  manual cross-reference following, and the document's own terminology. The
  product plan said "the original `.docx`, not the system's `get_toc`", and the
  reason survives the corpus change: **the source renditions are permitted, the
  system's parse output is not.** R0 froze `.txt` as the human-checkable
  rendition precisely for this. `extract_structure` output, `search_clauses`,
  and any dense, sparse, or hybrid ranking are forbidden as gold sources.
- Retrieval may propose candidates during §8.2.3 completeness audit only. The
  author adjudicates against the source, existing gold is never deleted, and no
  gold is created by pooling alone.
- **Clause text never enters a committable record.** §8.1's field rule is
  enforced by the contract: an annotation record that can hold a quotation is a
  bug, not a policy question. Question text, clause IDs, section paths,
  versions, refusal labels, and derived verdict labels are committable; clause
  prose and quotations are not.
- Key scoring points may carry necessary factual values (timer defaults, state
  names, parameter ranges) but must not reproduce clause wording as sentences.
- No quality metric is produced or reported in W1. Throughput is a cost
  measurement, not a quality one.
- No provider is called. Nothing in W1 reaches the enforcer, ledger, or
  transport, and no successor manifest is created.
- No wall-clock promise about full-corpus encoding is written down before Task 5
  measures it, per product plan §7.
- The archive and OOXML boundary stays untouched, limits included.

## Target volumes

From product plan §8.1, with W1's share from §12's week table (all dev plus
about half of locked):

| Set | Total | dev | locked | W1 target |
|---|---|---|---|---|
| L1 clause QA | 40 | 15 | 25 | 15 dev + ~12 locked |
| L2 conformance | 20 | 8 (3/3/2) | 12 (4/4/4) | 8 dev + ~6 locked |

L2-adv is W3's and is not started here.

## File map locked for W1

- `src/specpilot/corpus/clauses.py` — clause model over R0 sections.
- `src/specpilot/contracts/annotation.py` — L1/L2 records and independent-path
  provenance.
- `src/specpilot/annotation/store.py` — create-only annotation storage.
- `src/specpilot/annotation/progress.py` — countable progress without prose.
- `src/specpilot/embedding/throughput.py` — sampled measurement and cache key.
- `src/specpilot/embedding/local_encoder.py` — the lazy, optional model load,
  kept out of `throughput.py` so the timing harness imports without a runtime.
- `src/specpilot/corpus/overlap.py` — the frozen literal-overlap measure.
- `src/specpilot/cli.py` — `corpus parse`, `corpus clauses`, `corpus overlap`,
  `annotation progress`, `annotation template`, `annotation add`,
  `embedding measure`.
- `tests/unit/corpus/`, `tests/unit/annotation/`, `tests/unit/embedding/`.

---

### Task 1: The citable unit

**Files:**
- Create: `src/specpilot/corpus/clauses.py`
- Test: `tests/unit/corpus/test_clauses.py`

**Interfaces:**
- Produces: `Clause` with `clause_id`, `section_number`, `section_path`,
  `anchor`, and a token/paragraph span, plus
  `build_clauses(structure, text) -> tuple[Clause, ...]`.
- Consumes: R0's `RfcStructure` and the frozen text rendition.

**The decision this task exists to make.** A section is what the source numbers
and what a citation names; a token span is what the outbound caps count. RFC
9110 §3.7-scale sections exceed the 512-token excerpt cap on their own, so a
section cannot be the unit that leaves the machine, and a token window cannot
be the unit a citation names. The clause carries both: a stable citable
identity from the section, and a bounded span that the enforcer can price.

- [x] **Step 1: Write failing tests for identity and boundedness**

Assert a clause ID is stable across two builds of the same document and changes
when the section number changes; that every clause's span is within the excerpt
cap; that a section longer than the cap yields several clauses sharing one
section number with distinct ordinals; and that clause IDs never collide.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement the clause model**

Derive the citable identity from `document_id`, `document_version`, and section
number. Split only at paragraph boundaries the source already defines — the `pn`
anchors R0 resolves — so a clause never begins mid-sentence. Record the parent
section for every clause so parent-child chunking in W2 has a real parent.

- [x] **Step 4: Verify GREEN against the frozen corpus**

Build clauses for RFC 9110 and 9112 and record counts, the longest clause, and
how many sections needed splitting. Numbers go in the task report, not in Git.

---

### Task 2: Annotation records that cannot hold a quotation

**Files:**
- Create: `src/specpilot/contracts/annotation.py`
- Create: `src/specpilot/annotation/store.py`
- Test: `tests/unit/annotation/test_annotation_contracts.py`

**Interfaces:**
- Produces: `L1Annotation`, `L2Annotation`, `IndependentPath`,
  `AnnotationStore.create(...)`.
- Preserves: the create-only, content-addressed, `0600` storage discipline W0
  established for manifests.

- [x] **Step 1: Write failing committable-field tests**

Assert the contract forbids extra fields; that `L1Annotation` holds question,
key scoring points, clause IDs, section paths, document version, and an
expected-refusal label, and has no field capable of holding clause prose; that
`L2Annotation` additionally holds `claim_id`, `expected_verdict`, and the
Verifier pair's `proposed_verdict` / `supports_verdict` as separate fields that
cannot substitute for one another; and that every record names the
`IndependentPath` its initial gold came from.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement the contracts and the store**

`IndependentPath` is an enum of exactly the §8.2.1 permitted paths. There is no
value for retrieval, so a record whose gold came from the retriever cannot be
represented — which is the point.

- [x] **Step 4: Write and pass the pooling-audit invariants**

A completeness-audit addition records the candidate's origin and the author's
adjudication, and the store refuses any update that removes an existing gold
clause ID.

---

### Task 3: One command parses one specification

**Files:**
- Modify: `src/specpilot/cli.py`
- Test: `tests/cli/test_corpus_parse.py`

**Interfaces:**
- Produces: `specpilot corpus parse --manifest <id> --manifest-dir <dir>`,
  emitting counts and identities as JSON on stdout.

This is W1's hard gate from product plan §12: *one specification parses with one
command.*

- [x] **Step 1: Write failing CLI tests**

Assert the command resolves a stored v2 manifest, verifies the document hash
against the manifest before parsing, and prints section, clause, and
cross-reference counts with no clause text. Assert a hash mismatch refuses with
a stable code and parses nothing.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement, reusing R0's boundary rather than re-parsing loosely**

- [x] **Step 4: Verify GREEN on both frozen sources**

---

### Task 4: Countable annotation progress

**Files:**
- Create: `src/specpilot/annotation/progress.py`
- Modify: `src/specpilot/cli.py`
- Test: `tests/unit/annotation/test_progress.py`

**Interfaces:**
- Produces: `specpilot annotation progress --annotation-dir <dir>`.

The other half of W1's gate: *progress is checkable — completed counts, source
paths, and adjudication records all present.*

- [x] **Step 1: Write failing progress tests**

Assert the report gives per-set completed counts against §8.1 targets, the
distribution of independent paths used, the clause-first / scenario-first split
against the 60/40 target, the unanswerable count against the 20% requirement and
the dev 3 / test 5 and dev 2 / test 4 floors, and how many records are missing
an adjudication record. Assert no question or clause text appears in the output.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and verify GREEN**

---

### Task 5: Measured local embedding throughput

**Files:**
- Create: `src/specpilot/embedding/throughput.py`
- Test: `tests/unit/embedding/test_throughput.py`

**Interfaces:**
- Produces: `embedding_cache_key(weights_sha256, pipeline_version, text_sha256)`
  and a sampled measurement command.

- [x] **Step 1: Write failing cache-key and estimate tests**

Assert the cache key changes when any of the three inputs changes, so a
re-chunk or a weight swap cannot silently reuse stale vectors. Assert the
full-corpus estimate is derived from the measured rate and the real clause
count, and that requesting an estimate without a measurement raises rather than
guessing.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement measurement over a sample**

Measure MPS and CPU separately on the target machine. Record weight hash,
device, batch size, sample size, and observed rate. No model weights, vectors,
or clause text enter Git.

- [x] **Step 4: Record the measurement and derive the estimate**

Only now may a full-corpus encoding time be written down anywhere, per §7.
Recorded in `../../reports/w1-embedding-throughput.md`: **40–100 s for the whole
1909-clause corpus on this machine**, measured end to end rather than
extrapolated. What to batch by turned out to matter where how many did not:
grouping by token count cuts padding waste from 61.6% to 4.3%, grouping by word
count only reaches 33.9% once the corpus mixes prose with grammar, and batch
size between 8 and 32 is indistinguishable once runs are interleaved.

---

### Task 5a: The entry path Task 6 needs — ADDED 2026-08-08

**Files:**
- Create: `src/specpilot/corpus/overlap.py`
- Modify: `src/specpilot/cli.py`
- Test: `tests/unit/corpus/test_overlap.py`, `tests/cli/test_annotation_entry.py`

**Why this was missing.** Tasks 1–5 built everything that reads or checks
annotations and nothing that writes one. Standing at the start of Task 6, the
author has no command that returns a `clause_id`, so `gold_clause_ids` cannot be
filled in at all; no command that stores a record, so each of the 23 would be
hand-written Python; and no way to obtain `question_gold_jaccard`, which the
contract requires on every answerable item. All three are mechanical — counting,
shape validation, and one arithmetic measure — and none of them originates a
question, chooses a gold clause, or decides a verdict, so all three belong to
tooling under Task 6's own rule.

- [x] **Step 1: Write failing tests for the index, the entry, and the overlap**

Assert `corpus clauses` lists clause IDs with their section numbers and paths and
no clause text; that `annotation add` refuses a record the contract rejects and
stores one it accepts; and that the overlap measure is stable, order-independent,
and takes the maximum over gold clauses rather than the union.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and verify GREEN on the frozen corpus**

**The decision this task exists to make.** For a multi-gold item, overlap over
the *union* of gold clauses falls as gold is added, so an item would look less
literal purely for being better annotated. Section 8.2.2 wants to separate
"semantic retrieval worked" from "literal matching got lucky", and luck needs
only one clause to match. The measure is therefore the maximum over gold
clauses, and it is frozen here because W6 reports Macro-Recall stratified by it.

---

### Task 6: The author's gold annotation — OWNER: the author

**Deliberately left unchecked, like W0 Task 8 Step 4.** This is the largest
single work item in the project and the one no tool here may perform. Tooling
may count records, validate their shape, check protocol invariants, and report
progress; it may not originate a question, choose a gold clause, or decide a
verdict.

- [ ] **Step 1: Annotate to the W1 targets using only §8.2.1 paths** — the
  author's own work, with each record naming the path its gold came from.
  Operating procedure: `../../runbooks/w1-annotation.md`.

Mixed question directions per §8.2.2: about 60% clause-first with the clause
title's full wording deliberately not reused, about 40% scenario-first.
Unanswerable cases are constructed on purpose to the target count, not harvested
from scenario-first failures. Every answerable item records the token-level
Jaccard overlap between question and gold clause, so W6 can report Macro-Recall
stratified by literal overlap.

---

## Plan self-review record

- **Scope decision:** W1 covers the annotation scaffolding, the parse gate, and
  the throughput measurement. Chunking strategy, Qdrant, BM25, RRF, and pooling
  belong to W2 and are not started here.
- **Independent-path translation:** §8.2.1's rule is preserved in substance —
  source renditions permitted, system parse output forbidden — rather than
  copied literally from a DOCX-shaped sentence.
- **Enforcement over convention:** the committable-field rule and the
  independent-path rule are both expressed as contracts that cannot represent
  the forbidden state, rather than as documentation a tired annotator must
  remember at 1 a.m.
- **Type consistency:** R0's `RfcStructure` feeds Task 1's clauses; clauses feed
  Task 3's CLI and Task 5's estimate; annotations reference clause IDs and never
  clause text.
- **Placeholder scan:** every implementation step names concrete behaviour,
  files, and verification. Task 6 is marked as the author's and is not something
  tooling completes.
