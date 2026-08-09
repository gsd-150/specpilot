# L1 Pooling Completeness Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute one immutable BM25-only plus dense-only completeness audit over the 20 existing L1 annotations, recording an adjudication for every item and appending any human-confirmed missing gold.

**Architecture:** A new `retrieval.pooling` module owns immutable candidate/run/decision contracts and private create-only storage. The CLI registers independently produced route rankings once, then resolves the stored candidates locally during a resumable human review. `AnnotationStore.amend` remains the only path that writes adjudication successors.

**Tech Stack:** Python 3.12+, Pydantic 2, argparse, pytest, existing BM25/BGE-M3/Qdrant routes, content-addressed JSON stores.

## Global Constraints

- Cover exactly the current 20 L1 item IDs in a registered order.
- Pool only BM25 top 5 and dense top 5; refuse hybrid/RRF/fused input.
- Retrieval proposes; only the author chooses an adjudication.
- Never remove gold. Extensions are successor annotations.
- Registration is one-time; resume never reruns retrieval.
- Stored audit records contain locators and hashes, never clause text.
- Production records stay ignored under `artifacts/restricted/` with directory mode `0700` and file mode `0600`.
- Never compute or print retrieval or answer-quality metrics.

---

### Task 1: Immutable pooling protocol and store

**Files:**
- Create: `src/specpilot/retrieval/pooling.py`
- Create: `tests/unit/retrieval/test_pooling.py`

**Interfaces:**
- Consumes: `RouteRanking`, unit metadata/content hashes, annotation heads.
- Produces: `PoolingCandidate`, `PoolingItem`, `PoolingRun`, `PoolingOutcome`, `PoolingDecision`, `PoolingStore`, `build_pool(...)`, `seal_run(...)`.

- [ ] **Step 1: Write failing candidate-union tests**

```python
def test_pool_is_the_union_of_independent_top_five_routes():
    bm25 = RouteRanking(route="bm25", unit_ids=("a", "b", "c"))
    dense = RouteRanking(route="dense", unit_ids=("b", "d"))
    pool = build_pool(bm25, dense, units=unit_facts("a", "b", "c", "d"))
    assert tuple(candidate.unit_id for candidate in pool) == ("a", "b", "c", "d")
    assert pool[1].route_ranks == {"bm25": 2, "dense": 1}
```

Also prove that more than five IDs per route, unknown unit IDs, duplicate route
inputs, and any route set other than exactly `{"bm25", "dense"}` are refused.
A `FusedRanking` must be unrepresentable as an accepted input.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/retrieval/test_pooling.py -q`

Expected: collection fails because `specpilot.retrieval.pooling` does not exist.

- [ ] **Step 3: Implement the minimal candidate contracts and union**

```python
class PoolingOutcome(StrEnum):
    GOLD_COMPLETE = "gold_complete"
    GOLD_EXTENDED = "gold_extended"
    AUDIT_BLOCKED = "audit_blocked"

class PoolingCandidate(_FrozenModel):
    unit_id: Sha256
    document_id: Identifier
    document_version: Identifier
    section_number: str | None
    section_path: SectionPath
    content_sha256: Sha256
    route_ranks: dict[Literal["bm25", "dense"], int]
```

Keep route scores out: the run freezes membership and rank, not incomparable
BM25 and cosine values.

- [ ] **Step 4: Write failing run/decision/sealing tests**

The run binds ordered items, annotation heads, source manifests, BM25
fingerprint, dense collection, model-weight hash, vector schema/point count,
top-k, candidates, author, and creation time. Tests prove:

```python
def test_run_cannot_seal_with_unadjudicated_items():
    with pytest.raises(ValueError, match="unadjudicated"):
        seal_run(run, decisions=())

def test_blocked_decision_prevents_sealing():
    with pytest.raises(ValueError, match="blocked"):
        seal_run(run, decisions=(blocked_decision(run.items[0]),))
```

`gold_extended` requires at least one registered candidate;
`gold_complete`/`audit_blocked` require none. A decision binds the run ID and
registered annotation head, and becomes applied only when it names the written
successor annotation ID.

- [ ] **Step 5: Verify RED, implement validation, and verify GREEN**

Run the focused suite before and after implementation. The first run must fail
on missing behavior; the second must pass.

- [ ] **Step 6: Write failing create-only store tests**

```python
def test_registration_is_create_only(tmp_path):
    store = PoolingStore(tmp_path)
    stored = store.create_run(run())
    assert store.create_run(run()) == stored
    with pytest.raises(ValueError, match="already registered"):
        store.create_run(changed_candidates_same_item_set())
```

Also assert content-ID verification, safe replay, tamper refusal, maximum record
size, non-standard JSON refusal, and `0700`/`0600` permissions.

- [ ] **Step 7: Implement storage, verify Task 1, and commit**

Use `canonical_json`, `canonical_sha256`, and
`O_CREAT | O_EXCL | O_NOFOLLOW`. Then run the focused suite and commit:

```bash
git add src/specpilot/retrieval/pooling.py tests/unit/retrieval/test_pooling.py
git commit -m "feat: register immutable pooling runs"
```

---

### Task 2: Apply decisions through annotation successors

**Files:**
- Modify: `src/specpilot/annotation/store.py`
- Modify: `src/specpilot/retrieval/pooling.py`
- Modify: `tests/unit/annotation/test_annotation_contracts.py`
- Modify: `tests/unit/retrieval/test_pooling.py`

**Interfaces:**
- Produces: `apply_decision(pool_store, annotation_store, run, decision, local_units) -> Annotation`.

- [ ] **Step 1: Write a failing no-change adjudication test**

```python
def test_gold_complete_creates_an_adjudication_only_successor(tmp_path):
    successor = apply_decision(..., decision=gold_complete(...))
    assert successor.gold_clause_ids == root.gold_clause_ids
    assert successor.predecessor_annotation_id == root.annotation_id
    assert successor.adjudications[-1].candidate_origin == "pooling"
```

This successor is what clears `awaiting_adjudication` when no gold is added.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/retrieval/test_pooling.py tests/unit/annotation/test_annotation_contracts.py -q`

- [ ] **Step 3: Write failing extension tests**

An extension retains old gold, accepts only registered selected candidates,
derives paths from registered facts, appends concrete `bm25_retrieval` and/or
`dense_retrieval` origins followed by `human_source_review`, recomputes
`question_gold_jaccard`, and refuses duplicates or unregistered candidates.
The decision is stored before amendment and remains unapplied if amendment
writing fails.

- [ ] **Step 4: Implement minimal decision application**

Add an optional `question_gold_jaccard` argument to `AnnotationStore.amend`;
when absent it preserves the previous value. Pooling calculates it from local
texts before calling the store. No audit record may hold those texts.

- [ ] **Step 5: Verify Task 2 GREEN and commit**

```bash
.venv/bin/python -m pytest tests/unit/retrieval/test_pooling.py tests/unit/annotation/test_annotation_contracts.py tests/unit/annotation/test_progress.py -q
git add src/specpilot/annotation/store.py src/specpilot/retrieval/pooling.py tests/unit/annotation/test_annotation_contracts.py tests/unit/retrieval/test_pooling.py
git commit -m "feat: apply add-only pooling adjudications"
```

---

### Task 3: Registration and resumable local review CLI

**Files:**
- Modify: `src/specpilot/cli.py`
- Create: `tests/cli/test_annotation_pooling.py`

**Interfaces:**
- Produces: `specpilot annotation pool-register`, `pool-review`, `pool-status`.
- Uses: frozen-source loading, `LocalCorpus`, `Bm25Index`, local encoder,
  `DenseIndex.open`, `PoolingStore`, and `AnnotationStore`.

- [ ] **Step 1: Write failing registration CLI tests**

Use fixture sources, a fake local encoder, and fake dense client through
dependency injection. Assert registration selects L1 heads only, fixes their
order, builds BM25 once, queries dense once per question, records exactly top 5
per independent route, and emits only IDs/counts/configuration hashes. It must
refuse changed candidates for an existing item set and never print source text
or a quality metric.

- [ ] **Step 2: Run registration tests and verify RED**

Run: `.venv/bin/python -m pytest tests/cli/test_annotation_pooling.py -q`

- [ ] **Step 3: Implement `pool-register`**

Required arguments:

```text
--annotation-dir --pool-dir --manifest-dir
--manifest MANIFEST_ID (repeatable)
--xml PATH (repeatable and paired by position)
--model-dir --model-id --device
--qdrant-url --collection --weights-sha256
--author-id --created-at
```

Verify every source snapshot before loading the model or Qdrant. Validate
vector size and point count. Never create, delete, or overwrite a collection.

- [ ] **Step 4: Write failing interactive review tests**

The sheet displays current gold and registered candidates under stable letters.
Input accepts `complete`, comma-separated candidate letters, or `blocked`.
Assert stop/resume, stale-head refusal, unanswerable completion, multi-clause
extension, hash mismatch refusal, and no retrieval calls during resume.

- [ ] **Step 5: Implement `pool-review` and `pool-status`**

Resolve displayed text from `LocalCorpus`, verify registered content hashes,
measure elapsed time, store the decision, apply it, and advance. EOF exits
without sealing. Machine-readable output remains aggregate-only.

- [ ] **Step 6: Verify Task 3 GREEN and commit**

```bash
.venv/bin/python -m pytest tests/cli/test_annotation_pooling.py tests/unit/retrieval/test_pooling.py -q
git add src/specpilot/cli.py tests/cli/test_annotation_pooling.py
git commit -m "feat: run resumable l1 pooling review"
```

---

### Task 4: Progress integration and documentation

**Files:**
- Modify: `src/specpilot/annotation/progress.py`
- Modify: `src/specpilot/cli.py`
- Modify: `tests/unit/annotation/test_progress.py`
- Modify: `tests/cli/test_annotation_progress.py`
- Modify: `docs/runbooks/w1-annotation.md`
- Modify: `docs/superpowers/plans/2026-08-08-w2-corpus-and-retrieval.md`

**Interfaces:**
- Extends `annotation progress` with optional `--pool-dir` aggregate reporting.

- [ ] **Step 1: Write failing progress tests**

Assert the report carries registered/adjudicated counts, outcome counts, added
gold count, sealed state, and run ID. Assert it contains no question, clause
text, route score, Recall, MRR, Hit@k, or accuracy key.

- [ ] **Step 2: Verify RED, implement aggregate reporting, verify GREEN**

Run focused progress and pooling suites before and after implementation.

- [ ] **Step 3: Update runbook and W2 checklist**

Document exact commands, the one-time boundary, resumption, and author-owned
choices. Check W2 Task 7 Steps 1–3 only after code/tests pass. Leave Step 4
unchecked until the production run seals.

- [ ] **Step 4: Run full verification and commit**

```bash
make check
.venv/bin/python -m pytest -q
git add src/specpilot/annotation/progress.py src/specpilot/cli.py tests/unit/annotation/test_progress.py tests/cli/test_annotation_progress.py docs/runbooks/w1-annotation.md docs/superpowers/plans/2026-08-08-w2-corpus-and-retrieval.md
git commit -m "docs: make pooling completion auditable"
```

Report PostgreSQL/Qdrant skips as missing integration evidence.

---

### Task 5: Execute the production audit — OWNER: the author

**Files:**
- Create, restricted/ignored: `artifacts/restricted/pooling/`
- Modify through successors, restricted/ignored: `artifacts/restricted/annotations/`
- Create: sanitized aggregate report under `docs/reports/`

**Interfaces:**
- Consumes frozen RFC snapshots, local BGE-M3 weights, and frozen Qdrant collection.
- Produces one sealed 20-item run and zero L1 items awaiting adjudication.

- [ ] **Step 1: Start or verify Qdrant and validate the collection**

Do not create or delete a collection during the audit. Refuse identity, schema,
or point-count mismatches.

- [ ] **Step 2: Register the production run once**

Confirm it names 20 items without printing source text.

- [ ] **Step 3: Present all items and record author decisions**

The agent may operate the CLI and explain candidates. The author supplies every
choice. For `l1-dev-010`, present existing paragraph 3 and candidate paragraph
2; never append paragraph 2 automatically.

- [ ] **Step 4: Seal and verify**

Required evidence: L1 `awaiting_adjudication=0`, pooling 20/20, blocked 0,
sealed run, valid successor chains, and `0700`/`0600` permissions.

- [ ] **Step 5: Commit sanitized documentation only**

Record counts, timing, disagreements, added-gold count, and the consecutive-
paragraph rule without questions, clause text, or candidate pools.

## Plan self-review

- **Coverage:** registration, independent union, one-time resume, three
  outcomes, add-only amendments, progress, production execution, and
  `l1-dev-010` each have an owning task.
- **Types:** existing `RouteRanking`, `GoldOrigin`, `AnnotationStore.amend`,
  `LocalCorpus`, `Bm25Index`, and `DenseIndex` names are preserved.
- **Scope:** corpus-manifest implementation and quality evaluation remain out
  of scope; this audit record may not substitute for an evaluation manifest.
- **Human boundary:** no code path auto-selects gold. Every production
  adjudication remains the author's act.
