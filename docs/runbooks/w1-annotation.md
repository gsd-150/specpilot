# W1 Annotation Runbook

This is the operating procedure for gold annotation against frozen sources.
The tools validate records, verify source references, calculate overlap, and
report aggregates. They do not decide a question, a gold clause, or a verdict.

## Provenance v2 is an audit trail, not an admission gate

Every L1 and L2 record uses `annotation-l1/v2` or `annotation-l2/v2` and names
two annotation-level origins:

- `content_origin`: `human`, `model`, or `mixed`;
- `label_origin`: `human`, `model`, or `mixed`.

Every answerable record also carries a non-empty ordered `gold_origins` list.
Each event records an origin and, for model or retrieval events, its producer.
The accepted origins are:

| Origin | Producer |
|---|---|
| `source_text_navigation`, `literal_search`, `cross_reference_trace`, `terminology_index`, `human_source_review` | forbidden |
| `model_proposal`, `search_clauses`, `dense_retrieval`, `bm25_retrieval`, `hybrid_retrieval` | required |

Origins are disclosed so a report can distinguish human, model, and retrieval
assistance. They do not decide whether a source-checked record can be stored.
An unanswerable L1 record has no clauses, section paths, overlap figure, or
Gold-origin events. An L2 `insufficient_evidence` item remains answerable and
retains its Gold origins.

The lean v2 provenance fields have this shape:

```json
{
  "schema_version": "annotation-l1/v2",
  "content_origin": "mixed",
  "label_origin": "mixed",
  "gold_origins": [
    {"origin": "model_proposal", "producer": "openai-codex"},
    {"origin": "human_source_review"}
  ]
}
```

Retrieval-originated Recall is a diagnostic: report it with the provenance
distribution and do not present it as independent evidence of retrieval
quality. The record still must be checked against the frozen source.

## Source checks retained at entry

When a record has gold, `annotation add` requires `--manifest`,
`--manifest-dir`, and `--xml`. The entry path still refuses:

- a clause absent from the named frozen document (`unknown_gold_clause`);
- a document ID or version mismatch;
- a key point that restates its source clause;
- malformed records (`invalid_annotation_record`);
- v1 or unknown schemas (`unsupported_annotation_schema`).

Records contain no clause prose. Answerable records need at least one gold
clause, a Gold-origin event, and `question_gold_jaccard`; key points remain
criteria rather than quotations.

## Procedure

Set the source identity for the frozen RFC rendition:

```bash
export SP_MANIFEST=af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691
export SP_XML=artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml
```

List locators (not clause text), then calculate the required literal-overlap
figure:

```bash
.venv/bin/python -m specpilot.cli corpus clauses --manifest "$SP_MANIFEST" --manifest-dir manifests/local/r0/source --xml "$SP_XML" --section 5.6.2
.venv/bin/python -m specpilot.cli corpus overlap --manifest "$SP_MANIFEST" --manifest-dir manifests/local/r0/source --xml "$SP_XML" --clause-id <clause_id> --question "..."
```

Start a v2 template and fill in its provenance before adding it:

```bash
.venv/bin/python -m specpilot.cli annotation template --level l1 > tmp/l1-dev-001.json
.venv/bin/python -m specpilot.cli annotation add --record tmp/l1-dev-001.json --annotation-dir artifacts/restricted/annotations --manifest "$SP_MANIFEST" --manifest-dir manifests/local/r0/source --xml "$SP_XML"
```

The template deliberately remains invalid until the author supplies a question,
answerable gold, overlap, and origin events. Replaying identical content is a
no-op; a different root with the same `item_id` is refused.

## Progress and pooling

Run:

```bash
.venv/bin/python -m specpilot.cli annotation progress --annotation-dir artifacts/restricted/annotations
```

Progress is aggregate-only and contains no question, key-point, section-path,
or source prose. Alongside completed counts, direction mix, adjudication state,
and pooled clause counts, it reports:

- `provenance.content_origins` and `provenance.label_origins`;
- event totals in `provenance.gold_origins`;
- ordered event chains in `provenance.gold_origin_chains`;
- `provenance.retrieval_originated_gold_items`;
- `verdict_counts` for L2 (empty for L1).

Pooling is one source-reviewed, add-only amendment before the evaluation set is
time-locked. `AnnotationStore.amend` writes a successor, retains all prior
gold and all prior origin events, appends the new events and adjudication, and
refuses an addition of gold without at least one new origin. Never run another
pooling amendment after locking the set or tune a system on locked results.

`artifacts/restricted/` is `0700` and git-ignored. Do not commit source text,
full clause indexes, or real annotation records.
