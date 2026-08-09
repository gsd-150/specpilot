# R1: IIIF Corpus Foundation Implementation Plan

> **SUPERSEDED — 2026-08-09, before any task ran. Not executed.**
>
> The corpus stays on RFC 9110/9112. This plan existed to solve one problem —
> the annotator could not adjudicate HTTP semantics fast enough to produce gold
> — and that problem is now addressed by changing the *task* instead of the
> corpus: proposals are drafted for the author, who adjudicates by forced choice
> rather than by authoring from scratch. See
> `2026-08-09-assisted-annotation-and-review.md`.
>
> Kept rather than deleted because its corpus comparison is the record of what
> was weighed. Its conclusion still holds and is worth restating: RFC 9110/9112
> is the better document on nearly every engineering dimension — machine-tagged
> normative keywords, cross-references by an order of magnitude, paragraph
> identity published by the source itself, an already-hardened boundary, and a
> volume known to be sufficient. Keeping it keeps all of that. What IIIF would
> have bought is now bought more cheaply, and the two weaknesses this plan
> recorded — absent keyword markup and thin cross-references — are not incurred.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the corpus to the IIIF specification family — a normative, freely
licensed, machine-readable body of standards in the annotator's own field — with
its own ingestion boundary, source manifests, source-terms assessment, and parse
path, so that annotation can proceed at a rate the schedule actually depends on.

**Why this plan exists:** The bottleneck is annotation, and the measured cause is
domain judgement, not tooling. Writing an L1 question and adjudicating its gold
requires knowing the material well enough to say what a realistic question is
and whether a clause answers it. Against HTTP framing semantics that cost is
prohibitive for this annotator; against digital-library standards it is ordinary
work. Product plan §13 already names this risk — "领域知识不足导致标注错误" — and
it has now materialised as 0 of 40 L1 items after two weeks of corpus.

**This is the third corpus.** 3GPP was refused by the ingestion boundary; IETF
RFC was selected on licence and format and never tested against the one
constraint that turned out to bind. That constraint is now in the list, first.

**Architecture:** A new `specpilot.ingestion.html` boundary verifies an
already-fetched IIIF specification — byte caps, strict UTF-8, and a parse that
refuses DOCTYPE tricks, entities, scripts, iframes, embedded objects, and
external resource references. `source-manifest/v2` already describes a document
with no archive and no DOCX and is reused unchanged. Everything provider-side is
untouched.

**Tech Stack:** Python 3.12+, Pydantic 2, defusedxml, pytest, Hypothesis, Ruff,
mypy. HTML parsing uses the standard library's `html.parser` driven by our own
rules rather than a new dependency; see Task 2.

## Measured starting facts

Captured 2026-08-09 by fetching the published specifications. Every number is
re-derived by Task 5 rather than trusted from here.

| Document | RFC 2119 keywords | Compliance model | Licence |
|---|---|---|---|
| Presentation API 3.0 | ~800 (MUST ~450, SHOULD ~250, MAY ~150) | requirements distributed per property | CC-BY 4.0 |
| Image API 3.0 | ~180 (MUST ~85, SHOULD ~65, MAY ~30) | §6 defines level0/level1/level2 | CC-BY 4.0 |

For comparison, the corpus being replaced carried 594 BCP 14 keywords across
both documents. The normative material available for L2 roughly doubles.

Both cite RFC 2119 explicitly. Headings are numbered in the visible text
("3.2. Technical Properties") and carry stable anchors derived from them
(`#32-technical-properties`). Each document has a linked table of contents. The
body carries no scripts and no iframes; one diagram is referenced as an external
image.

**Licence line, verbatim from both documents:** "Copyright © 2012-2026 Editors
and contributors. Published by the IIIF Consortium under the CC-BY license, see
disclaimer."

## Which corpus is actually better, answered plainly

On engineering merit the corpus being replaced wins nearly every dimension:
machine-tagged normative keywords, cross-references by an order of magnitude,
paragraph identity published by the source itself, an ingestion boundary already
hardened through two authorized security fix waves, and a volume known to be
sufficient. IIIF wins two: the annotator can adjudicate it, and CC-BY is clean.

The swap is still right, because engineering merit in a corpus only pays once
there is gold to evaluate against. Two weeks produced zero of forty L1 items,
and §8's entire credibility apparatus — gold isolation, judge calibration, the
adversarial subset, both core comparisons — needs gold before any of it yields a
number. 2,519 cross-references cannot become a result that nobody can annotate.

**So this plan trades document quality for project completion.** It should be
described that way, in the report and in interviews, rather than as an upgrade.

## The three things this corpus does worse, recorded before they are discovered

None is a reason to stop, and each changes work in this plan.

1. **Normative keywords are not machine-tagged.** RFC v3 XML wraps every one in
   `<bcp14>`, so W1's modal attribution inferred nothing. IIIF marks them with
   typographic emphasis only. Keyword extraction becomes text-and-markup
   matching against the RFC 2119 list, which is what product plan §4.1 step 4
   originally assumed for 3GPP. It is weaker, and the orphan-normative QA line
   has to be re-derived rather than carried over.

2. **Cross-reference density is far lower, and this is load-bearing.** RFC 9110
   alone carried 2,519 `<xref>` elements. Presentation 3.0 shows roughly 150
   internal cross-references and 30 external, and Image 3.0 references
   Presentation only 4–5 times. `expand_references` is one of §5.1's five tools
   and §8.4 gives it a dedicated metric; §3.2's case for the previous corpus
   rested partly on reference density. **Task 5 measures this exactly, and if
   cross-document references cannot support a reference-expansion metric, that
   is reported as a finding and the metric is re-scoped — not quietly dropped
   and not padded with a third document chosen to rescue it.**

3. **Clause identity stops being the source's own, and this is the quiet one.**
   RFC v3 publishes `pn` on every paragraph, so a clause ID derives from an
   identifier the document itself assigns, and W2's QA line checked 1559 of 1559
   section numbers against that independent ground truth. IIIF numbers headings
   and anchors them but publishes nothing below heading level, so paragraph
   ordinals become ours. Two consequences: §4.1's "clause ID and section path
   correct" line loses its independent basis and becomes our derivation checked
   against itself, and citation stability across re-parses now rests on our
   ordinal rule rather than on a published fact. **Task 5 must state the ordinal
   rule explicitly and test that a clause ID survives two builds and an edit
   elsewhere in the same document**, because that test now stands where the
   source's own numbering used to.

## What CC-BY changes, and the decision it forces

Route C promised that a permissive corpus would collapse the dual track — real
sources into the repository, §8.0 and §9.6 simplified. It never delivered,
because the RFC source-terms assessment left open whether sending an excerpt to
a third-party API is one of the acts TLP §3.c.iii licenses.

CC-BY 4.0 grants redistribution with attribution outright, so the question that
blocked the collapse does not arise for redistribution. **Whether to actually
collapse it is a decision for Task 4, taken with the assessment in hand rather
than assumed here.** Collapsing has real consequences: the demo runs on the real
corpus, third parties can reproduce retrieval, and §8.0's two-corpus table stops
describing reality. Until Task 4 records that decision, the existing rule holds
and no source text is committed.

## Global Constraints

- **Every boundary that exists stays.** The archive and OOXML boundary is the
  evidence behind route C; the RFC boundary is the evidence behind this corpus's
  predecessor. No task here removes, weakens, or bypasses either, and no limit
  in either is raised.
- **The RFC corpus is retained, not deleted.** Its manifests, its source-terms
  assessment, and its parse path stay as the record of what was frozen and
  measured. Its three L2 annotation records are superseded by corpus change and
  are neither edited nor reused; Task 7 records that plainly.
- Everything provider-side is corpus independent and is not touched: the egress
  policy and its caps, the atomic ledger, the policy-bound transport, the HTTP
  provider adapter and its live smoke, both evidence indexes, and the API-policy
  conclusion gate.
- `source-manifest/v1` and `/v2` stay byte-identical. Existing manifests, their
  canonical bytes, and their IDs are unchanged by every task here.
- No successor manifest is created. IIIF source manifests are default-deny like
  every other initial manifest, and this plan does not authorize egress.
- Restricted directories stay `0700` and restricted files `0600`, with
  no-replace publication and symlink refusal, exactly as W0 established.
- **No quality metric is produced in R1.** Not retrieval, not answers. This plan
  freezes a corpus and proves it parses.
- The frozen pair is **Presentation API 3.0** and **Image API 3.0**, mirroring
  the overall-description-plus-one-specific-layer shape used twice before.
  Content Search and Authorization are deliberate extension points, not scope.
- Per-document outbound caps are one fifth of each document's measured indexable
  text, by the same derivation as the RFC caps, and no document is disclosable
  until it is priced. Task 6 measures; nothing is carried over.

## File map locked for R1

- `src/specpilot/contracts/html.py` — HTML limits and refusal codes.
- `src/specpilot/ingestion/html.py` — the bounded, single-snapshot verification
  boundary.
- `src/specpilot/iiif/structure.py` — sections, clauses, cross-references, and
  normative keywords over verified HTML.
- `src/specpilot/corpus/clauses.py` — extended, not replaced: the clause model
  is source-format independent and already is.
- `src/specpilot/cli.py` — `corpus parse`, `corpus clauses`, `corpus qa`,
  `corpus overlap` retargeted; new `--html` where `--xml` was.
- `docs/compliance/iiif-source-terms.md` — the CC-BY assessment.
- `tests/unit/ingestion/test_html_boundary.py`,
  `tests/unit/iiif/test_structure.py`, `tests/helpers/iiif_factory.py`.

---

### Task 1: Fetch and freeze the two specifications

**Files:**
- Create (never committed): `artifacts/restricted/sources/iiif/presentation-3.0/`
  and `artifacts/restricted/sources/iiif/image-3.0/`

**Interfaces:**
- Produces: two frozen HTML snapshots with recorded SHA-256, fetch time, and URL.

Network on this machine requires an explicit proxy; the CLI does not read the
system setting. The fetch command records the URL and hash it actually got, so a
proxy that rewrites content produces a hash mismatch rather than a silent
substitution.

- [ ] **Step 1: Fetch both documents and record URL, time, and SHA-256**

- [ ] **Step 2: Freeze under `0700`/`0600` with no-replace publication**

- [ ] **Step 3: Record the byte size and confirm both are within the limits
      Task 2 will set** — a document that only parses with a raised ceiling is a
      finding, not a configuration step.

- [ ] **Step 4: Count sections, headings, and body paragraphs, and gate on the
      total**

The corpus being replaced held 1,907 clauses. IIIF's volume has never been
measured, and a much smaller corpus is a real risk: 40 L1 items over too few
clauses oversamples the same material, which is the failure the current three L2
records already show in miniature — all three anchored to one clause in one
section.

A rough count is enough here and does not need Task 5's parser. **If the two
documents together yield fewer than roughly 600 body paragraphs, stop and report
before building anything**, because the remedy is a corpus decision — adding
Content Search and Authorization, or reconsidering — and it is far cheaper
before the boundary and the parser exist than after.

---

### Task 2: The HTML verification boundary

**Files:**
- Create: `src/specpilot/contracts/html.py`, `src/specpilot/ingestion/html.py`
- Test: `tests/unit/ingestion/test_html_boundary.py`

**Interfaces:**
- Produces: `HtmlLimits`, `UnsafeHtmlError`, `verify_html_snapshot(...)`,
  `read_html_snapshot(...)`, mirroring `ingestion.rfc`'s shape exactly.

The RFC boundary's discipline transfers whole and is not re-litigated: one
bounded `O_NOFOLLOW` snapshot, manifest hash compared before the bytes are
interpreted, and every downstream consumer reading that same snapshot rather
than reopening the path.

What differs is the risk surface. HTML admits scripts, iframes, embedded
objects, external resource references, `javascript:` URLs, and event-handler
attributes. `html.parser` from the standard library is used rather than a new
dependency, because the parser's job here is to *refuse*, not to render: an
allowlist of elements and attributes is a smaller and more auditable thing than
a sanitiser's denylist.

- [ ] **Step 1: Write failing tests for each refusal**

Non-regular file, symlink, missing `O_NOFOLLOW`, oversized, growing during read,
hash mismatch, invalid UTF-8, DOCTYPE other than plain `html`, any entity
declaration, `<script>`, `<iframe>`, `<object>`, `<embed>`, `<link>` to an
external resource, an `on*` attribute, a `javascript:` or `data:` URL, and an
element outside the allowlist. Each gets a stable code.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement the boundary**

- [ ] **Step 4: Verify GREEN against both frozen documents**

Report which constructs each document actually contains. "The documents we
looked at had none" is not a property of the format, and the boundary refuses
all of them regardless.

---

### Task 3: Source manifests for the two documents

**Files:**
- Create: `manifests/local/r1/source/`

**Interfaces:**
- Consumes: `source-manifest/v2`, unchanged.
- Produces: two default-deny manifests.

`source-manifest/v2` was built for a source with no archive and no DOCX, which
describes an HTML specification exactly. If it needs a field it does not have,
that is a finding to report before adding one — a schema version bumped for
convenience is a schema version nobody can compare across corpora.

- [ ] **Step 1: Write failing tests for identity and default-deny**

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Create both manifests and verify `cloud_egress_authorized` is
      false on each**

---

### Task 4: The CC-BY source-terms assessment — OWNER: the author

**Files:**
- Create: `docs/compliance/iiif-source-terms.md`
- Create (never committed): licence snapshots under
  `artifacts/restricted/compliance/iiif-source-terms/`

**This is the author's own judgement, like every assessment before it.** No
external party reviews it, no command in this repository produces one, and a
completed assessment is not approval. Tooling freezes the snapshots and checks
the record is complete; it does not conclude.

- [ ] **Step 1: Freeze CC-BY 4.0, the IIIF disclaimer, and both documents'
      licence lines as hash-recorded snapshots**

- [ ] **Step 2: Write the assessment against the same four-part structure §3.2
      requires** — source-side terms, provider-side terms, the outbound caps as
      a premise of the judgement, and a recorded conclusion.

Two questions this assessment must answer that the RFC one could not:

  1. **Attribution.** CC-BY conditions redistribution on attribution. What
     carries it for an excerpt sent to a provider, and for a clause quoted in a
     report? `VersionMetadata` already travels with every payload; whether that
     satisfies the condition is the author's call to record, not tooling's.
  2. **The single-track decision.** Whether real source text now enters the
     repository. Record the decision and its reasoning either way. If yes,
     §8.0's two-corpus table, §9.6's demo, and the roadmap's "never committed,
     whatever the corpus" constraint all change together, in one commit, with
     this assessment as the reason.

- [ ] **Step 3: Record the conclusion** — OWNER: the author.

---

### Task 5: Sections, clauses, cross-references, and normative keywords

**Files:**
- Create: `src/specpilot/iiif/structure.py`, `tests/helpers/iiif_factory.py`
- Test: `tests/unit/iiif/test_structure.py`

**Interfaces:**
- Produces: `IiifStructure` with sections, cross-references, and normative
  keyword positions — the same shape `RfcStructure` exposes, so `corpus.clauses`
  consumes either without a branch.

- [ ] **Step 1: Write failing tests against a synthetic fixture document**

Assert a numbered heading yields its printed section number and its anchor;
that the anchor derivation is reversible enough to resolve an internal link to a
section; that a link to a missing anchor is reported as dangling rather than
dropped; that an RFC 2119 keyword is found whether it is emphasised or plain;
and that a keyword inside a code block or an example is **not** counted, because
an example of a requirement is not a requirement.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement over the verified snapshot**

- [ ] **Step 4: Measure both documents and report every figure**

Sections, clauses, tables, normative keywords by modality, internal
cross-references, cross-document references, and dangling references. These
replace the "measured starting facts" above.

- [ ] **Step 5: Decide the reference-expansion question on the measurement**

If cross-document references are too few to support §8.4's reference-expansion
metric, say so with the count, and record whether the metric is re-scoped to
internal references or dropped from the first release. Do not add a third
document to rescue the number.

---

### Task 6: Parse QA, clause identity, and the per-document caps

**Files:**
- Modify: `src/specpilot/corpus/qa.py`, `src/specpilot/corpus/clauses.py`,
  `src/specpilot/egress/policies/default-v1.json`, `src/specpilot/cli.py`
- Test: `tests/unit/corpus/`, `tests/unit/egress/test_corpus_document_budget.py`

- [ ] **Step 1: Write failing tests for the QA lines against IIIF**

Section numbering against the document's own printed numbers, cross-reference
targets resolving, table fidelity, uncaptured text, orphan normative keywords,
and `excerpt_fit`. The orphan-normative line needs re-deriving: without
`<bcp14>` the denominator is whatever the keyword matcher finds, and its
precision is now part of what the line measures.

- [ ] **Step 2: Run and verify RED**

- [ ] **Step 3: Implement and run against both frozen documents**

- [ ] **Step 4: Measure the per-document denominators and set the caps**

One fifth of each document's indexable units, tokens, and bytes, by the same
derivation the RFC caps used, with bytes load-bearing and tokens secondary.
Update `MEASURED_CORPUS` and the shipped policy together; the assertion test
turns red if they drift.

- [ ] **Step 5: Verify no indexable unit exceeds the excerpt cap**

If one does, exclude it by content property or split it — and record which,
because the last corpus needed exactly this and the first attempt keyed the
exclusion on a document ID.

---

### Task 7: Retarget the CLI, retire the RFC records, and update the plan

**Files:**
- Modify: `src/specpilot/cli.py`, `docs/runbooks/w1-annotation.md`,
  `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`,
  `SpecPilot_项目方案.md`

- [ ] **Step 1: Retarget `corpus parse`, `clauses`, `qa`, `overlap` to accept
      either corpus** and keep the RFC path working, because the RFC manifests
      remain valid records and a command that cannot read them makes them
      unverifiable.

- [ ] **Step 2: Record the supersession of the three L2 records**

They were adjudicated against RFC 9112 §6.3 and do not transfer. They are not
deleted — the store is create-only and the provenance chain is the audit trail —
but the progress report must not count them toward IIIF targets.

- [ ] **Step 3: Update the runbook with IIIF examples end to end**

- [ ] **Step 4: Annotate the product plan** — §3.2 corpus, §4.1 pipeline,
      §4.6.1's route record, §8.0 and §9.6 if Task 4 collapsed the dual track,
      and §13's domain-knowledge risk row, which is the row this whole plan is a
      response to.

---

### Task 8: The author's first five annotations — OWNER: the author

**Deliberately left unchecked, like W0 Task 8 Step 4 and W1 Task 6.**

- [ ] **Step 1: Annotate five L1 items against the IIIF corpus and record the
      wall-clock time for each.**

The five items matter less than the five timings. Product plan §11 now says
completion is determined entirely by annotation throughput and that the
throughput has never been measured; five timed items convert the schedule from a
guess into a projection. Until they exist, no date from §11 goes into any
outward-facing material.

---

## Plan self-review record

- **Scope decision:** R1 covers fetching, the HTML boundary, manifests, the
  assessment, structure extraction, QA, caps, and retargeting. It produces no
  quality metric and creates no successor manifest.
- **Third corpus, named as such:** the plan says so in its second paragraph
  rather than presenting IIIF as the plan all along. The constraint that bound —
  whether the annotator can adjudicate the material — is now first in the list
  and was absent from both previous selections.
- **Weaknesses recorded before discovery:** absent machine-readable keyword
  markup, cross-reference density roughly an order of magnitude below the corpus
  being replaced, and clause identity that stops being the source's own. The
  second is load-bearing for a named metric, so Task 5 Step 5 forces a recorded
  decision rather than a quiet drop; the third removes an independent QA basis,
  so Task 5 replaces it with an explicit stability test.
- **Comparison stated, not implied:** the plan says outright that the corpus
  being replaced is the better document and that this is a trade of document
  quality for project completion. Presenting a forced swap as an upgrade is the
  kind of claim §8.0 exists to prevent.
- **Volume gated before construction:** IIIF's clause count has never been
  measured, and Task 1 Step 4 stops the plan if it is too small — while the
  remedy is still a corpus decision rather than a rewrite.
- **What is not touched:** every provider-side component, both existing
  ingestion boundaries, and both existing manifest schema versions. The work
  that carries the project's engineering weight is corpus independent and stays
  that way.
- **Author-owned steps marked:** Task 4's conclusion and Task 8 are the author's
  and are not something tooling completes.
- **Placeholder scan:** every implementation step names concrete behaviour,
  files, and verification.
