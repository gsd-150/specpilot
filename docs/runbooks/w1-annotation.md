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
specpilot annotation add --record tmp/l1-dev-001.json --annotation-dir artifacts/restricted/annotations
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
| `invalid_annotation_record` | Any field the contract does not declare |
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
