# W1 Annotation Runbook

The operating procedure for product plan §8.2's gold annotation. Everything in
this file is mechanics; every judgement it asks for is the author's.

Kept in English for consistency with the rest of `docs/`, which is part of the
deliverable.

## What tooling may and may not do

Tooling here lists clause identifiers, validates record shape, computes one
arithmetic overlap figure, and counts progress. It does not originate a
question, choose a gold clause, or decide a verdict — and it is built so that it
cannot: there is no command that proposes an item, and `IndependentPath` has no
value for retrieval, so gold discovered by the system has no representation.

## Using a language model while annotating

Not covered by §8.2.1's letter, which names `search_clauses` and this system's
dense, sparse, and hybrid rankings. An outside model is none of those. It is
still governed, and the line falls in a specific place.

**A model may not choose the gold clause or decide the verdict.** Reading a
model's proposal and agreeing with it is not independent discovery — it is
§8.2.1's own banned pattern, "run the system, see whether the output looks
reasonable, adopt it if so", with a different producer. Checking the proposal
against the source afterwards does not convert it, because that check is the
agreeing.

For L2 this bites harder than for L1. The system's Verifier is itself a language
model, so a model-authored `expected_verdict` makes part of the verdict
confusion matrix a measurement of whether two models agree. §4.6 and §8.3.3
already concede that a different model slug reduces same-model self-evaluation
bias without proving the errors independent; a model-authored gold verdict is
that same problem one level earlier, where nothing downstream can correct it.

**A scenario written from a known clause is contaminated too**, not just its
fields. If the design description was composed to violate a specific clause, the
answer is built into the question, and re-deriving the gold afterwards only
returns to the same clause. Such an item is discarded rather than repaired.

**A model may polish wording**, as long as it does not decide which clause or
which verdict. Then `independent_path` remains true and the record stands — with
one line in the report saying prose was model-assisted, because §8.1 already
requires disclosing that annotation was single-annotator and that disclosure has
to be complete.

There is deliberately no `IndependentPath` member for a model proposal. Adding
one to accommodate a draft would be widening the gate until it passes, which is
the same move as raising a QA threshold to make a document parse.

## Two checks the entry path enforces

Both need the source, which is why `annotation add` requires `--manifest`,
`--manifest-dir`, and `--xml` whenever a record carries gold.

- **`unknown_gold_clause`** — a gold clause id that the named document does not
  contain. Shape validation cannot see this; a stale or mistyped id would sit in
  the set as a clause the retriever can never return, quietly depressing recall.
- **`key_point_restates_clause`** — a criterion of eight tokens or more whose
  vocabulary is more than 80% drawn from its gold clause. §8.1 permits factual
  values and forbids reproducing wording as sentences; the threshold sits in the
  wide gap measured on real drafts, between criteria written as judgement
  standards at about 33% and one that preserved the clause's sentence at 93%.
  The eight-token floor exists because three words can be entirely clause
  vocabulary without reproducing any expression.

A verdict label inside a key point's `factual_values` is refused by the contract
itself, before the source is consulted: §8.1 keeps task-level gold and scoring
criteria in separate fields, and a verdict in a key point makes an answer
scoreable for producing the word rather than the reasoning.

## Targets

| Set | Total | dev | locked | W1's share |
|---|---|---|---|---|
| L1 clause QA | 40 | 15 | 25 | 15 dev + ~12 locked |
| L2 conformance | 20 | 8 (3/3/2) | 12 (4/4/4) | 8 dev + ~6 locked |

Floors that hold **per split**, not just in total: L1 unanswerable dev ≥ 3 and
locked ≥ 5; L2 `insufficient_evidence` dev = 2 and locked = 4. Direction mix for
L1 only: about 60% clause-first, 40% scenario-first.

## The permitted paths, and the one that is not

Initial gold may come only from a path unrelated to the system's retriever
(§8.2.1). Recorded on every item as `independent_path`:

| Value | What it means here |
|---|---|
| `source_text_navigation` | Reading the frozen `.txt` or `.xml` rendition |
| `literal_search` | Exact string search over the frozen bytes |
| `cross_reference_trace` | Following the document's own cross-references |
| `terminology_index` | The document's terminology and abbreviation lists |

`specpilot corpus parse` output, `search_clauses`, and any dense, sparse, or
hybrid ranking are **not** gold sources. The frozen renditions are:

```
artifacts/restricted/sources/ietf/rfc9110/rfc9110.txt
artifacts/restricted/sources/ietf/rfc9112/rfc9112.txt
```

## Procedure per item

Environment for the examples below — RFC 9110:

```bash
export SP_MANIFEST=af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691
export SP_XML=artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml
```

RFC 9112's manifest is
`3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2`, its XML
`artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml`.

### 1. Find the clause in the source, then get its identifier

Locate the paragraph by reading or searching the frozen `.txt`. Then translate
"the second paragraph of §5.6.2" into the identifier a record stores:

```bash
specpilot corpus clauses --manifest "$SP_MANIFEST" --manifest-dir manifests/local/r0/source --xml "$SP_XML" --section 5.6.2
```

One JSON line per clause, in document order, with `ordinal` counting paragraphs
within the section. `--section 1` selects 1.2 but not 10 — the match is by
number component, not string prefix. No clause text is printed; the source is
where text is read.

### 2. Write the question

Clause-first items must not reuse the clause title's full wording (§8.2.2).
Unanswerable items are **constructed on purpose** to the floor — out of scope,
cross-specification, or needing a version the corpus does not carry — not
harvested from scenario-first attempts that happened to fail. Failed attempts
may top the quota up; they may not be its main source.

### 3. Measure the literal overlap

Required on every answerable item, so W6 can report Macro-Recall stratified by
it. Repeat `--clause-id` for multi-gold items; the figure is the maximum over
gold clauses, not the union.

```bash
specpilot corpus overlap --manifest "$SP_MANIFEST" --manifest-dir manifests/local/r0/source --xml "$SP_XML" --clause-id <clause_id> --question "..."
```

A question that reuses the clause's wording comes back at or near 1.0. That is
information, not a failure — but a set where most clause-first items score near
1.0 is a set that literal matching can win, which is what the stratification
exists to expose.

### 4. Record it

```bash
specpilot annotation template --level l1 > tmp/l1-dev-001.json
# fill it in, then:
specpilot annotation add --record tmp/l1-dev-001.json --annotation-dir artifacts/restricted/annotations \
  --manifest "$SP_MANIFEST" --manifest-dir manifests/local/r0/source --xml "$SP_XML"
```

The template is deliberately invalid as printed — empty question, no gold — so
it cannot be submitted twenty-three times unedited. Records are content
addressed: adding the identical file twice is a no-op returning the same ID,
while a second, different record for the same `item_id` is refused.

What the contract will refuse, all of them worth knowing before you hit them:

| Refusal | Cause |
|---|---|
| `invalid_annotation_record` | Answerable with no gold, or no overlap figure |
| `invalid_annotation_record` | Unanswerable carrying gold or an overlap figure |
| `invalid_annotation_record` | `independent_path` not one of the four |
| `invalid_annotation_record` | A verdict label in a key point's factual values |
| `invalid_annotation_record` | Any field the contract does not declare |
| `source_required_for_gold` | The record carries gold but no source was given |
| `document_id_mismatch` | The record names a different document than the source |
| `document_version_mismatch` | The record, manifest, and XML publication version do not agree |
| `invalid_document_identity` | The RFC number or unique publication date is missing or invalid |
| `unknown_gold_clause` | A gold clause id the document does not contain |
| `key_point_restates_clause` | A criterion reproducing its clause's wording |
| `item_id_already_annotated` | That `item_id` already owns a different record |

L2 additionally requires `claim_id`, `expected_verdict`, `proposed_verdict`, and
`supports_verdict`. The first is the task-level gold; the last two are the
Verifier pair's support relation. §8.1 is explicit that these are different
labels and must not substitute for one another, which is why they are separate
fields rather than one reused verdict.

### 5. Check progress

```bash
specpilot annotation progress --annotation-dir artifacts/restricted/annotations
```

Counts items, not files, so a §8.2.3 amendment does not inflate the total. A
tampered record, a chain missing its root, or two chains claiming one `item_id`
refuse the whole report rather than quietly reporting a smaller number.

## Where records live

`artifacts/restricted/` is `0700` and git-ignored. Annotation records hold no
clause prose by construction, so committable fields could be published later —
but that is a deliberate step, not the default, and the pooled-gold share and
single-annotator limitation must be disclosed with them (§8.2.3, §8.1).

## What comes after W1

Pooling (§8.2.3) runs once at the end of W2, when BM25-only and dense-only are
both available, and never after the test set is locked. It uses
`AnnotationStore.amend`, which writes a successor and refuses any removal of
established gold. Until then every record shows as `awaiting_adjudication` in
the progress report, which is correct and not a backlog.
