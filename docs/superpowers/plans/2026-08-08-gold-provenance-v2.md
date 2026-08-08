# Gold Provenance v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admit model and retrieval Gold through annotation v2 while retaining a lean, ordered provenance audit and all existing source-correctness gates.

**Architecture:** Annotation records summarize content and label authorship with two enums and preserve Gold discovery as an ordered event tuple. Contracts own one explicit v2 schema dispatcher shared by storage and CLI; progress counts only item heads and exposes origin distributions, Gold chains, retrieval bias, and L2 verdict counts. Three local RFC 9112 candidates are validated temporarily and remain outside the formal store until explicit approval.

**Tech Stack:** Python 3.12+, Pydantic v2, argparse, canonical JSON/SHA-256, pytest, Ruff, mypy.

## Global Constraints

- Use only `annotation-l1/v2` and `annotation-l2/v2`; v1 and unknown schemas refuse with `unsupported_annotation_schema`.
- Origin is audit metadata, never an admission gate.
- Preserve source hash/safety, identity/version, Gold existence, overlap, key-point, strict-label, lineage, private-storage, and add-only pooling checks.
- A pooling successor stores the complete inherited Gold-origin chain plus appended events.
- Do not add an automatic Gold proposal command or persistent BM25/Qdrant index.
- Do not stage the user-owned `SpecPilot_项目方案.md`, ignored candidates, frozen sources, or formal annotations.
- Do not resume or rewrite the separate RFC single-snapshot security work in these tasks.

## Corrected Current Progress

- Formal annotations: `0`.
- Gold provenance v2: lean design approved; implementation not started.
- RFC single-snapshot Task 1: implemented and independently reviewed clean.
- RFC single-snapshot Task 2: implemented at `6fed0c6`; focused/full verification passed, controller review paused.
- Last implementation suite: `580 passed, 32 skipped`; Ruff and mypy clean.

---

### Task 1: Implement lean annotation v2 atomically across all consumers

**Files:**
- Modify: `src/specpilot/contracts/annotation.py`
- Modify: `src/specpilot/annotation/store.py`
- Modify: `src/specpilot/cli.py`
- Modify: `src/specpilot/annotation/progress.py`
- Modify: `tests/unit/annotation/test_annotation_contracts.py`
- Modify: `tests/cli/test_annotation_entry.py`
- Modify: `tests/unit/annotation/test_progress.py`
- Modify: `tests/cli/test_annotation_progress.py`
- Modify: `docs/runbooks/w1-annotation.md`
- Modify: `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`
- Modify: `docs/superpowers/plans/2026-08-07-w1-annotation-and-embedding.md`
- Modify without staging: `SpecPilot_项目方案.md`

**Interfaces:**
- Produces: `AnnotationOrigin`, `GoldOrigin`, `GoldOriginEvent`, `UnsupportedAnnotationSchemaError`, and `annotation_model_for_schema(...)`.
- Produces: `content_origin`, `label_origin`, and `gold_origins` on L1/L2 v2.
- Changes: `AnnotationStore.amend(..., added_gold_origins: tuple[GoldOriginEvent, ...], ...)`.
- Produces: `ProvenanceProgress`, `SetProgress.provenance`, and `SetProgress.verdict_counts`.
- Documents: v2 origins, diagnostic retrieval metrics, model/mixed label disclosure, stable schema refusal, and current CLI invocation.
- Preserves: all source-aware entry checks and storage invariants.

- [ ] **Step 1: Write failing v2 contract and store tests**

Replace v1/`independent_path` fixtures with:

```python
"schema_version": "annotation-l1/v2",
"content_origin": "mixed",
"label_origin": "mixed",
"gold_origins": (
    {"origin": "model_proposal", "producer": "openai-codex"},
    {"origin": "human_source_review"},
),
```

Add tests that:

- all three `AnnotationOrigin` values and ten `GoldOrigin` values validate;
- model/retrieval Gold origins require `producer`;
- human/source Gold origins reject `producer`;
- an answerable record requires a non-empty Gold-origin tuple;
- an L1 unanswerable record requires an empty tuple;
- L2 `insufficient_evidence` retains Gold origins;
- v1 and unknown stored schemas raise `UnsupportedAnnotationSchemaError`;
- a pooling amendment appends `bm25_retrieval@bm25-pool-r0` and
  `human_source_review` to the predecessor chain;
- adding Gold without any appended origin refuses before writing.

- [ ] **Step 2: Write failing CLI tests**

Update `gold_record()` and valid fixtures to v2. Assert the template emits v2,
contains `content_origin`, `label_origin`, and `gold_origins`, and omits
`independent_path`. Replace the retrieval-is-forbidden test with successful
model and hybrid-retrieval source-aware adds. Assert v1 and unknown schema
records return exit 2, empty stdout, `unsupported_annotation_schema\n`, and no
annotation directory.

- [ ] **Step 3: Run the focused tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/unit/annotation/test_annotation_contracts.py tests/cli/test_annotation_entry.py
```

Expected: collection or assertion failures because v2 types and dispatch do
not exist.

- [ ] **Step 4: Implement the lean contract**

Replace `IndependentPath` with:

```python
class AnnotationOrigin(StrEnum):
    HUMAN = "human"
    MODEL = "model"
    MIXED = "mixed"


class GoldOrigin(StrEnum):
    SOURCE_TEXT_NAVIGATION = "source_text_navigation"
    LITERAL_SEARCH = "literal_search"
    CROSS_REFERENCE_TRACE = "cross_reference_trace"
    TERMINOLOGY_INDEX = "terminology_index"
    HUMAN_SOURCE_REVIEW = "human_source_review"
    MODEL_PROPOSAL = "model_proposal"
    SEARCH_CLAUSES = "search_clauses"
    DENSE_RETRIEVAL = "dense_retrieval"
    BM25_RETRIEVAL = "bm25_retrieval"
    HYBRID_RETRIEVAL = "hybrid_retrieval"


class GoldOriginEvent(_FrozenModel):
    origin: GoldOrigin
    producer: Identifier | None = None
```

One event validator requires producer for model/retrieval origins and forbids
it for human/source origins. Change schema literals to v2 and add required
`content_origin`, `label_origin`, and `gold_origins=()` fields. Extend the
existing answerability validator with the Gold-origin rules; add no scope or
duplicate-event system.

Add one explicit dispatcher:

```python
class UnsupportedAnnotationSchemaError(ValueError):
    pass


def annotation_model_for_schema(
    schema_version: object,
) -> type[L1Annotation] | type[L2Annotation]:
    if schema_version == "annotation-l1/v2":
        return L1Annotation
    if schema_version == "annotation-l2/v2":
        return L2Annotation
    raise UnsupportedAnnotationSchemaError("unsupported annotation schema")
```

- [ ] **Step 5: Use the shared dispatcher in store and CLI**

`AnnotationStore.read()` parses the JSON object and calls the shared dispatcher;
it never defaults to L1. `amend()` requires `added_gold_origins` when Gold is
added, appends them to the predecessor's complete tuple, validates the
successor before writing, and retains adjudication/add-only behavior.

The CLI templates emit v2 with `content_origin: human`, `label_origin: human`,
and `gold_origins: []`. `_annotation_add()` maps the shared unsupported-schema
exception to `unsupported_annotation_schema`, maps malformed/invalid records to
`invalid_annotation_record`, and does not branch on origin. Keep
`_check_gold_against_source()` intact.

- [ ] **Step 6: Write failing progress tests before removing the last v1 consumer**

Convert fixtures to v2. Build the final three-candidate summary and assert:

```python
assert report.l2.provenance.content_origins == {"mixed": 3}
assert report.l2.provenance.label_origins == {"mixed": 3}
assert report.l2.provenance.gold_origins == {
    "human_source_review": 3,
    "model_proposal": 3,
}
assert report.l2.provenance.gold_origin_chains == {
    "model_proposal@openai-codex > human_source_review": 3,
}
assert report.l2.provenance.retrieval_originated_gold_items == 0
assert report.l2.verdict_counts == {
    "compliant": 1,
    "insufficient_evidence": 1,
    "violating": 1,
}
```

Add one hybrid Gold event and assert the retrieval item count becomes one. Add
a pooling successor and assert only the final head and full appended chain are
counted. Update every existing `AnnotationStore.amend()` call in this test file
with an explicit `added_gold_origins` tuple when it adds Gold. CLI progress must
omit `independent_paths`, emit the new objects,
carry no annotation prose, and map stored v1 to
`unsupported_annotation_schema`.

- [ ] **Step 7: Run progress tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/unit/annotation/test_progress.py tests/cli/test_annotation_progress.py
```

Expected: failures because progress still reads `head.independent_path`.

- [ ] **Step 8: Implement the five audit outputs**

Add:

```python
@dataclass(frozen=True, slots=True)
class ProvenanceProgress:
    content_origins: dict[str, int]
    label_origins: dict[str, int]
    gold_origins: dict[str, int]
    gold_origin_chains: dict[str, int]
    retrieval_originated_gold_items: int
```

For each item head, count the content/label enum once, count every Gold event,
join ordered event labels with ` > `, and count an item once if its chain
contains search, dense, BM25, or hybrid retrieval. Count L2 expected verdicts;
use an empty verdict mapping for L1. Preserve every other progress field. Catch
`UnsupportedAnnotationSchemaError` before generic `ValueError` in CLI progress.

- [ ] **Step 9: Update live documentation**

Rewrite the W1 runbook from admission gating to ordered-origin auditing. Show
the lean v2 fields, accepted origins, producer rule, progress fields, retained
source checks, `unsupported_annotation_schema`, and the fact that
retrieval-originated Recall is diagnostic. Replace bare `specpilot` examples
with `.venv/bin/python -m specpilot.cli`.

Update the roadmap deliverable to provenance auditing. Add only a supersession
note to the historical W1 plan. In `SpecPilot_项目方案.md`, change §8.2 to allow
all origins while requiring disclosure, keeping Jaccard, source verification,
one-time add-only pooling, and time locking. Do not stage that untracked file.

- [ ] **Step 10: Verify the atomic v2 migration and commit Task 1**

```bash
.venv/bin/python -m pytest -q tests/unit/annotation tests/cli/test_annotation_entry.py tests/cli/test_annotation_progress.py
.venv/bin/ruff check src/specpilot/contracts/annotation.py src/specpilot/annotation/store.py src/specpilot/annotation/progress.py src/specpilot/cli.py tests/unit/annotation tests/cli/test_annotation_entry.py tests/cli/test_annotation_progress.py
.venv/bin/mypy src
.venv/bin/python -m pytest -q
git diff --check
git add src/specpilot/contracts/annotation.py src/specpilot/annotation/store.py src/specpilot/annotation/progress.py src/specpilot/cli.py tests/unit/annotation/test_annotation_contracts.py tests/unit/annotation/test_progress.py tests/cli/test_annotation_entry.py tests/cli/test_annotation_progress.py docs/runbooks/w1-annotation.md docs/roadmaps/2026-08-06-specpilot-master-roadmap.md docs/superpowers/plans/2026-08-07-w1-annotation-and-embedding.md docs/superpowers/specs/2026-08-08-gold-provenance-v2-design.md
git commit -m "feat: replace independent Gold with provenance v2"
```

Expected: every source consumer is v2, the full suite is green, tracked
code/docs are committed, and `SpecPilot_项目方案.md` remains
untracked and unstaged.

---

### Task 2: Complete and temporarily validate the three RFC 9112 candidates

**Files:**
- Modify but never stage: `tmp/l2-dev-001.json`
- Modify but never stage: `tmp/l2-dev-002.json`
- Modify but never stage: `tmp/l2-dev-003.json`
- Preserve: `tmp/model-drafted-not-gold.rfc9112-6.3.json`

**Interfaces:**
- Produces: three complete local v2 candidates and a temporary validation store.
- Does not produce: a Git commit, formal annotation, or persistent index.

- [ ] **Step 1: Rewrite the three ignored candidates**

Use `content_origin: mixed`, `label_origin: mixed`, and this Gold chain:

```json
[
  {"origin": "model_proposal", "producer": "openai-codex"},
  {"origin": "human_source_review"}
]
```

Keep RFC 9112 version `2022-06`, section path
`Message Body > Message Body Length`, and clause
`817e50534fc9d2e00b485d0d445b95992b1fdc25ef354febcd87bfc1be60e7bb`.
Set `proposed_verdict` equal to the task label and `supports_verdict: true`.

Use these complete scenario/rubric values:

| Item | Verdict | Jaccard | Question |
|---|---|---:|---|
| `l2-dev-001` | `violating` | `0.1928` | An edge reverse proxy accepts a public POST request containing both Transfer-Encoding: chunked and Content-Length: 5. The decoded chunked body is exactly five octets. Because the two lengths agree, the proxy reads the body according to Transfer-Encoding and copies both received header fields unchanged onto its connection to the origin. Assess whether that forwarding behavior conforms to RFC 9112. |
| `l2-dev-002` | `compliant` | `0.1707` | An API gateway normalizes request framing before sending a request to an internal service. When it receives both Transfer-Encoding: chunked and Content-Length, it removes the received Content-Length, decodes the complete chunked body, and sends a fixed-length upstream request with no Transfer-Encoding and a newly generated Content-Length equal to the actual body size. Assess whether this forwarding behavior conforms to RFC 9112. |
| `l2-dev-003` | `insufficient_evidence` | `0.1477` | An incident report records only that several inbound POST requests carried both Transfer-Encoding: chunked and Content-Length. The proxy returned no 4xx response and logged no warning, but the report preserved neither the upstream requests nor any description of how the proxy handled those fields while forwarding. Based on this record alone, assess whether the proxy conformed to RFC 9112. |

Rubrics:

- `001`:
  - criterion `uses the outbound header set rather than decoded size agreement as the decisive evidence`; values `Transfer-Encoding`, `Content-Length`, `5 octets`;
  - criterion `checks whether the received length field was discarded before the downstream hop`; value `received Content-Length`;
  - criterion `checks whether the transfer coding was processed before the downstream hop`; value `chunked`.
- `002`:
  - criterion `distinguishes the received length field from a new length generated after decoding`; values `received Content-Length`, `new Content-Length`;
  - criterion `checks that the chunked coding is fully consumed before the upstream message is emitted`; value `chunked`;
  - criterion `checks that the upstream request uses one coherent framing method`; values `no Transfer-Encoding`, `new Content-Length`.
- `003`:
  - criterion `separates advisory error handling from obligations that apply when forwarding occurs`; values `ought to`, `MUST`;
  - criterion `requires evidence about the outbound header set before evaluating the relay step`; value `upstream headers`;
  - criterion `does not infer sanitization from the absence of a 4xx response or warning`; values `no 4xx`, `no warning`.

- [ ] **Step 2: Recompute overlap and source-validate in a temporary directory**

Run `corpus overlap` once per exact question with manifest
`3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2`,
manifest directory `manifests/local/r0/source`, RFC 9112 XML, and the Gold ID
above. Expected values are `0.1928`, `0.1707`, and `0.1477`.

Use these commands from the repository root:

```bash
for SP_V2_RECORD in tmp/l2-dev-001.json tmp/l2-dev-002.json tmp/l2-dev-003.json; do
  SP_V2_QUESTION="$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["question"])' "$SP_V2_RECORD")"
  .venv/bin/python -m specpilot.cli corpus overlap \
    --manifest 3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2 \
    --manifest-dir manifests/local/r0/source \
    --xml artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml \
    --clause-id 817e50534fc9d2e00b485d0d445b95992b1fdc25ef354febcd87bfc1be60e7bb \
    --question "$SP_V2_QUESTION"
done

SP_V2_VALIDATION_DIR="$(mktemp -d)"
for SP_V2_RECORD in tmp/l2-dev-001.json tmp/l2-dev-002.json tmp/l2-dev-003.json; do
  .venv/bin/python -m specpilot.cli annotation add \
    --record "$SP_V2_RECORD" \
    --annotation-dir "$SP_V2_VALIDATION_DIR" \
    --manifest 3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2 \
    --manifest-dir manifests/local/r0/source \
    --xml artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml
done
.venv/bin/python -m specpilot.cli annotation progress \
  --annotation-dir "$SP_V2_VALIDATION_DIR"
```

Expected: three L2 dev records, mixed content/label counts of three, the
two-event Gold chain count of three, no retrieval-originated Gold, and one of
each verdict.

- [ ] **Step 3: Run complete verification without formal entry**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
git diff --check
find artifacts/restricted/annotations -maxdepth 1 -type f -print
```

Expected: tests/static checks pass and the formal annotation directory prints
no record. Present all three candidates and validation outputs to the user.
This task intentionally has no commit.

---

### Task 3: Add the three reviewed candidates after explicit approval

**Files:**
- Create only after approval: three private records under `artifacts/restricted/annotations/`

**Interfaces:**
- Consumes: explicit user acceptance of the complete Task 2 records.
- Produces: three content-addressed L2 dev annotations and verified progress.

- [ ] **Step 1: Stop unless the user explicitly approves all three records**

No formal-write command is authorized before that response.

- [ ] **Step 2: Re-run temporary validation, then add to the formal directory**

Use the same three source-aware commands from Task 2, changing only
`--annotation-dir` to `artifacts/restricted/annotations`. Capture all returned
annotation IDs.

- [ ] **Step 3: Verify final progress and permissions**

Run `annotation progress` on the formal directory and inspect stored file
permissions. Expected: exactly three L2 dev records, mixed content/label counts
of three, Gold-chain count of three, zero retrieval-originated Gold, and one of
each verdict. Restricted records remain git-ignored and are never committed.

## Deferred Finding

Source-aware entry verifies Gold IDs but does not yet verify that each supplied
`gold_section_paths` value matches the actual section path of its Gold ID. This
is a separate integrity hardening change, not silently bundled into provenance
v2.
