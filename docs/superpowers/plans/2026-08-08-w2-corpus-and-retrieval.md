# W2: Frozen Corpus and Retrieval Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put both frozen RFCs into a versioned, verifiable index behind an
immutable `corpus_manifest`, with two independent retrieval routes, so that W3's
agent chain retrieves from something whose identity can be checked at load time
— and so §8.2.3's pooling has the two routes it requires.

**Why this shape:** Product plan §12 calls W2 the heaviest engineering week and
says annotation gives way to it. The gate is *ingestion and immutability*, not
quality: no quality metric is computed in W2, and the locked queries are run
once, pooling-only, with candidates and adjudications sealed.

**Tech Stack:** Python 3.12+, Pydantic 2, pytest, Hypothesis, Ruff, mypy,
Qdrant 1.12.4 via Compose, BGE-M3 through the existing optional `embedding`
extra. BM25 is a local pure-statistical implementation with no model.

## What W1 already settled

- Clauses, their identities, and their section paths — 1559 in RFC 9110, 350 in
  RFC 9112, every one under 252 tokens.
- BCP 14 attribution: 445 of 445 and 149 of 149 keywords belong to a clause.
- Measured encoding cost: 40–100 s for the whole corpus, batches grouped by
  token count.
- The annotation contract, store, entry path, and progress report.

Three of those figures moved while W2 Task 1 was being built, because building
it measured things W1 had not. Cross-reference text was being dropped wherever
the source rendered it from an attribute; grammar blocks the source numbers as
paragraphs were in no clause at all; and the batch sort key was words, which
stopped tracking tokens the moment grammar joined the corpus. All three are
fixed, and the numbers above are the ones after.

## §4.1 translated for RFC, and where the translation changes the work

The product plan's pipeline section is written for 3GPP DOCX. Four of its
clauses mean something different here, and saying so is part of the work rather
than a licence to skip it.

1. **The 7168-token child-chunk rule never fires.** Measured: the longest clause
   is 208 tokens in RFC 9110 and 220 in RFC 9112. §4.1.3's overlap windows exist
   because a 3GPP clause is a whole numbered subsection; an RFC clause is a
   paragraph. The parent-child structure §4.1.3 asks for is already present as
   section → clause, and it splits at boundaries the document published rather
   than at a 256-token window, which cannot begin mid-sentence. **Implementing
   sliding-window chunking here would add a code path nothing in this corpus can
   reach.** It is not implemented; this paragraph is the record of why.
2. **§4.1.4's TR 21.801 modal tagging becomes BCP 14.** Already built, and
   stronger than the original: RFC v3 tags every normative keyword as `<bcp14>`,
   so nothing is inferred from capitalisation. Declarative `is`/`are` statements
   remain unlabelled, exactly as §4.1.4 requires.
3. **§4.1.6's tokenizer warning transfers with new terms.** Clause numbers
   (`5.6.2`), field names (`Content-Length`), method names, status codes, RFC
   references, and ABNF rule names are this corpus's high-signal terms, and
   whitespace-and-punctuation splitting destroys all of them. **BM25 must not
   reuse `corpus/overlap.py`'s tokenizer**, which shatters them deliberately:
   crude set overlap is right for a stratification key and wrong for retrieval.
4. **Tables were outside the corpus entirely.** 12 tables in RFC 9110 and 3 in
   RFC 9112, 153 body rows and 796 words, none titled, none stating a
   requirement. W1 excluded `<td>`/`<th>` from clauses, which was right — a cell
   is not a clause — but §4.1's QA gate samples 10 tables, and a question about
   a reference table had nothing to cite. Task 1 gave them a unit.
5. **The QA sample becomes the whole corpus.** §4.1 asks for 20 clauses and 10
   cross-references because a DOCX has to be checked by hand. RFC v3 publishes
   its own section numbering in every `pn` and its own reference targets as
   anchors, so both lines are checked exhaustively instead.

## Global Constraints

- **No quality metric is computed in W2.** Not Macro-Recall, not Hit@5, not MRR.
  The locked queries are run once, pooling-only, per §8.2.4.
- **Pooling uses BM25-only and dense-only top-5, never the hybrid ranking**, and
  the two routes share no representation model (§4.1.6, §8.2.3).
- **BM25 parameters and IDF are frozen before pooling** and recorded in the
  gold metadata, because the online sparse route is the same implementation and
  later tuning would change what pooling did (§8.2.3).
- **Pooling proposes; the author adjudicates.** No gold is created by pooling
  alone, and no existing gold is removed — the store already refuses removal.
- Retrieval output remains a forbidden source of *initial* gold. Nothing in this
  week may write an annotation record.
- The corpus never leaves the machine. No provider is called, and no successor
  source manifest is created.
- Real source text, full indexes, complete clauses, and quotations stay out of
  Git; restricted directories remain `0700` and restricted files `0600`.
- The archive, OOXML, and RFC boundaries stay untouched, limits included.

## File map for W2

- `src/specpilot/corpus/tables.py` — tables as a citable unit.
- `src/specpilot/corpus/qa.py` — §4.1's blocking QA lines, executable.
- `src/specpilot/retrieval/bm25.py` — independent sparse route and tokenizer.
- `src/specpilot/retrieval/dense.py` — Qdrant collection and dense route.
- `src/specpilot/retrieval/hybrid.py` — RRF over the two routes.
- `src/specpilot/retrieval/pooling.py` — candidate pooling and its sealed log.
- `src/specpilot/contracts/corpus_manifest.py` — the frozen corpus identity.
- `src/specpilot/cli.py` — `corpus qa`, `corpus index`, `corpus freeze`,
  `retrieval search`, `retrieval pool`.
- `tests/unit/corpus/`, `tests/unit/retrieval/`, `tests/integration/qdrant/`.

---

### Task 1: Tables as a citable unit

**Files:**
- Create: `src/specpilot/corpus/tables.py`
- Test: `tests/unit/corpus/test_tables.py`

**Interfaces:**
- Produces: `Table` with `table_id`, `section_number`, `section_path`,
  `ordinal`, `row_count`, `column_count`, plus `build_tables(...)` and
  `iter_table_rows(...)`.

A table is cited whole — "the table in §7.3" — so the table is the unit, and
rows are its content. Cells are neither: a cell in isolation names nothing.

- [x] **Step 1: Write failing tests for table identity and shape**

Assert the table ID is stable across builds and distinct per section ordinal;
that row and column counts match the source; that a header row is marked rather
than dropped; and that `Table` has no field that can hold cell text.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and verify GREEN on the frozen corpus**

Record the counts for both documents in the task report, not in Git.

---

### Task 2: The parse QA gate, executable

**Files:**
- Create: `src/specpilot/corpus/qa.py`
- Modify: `src/specpilot/cli.py`
- Test: `tests/unit/corpus/test_qa.py`

**Interfaces:**
- Produces: `specpilot corpus qa --manifest <id> ... [--strict]`, exiting
  non-zero when any blocking line fails.

§4.1's QA paragraph is currently prose. It becomes a command, because a gate
that has to be remembered before freezing is a gate that will be skipped once.

The blocking lines, translated: every top-level section present with zero
missing; clause ID and section path correct on a stratified sample of at least
20 numbered clauses, 10 tables, and 10 cross-references; table text fidelity and
cross-reference target correctness each at least 90%; orphan normative
paragraphs at most 1% of candidate paragraphs; quarantined content at most 2% of
parsed blocks.

- [x] **Step 1: Write failing tests for each blocking line**

Each line gets a case that passes at the threshold and one that fails just
below it. A gate whose failure path is untested is a gate nobody has seen refuse.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement and run against both frozen sources**

Report every line's measured value, not just pass/fail.

**Both documents pass all five lines.** Section numbering 1559/1559 and 350/350
against the source's own `pn`; cross-reference targets 2519/2519 and 458/458;
table fidelity 714/714 and 82/82 words; uncaptured text 0.09% and 0.76% against
the 2% ceiling; orphan normative keywords 0 of 445 and 0 of 149.

---

### Task 3: An independent BM25

**Files:**
- Create: `src/specpilot/retrieval/bm25.py`
- Test: `tests/unit/retrieval/test_bm25.py`

**Interfaces:**
- Produces: `Bm25Tokenizer`, `Bm25Index.build(...)`, `.search(query, k)`, and
  frozen `Bm25Parameters(k1, b)` recorded with the index.

- [x] **Step 1: Write failing tokenizer and ranking tests**

Assert `5.6.2` survives as one term, `Content-Length` as one term (and also
contributes its parts, so a query saying "content length" still matches),
`RFC 9110` as a reference term, and that a status code keeps its digits
together. Assert IDF is computed over the frozen corpus and travels with the
index, and that scores are stable across two builds of the same input.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement, with the parameters frozen at their baseline**

`k1` and `b` take standard baseline values and are recorded, not tuned. §8.2.3
requires the pooling configuration to be reconstructible afterwards.

- [x] **Step 4: Verify GREEN and record term statistics**

1924 units, 4599 distinct terms, built in 0.04 s, mean 39.8 terms per unit.
Every high-signal compound survived: `5.6.2` at df 3, `obs-fold` 8, `206` 27,
`transfer-encoding` 34, `content-length` 49, `http/1.1` 99. Splitting a dotted
number never leaks its digits — `5` appears 4 times in the whole corpus, all of
them bare numerals in prose.

**A section cannot be found by its own number, and that is not a tokenizer
bug.** Searching `5.6.2` reaches the three clauses that *cite* §5.6.2, not §5.6.2
itself, because a section number is a locator and never appears in its own body
text. Whether the number and section path join a unit's indexable text is a
corpus-level decision that both routes must share, so it is settled in Task 4
where that text is assembled — not here, where the index is deliberately
agnostic about what it is given.

---

### Task 4: The dense route and a versioned collection

**Files:**
- Create: `src/specpilot/retrieval/dense.py`
- Test: `tests/unit/retrieval/test_dense.py`,
  `tests/integration/qdrant/test_collection.py`

**Interfaces:**
- Produces: `DenseIndex.build(...)`, `.search(...)`, and a collection name
  carrying the corpus and pipeline versions.

- [x] **Step 1: Write failing tests, unit and integration**

Unit tests cover the collection schema, the point payload, and the refusal to
write into a frozen collection. The Qdrant integration test skips without a
running instance and says so loudly, matching the ledger tests' discipline — a
skipped run proves nothing.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement, reusing W1's cache key**

Vectors are cached under `embedding_cache_key(weights, pipeline, text)`, which
already exists and already invalidates on a re-chunk. Encoding uses the
length-grouped batching W1 measured.

- [x] **Step 4: Build both documents and record the wall clock**

1924 points (1909 clauses + 15 tables), 1024 dimensions, cosine. Encoding and
upserting both documents took 46.4 s end to end on MPS.

**The indexable-text decision deferred from Task 3 is settled and measured.**
The section heading joins the indexed text, and it is what makes a section
findable by its own number:

| BM25 query `5.6.2` | top three |
|---|---|
| heading excluded | §5.6.1.2, §1.2, §A — misses it |
| heading included | **§5.6.2, §5.6.2**, §5.6.1.2 |

The cost is small and now measured rather than assumed: vocabulary 4599 → 4692
(+2%), and `tokens` rises from 8 documents to 10, which is exactly the IDF
dilution predicted.

Dense misses the same query either way — §13.2.2 and §18.2, at low scores. An
identifier carries almost no semantic signal, so the question embeds as "what
does section require". That is not a defect to fix in the dense route; it is
why §5.1 has `get_clause` as a separate tool and why the two routes are fused
rather than chosen between.

---

### Task 5: RRF, and the local primitives pooling needs

**Files:**
- Create: `src/specpilot/retrieval/hybrid.py`
- Modify: `src/specpilot/cli.py`
- Test: `tests/unit/retrieval/test_hybrid.py`

**Interfaces:**
- Produces: `reciprocal_rank_fusion(...)`, `specpilot retrieval search`, and the
  local `get_clause` / `get_toc` primitives §5.1 defines.

- [ ] **Step 1: Write failing fusion and boundary tests**

Assert RRF is order-independent between the two input rankings, that a document
ranked by only one route still places, and that the fused ranking is never used
as a pooling input. Assert the candidate pool does not leave the machine and
that `get_clause` returns full text locally while the excerpt cap still governs
anything outbound.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement and verify GREEN**

---

### Task 6: The frozen corpus manifest

**Files:**
- Create: `src/specpilot/contracts/corpus_manifest.py`
- Modify: `src/specpilot/cli.py`
- Test: `tests/unit/contracts/test_corpus_manifest.py`

**Interfaces:**
- Produces: `CorpusManifest`, `specpilot corpus freeze`, and a load-time
  verification that refuses a collection which no longer matches.

§6.4 lists exactly what it binds: the source manifests, parser/tokenizer/chunker
versions, embedding weight hash, BM25 and RRF parameters, the versioned
collection and snapshot IDs, the schema, the point count, the point and
content-hash inventory root, and the derived corpus hash.

- [ ] **Step 1: Write failing immutability and verification tests**

Assert the manifest is content addressed and create-only, like the source
manifests; that changing any bound field yields a new ID rather than mutating
one; that ingestion loses write access to the collection after freezing; and
that a load-time mismatch in schema, point count, or inventory root refuses
rather than warns.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement and freeze the real corpus**

---

### Task 7: The pooling run — ADJUDICATION OWNER: the author

**Files:**
- Create: `src/specpilot/retrieval/pooling.py`
- Test: `tests/unit/retrieval/test_pooling.py`

**Blocked on W1 Task 6.** Pooling has nothing to complete until gold exists.
The machinery is built and tested here; the run happens once, when the author's
annotation is done, and every adjudication in it is theirs.

- [ ] **Step 1: Write failing pooling-protocol tests**

Assert the pool is the union of BM25-only and dense-only top-5 and never
includes a hybrid ranking; that the run is refused if it would execute twice;
that every candidate is recorded with its origin route and content hash; and
that the sealed log contains the query order, the baseline configuration, and
the candidate hashes §8.2.4 requires.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement, with adjudication left to `AnnotationStore.amend`**

The store already refuses gold removal and already writes a successor rather
than mutating. Pooling proposes into that, and no further.

- [ ] **Step 4: Execute the run once gold exists** — OWNER: the author for every
  adjudication; tooling may present candidates and record decisions only.

---

## Plan self-review record

- **Scope decision:** W2 covers ingestion, both retrieval routes, fusion,
  immutability, and the pooling machinery. Agent chain, MCP, and the API are
  W3's. No quality metric is produced.
- **Translation over transcription:** four of §4.1's clauses are DOCX-shaped.
  Each is translated with its reason recorded, and the one that does not apply —
  sliding-window child chunking — is documented as not implemented rather than
  built as unreachable code.
- **Independence preserved:** BM25 and dense share no model, and BM25 gets its
  own tokenizer rather than reusing the deliberately crude one from the overlap
  measure. §8.2.3's argument for pooling depends on exactly this.
- **Blocked work marked:** Task 7 cannot complete before W1 Task 6, and says so
  rather than quietly assuming gold will appear.
- **Placeholder scan:** every implementation step names concrete behaviour,
  files, and verification.
