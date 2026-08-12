# Handoff — 2026-08-11, reconciled after W3 on 2026-08-13

Written when the author moved from Claude Code to Codex, then reconciled after
the W3 MCP/API/trace slice was completed on its feature branch. Measurements
that still describe the 2026-08-11 checkout are labelled as historical
snapshots; the current delivery state is stated separately. **Re-run before
trusting any number** — `AGENTS.md` requires fresh evidence for the same reason.

The one thing that is not a number: this project exists to get its author an
Agent / LLM application engineering internship in China, 正式批, roughly
September–October 2026. That deadline is what makes the ordering in the last
section a recommendation rather than a menu.

---

## Current delivery state — 2026-08-13

### Repository

| | |
| --- | --- |
| Feature branch | `feat/w3-mcp-api-trace` |
| Verified code HEAD | `6d5ad3f` "fix: remove local-only CI assumptions" |
| Local main | `f8f0b1d`; W3 is not merged into the default branch |
| Working tree | clean after the current handoff update |
| Git remote | `origin` exists and local `main` tracks `origin/main` |
| Feature publication | tracks `origin/feat/w3-mcp-api-trace`; draft PR [#1](https://github.com/gsd-150/specpilot/pull/1) is open |
| CI | PR and push runs for `6d5ad3f` both passed all seven jobs, including integration, browser, and image builds |

The feature branch is implemented, published, and verified locally and in
GitHub Actions, but it is not merged into `main`. The remaining publication
boundary is review, moving the draft PR to ready when appropriate, and merging
it into the default branch.

### Test suite

Fresh local W3 checks on 2026-08-13:

| command | result |
| --- | --- |
| `make check` | Ruff clean; strict mypy clean over 94 source files; 1,597 unit and CLI tests passed, 2 restricted-fixture tests skipped |
| `npm test -- --run` | 106 passed |
| `npm run build` | TypeScript and Vite production build passed |

The CI-portability repair was then verified against disposable PostgreSQL and
Qdrant services: the complete integration suite passed 248 tests with zero
skips, and the real Playwright fixture flow passed one browser case. GitHub's
PR and push workflows independently passed the same seven-job matrix, including
the database/Qdrant integration gate, browser flow, and API/MCP/ingestion image
builds. The W3 release report retains the earlier release-gate evidence for
provenance. For any later code change, rerun the following full local gate:

```bash
createdb specpilot_scratch \
  && SPECPILOT_TEST_DSN=postgresql:///specpilot_scratch \
     SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 \
     .venv/bin/python -m pytest -q ; \
  dropdb specpilot_scratch
```

The reason the full gate matters is historical: two defects were found inside
the skip gap on 2026-08-11:

- A stale assertion (`excerpt_tokens_exceeded`, renamed two commits earlier) that
  only a DSN-enabled run could reach.
- The test harness applied `001_egress_ledger.sql` by name and no other
  migration, so after `002` was added the test schema sat a version behind
  production. It passed locally only because the developer database had been
  migrated by hand; on a fresh database, commit `4f40817` fails. Both are fixed —
  the fixture now reads the whole `migrations/` directory in filename order — but
  the defect was committed before a fresh migration run caught it.

### Annotation — freshly recounted 2026-08-13

`.venv/bin/python -m specpilot.cli annotation progress --annotation-dir artifacts/restricted/annotations`

| | done / target | notes |
| --- | --- | --- |
| L1 total | **20 / 40** | 16 gold clauses |
| L1 dev | **15 / 15 — complete** | this is why retrieval could be scored at all |
| L1 locked | 5 / 25 | |
| L1 clause-first share | 0.60 / 0.60 target | met |
| L1 unanswerable, dev | 3 / 3 | met |
| L1 unanswerable, locked | 2 / 5 | **not met** |
| L2 total | **3 / 20** | all 3 awaiting adjudication |
| L2 dev | 3 / 8 | |
| L2 locked | 0 / 12 | |
| L2 unanswerable, dev | 1 / 2 | **not met** |

L2 gold chains record `model_proposal@openai-codex > human_source_review`, so
Codex has produced L2 annotations here before; L1 chains record
`model_proposal@claude-opus-5`. 23 items annotated, 20 superseded.

### Retrieval — 2026-08-11 snapshot, `diagnostic_only: true`, **n = 12 scored items**

`artifacts/restricted/eval-retrieval-dev-2026-08-09.json`

| route | hit@5 | macro recall | MRR |
| --- | --- | --- | --- |
| BM25 | 0.833 | 0.792 | 0.792 |
| dense | 0.917 | 0.875 | 0.847 |
| RRF | 0.917 | 0.875 | 0.856 |

`all_required_hit_rate` is **0.0** on every route, and that is not a bug. There
is exactly one multi-gold item — `l1-dev-010`, the split requirement at RFC 9110
§15.4.5, where ¶2 states the obligation and ends in a colon and ¶3 is the list it
introduces. Retrieval finds one of the two at k=5, which is the honest score for
a requirement that spans two paragraph anchors. Three further metrics are
deliberately not reported, each with its reason recorded in the file.

**These numbers are not résumé numbers.** n = 12, one annotator, binary labels,
`diagnostic_only`. Quoted without those qualifiers they are a claim the data
cannot support.

### Corpus and manifests — 2026-08-11 snapshot

- Corpus manifest `1abafff704358c2357ead5b837d212f130cadfa330dfa30d1df0a24f76d74295`,
  sealing 1,922 points in `specpilot_ff4841e2d846388014efa06870fbbdb7`.
- Four source manifests in `manifests/local/r0/source/`: two default-deny roots
  (`af230fed…` 9110, `3a752dd9…` 9112) and two authorized successors
  (`c42813e7…` 9110, `b74abd04…` 9112).
- **Authorization expires 2026-11-08.** Author `chunxue`, provider `deepseek`,
  endpoint `online-main`. That covers 正式批, but only just — an expiry during
  interview season would take the demo offline.
- RFC sources present at `artifacts/restricted/sources/ietf/` (gitignored,
  1.2 MB and 263 KB), BGE-M3 weights 2.1 GB at `data/cache/models/bge-m3`.

### Ledger — 2026-08-11 snapshot plus current operator boundary

At the time of the snapshot, `specpilot_live` had migrations 001 and 002 and was
not modified during Task 11 verification. The W3 branch adds migrations 004 and
005 after Task 11's migration 003. These are versioned operator artifacts: the
wheel and API image do not apply them automatically. An existing corpus ledger
must receive migration 003 and an explicit policy successor/rebind before the
planning stage can use the W3 policy. The recorded 2026-08-11 corpus disclosure
was:
**11 excerpts / 4,269 bytes of RFC 9110** against per-document caps of
314 / 76,113. The licence premise has plenty of headroom and the accounting
survives across evaluation roots.

### What runs, and what remains

The W3 feature branch exposes five read-only tools over real Streamable HTTP
MCP, runs the model-authored bounded L1 plan through the PostgreSQL disclosure
ledger and verifier, accepts owner-bound asynchronous runs over FastAPI, and
serves a packaged React trace page with a 60-second polling limit. Refusal,
disclosure block, provider failure, and expired-lease interruption remain
distinct. The W3 browser closure uses an HTTP-only fixture cookie and a
synthetic local corpus; it makes no real provider call.

**Runs end to end, verified live against a real provider on 2026-08-11 — both
directions:**

- *Refusal.* Asked for a status code RFC 9110 does not define, the model returned
  `sufficient: false` rather than supplying the 429 it certainly knows.
- *Answer.* "What must an origin server send with a 405 response?" →
  `answered`, `citation_faults: []`, citing §10.2.1 and §15.5.6, both resolving
  to the clauses that carry the Allow requirement.

The pre-W3 API and MCP stubs have been replaced on the feature branch. The next
product slices remain SSE and reconnect semantics, L2/Compliance, checkpoint
recovery, the full W5 fixture matrix, and locked W6 evaluation. See
`docs/reports/w3-mcp-api-trace-report.md` for the W3 evidence and limitations.

---

## Open items, and what each one actually blocks

1. ~~**W3 is not integrated into `main`.**~~ **Closed 2026-08-13.** PR #1 merged
   at 04:50; `main` is `96b13eb` and matches `origin/main`, CI green on the merge
   commit. This item was written ten minutes before the merge — a reminder that
   the fastest-moving lines in this file are the ones about its own state.

   Re-verified independently on 2026-08-13: **1,856 passed, 0 skipped, 0 failed**
   on a fresh database with Qdrant up, ruff clean, mypy `--strict` clean over 94
   source files.

2. **No rule for requirements that span consecutive paragraphs.** The one known
   instance is *closed*: the §8.2.3 pooling completeness audit on 2026-08-09
   (run `603777f3…`) adjudicated all 20 L1 items, extended `l1-dev-010`'s gold
   with §15.4.5 ¶2, removed nothing, and left L1 `awaiting_adjudication` at 0.
   What was never written is the general rule. A clause is a paragraph and a
   forced choice takes one, so the next requirement split across an obligation
   and the list it introduces will be recorded wrong the same way — and only
   caught if another completeness audit happens to run. This is a protocol gap,
   not a data defect.

3. **`deep_review_coverage: 1.0` is not evidence a deep review happened**, and
   the live plan says so about its own design. Sampled items took a median of 24
   seconds against 22 for the rest — indistinguishable.

4. **Deferred integrity check** (`2026-08-08-gold-provenance-v2.md`): source-aware
   entry verifies Gold IDs but does not verify that each supplied
   `gold_section_paths` value matches its Gold ID's actual section path.

5. **Unanswerable floors unmet**: L1 locked 2/5, L2 dev 1/2. §8.1 requires these
   to be deliberately constructed, not harvested from failed scenario-first
   items.

6. **Post-W3 product work remains.** SSE and reconnect semantics, L2/Compliance,
   checkpoint recovery, the complete W5 demo matrix, and locked W6 evaluation
   are not part of the completed W3 slice.

---

## Recommended path

Ordered by delivery risk and the September–October interview deadline.

### 1. Review and integrate PR #1

The branch is published and both PR and push CI runs are green. Review the PR,
move it out of draft when appropriate, and integrate it into `main`. Do not
describe W3 as shipped from the default branch until this boundary is closed.

`.gitignore` was checked on 2026-08-11 and already excludes everything that must
not leave: `artifacts/restricted/` (the RFC sources and annotation records),
`manifests/local/`, `data/` (weights and caches), and `tmp/`. Confirm with
`git status --ignored --short` before publishing the feature branch.

### 2. Preserve the verified environment boundary

The earlier local Docker Hub DNS failure remains useful environment history,
but it is no longer a release blocker: disposable PostgreSQL/Qdrant verification
passed and GitHub CI built the API, MCP, and ingestion images. Keep migrations
003–005 explicit, continue using fresh services for release gates, and never
point verification at `specpilot_live`.

### 3. The defence pass — before any new depth

**This is the highest-value item in the list and the easiest to skip.** The
project is now demonstrable in both directions against a real provider, which is
the anchor to defend against; defending a system you can run is far easier than
defending infrastructure.

The risk is not difficulty. It is that essentially every design decision in the
repo was agent-authored, and an interviewer's second question goes one level
below "介绍一下你的项目". Nobody in 2026 minds that AI was used — what gets
tested is whether you understand what you shipped.

Use `llm-interview-coach` or `resume-interview-skill`. Generate hard follow-ups
on the parts you did not participate in, answer them out loud, find the holes.
Good questions to start with, all from work done 2026-08-10/11 and all of which
have precise answers:

- Why is the root-level unique cap 10? (5 online + 5 judge, for **one** case.)
- Why does the model cite a content hash instead of a clause number? (It binds
  the citation to the bytes actually disclosed; and the model cannot invent a
  locator it was never shown.)
- Why was `unknown_clause` a name that had always been false? (The checker only
  ever held the disclosed set; it never consulted the corpus, so it could not
  distinguish "invented" from "real but unsent".)
- Why can't `transmitted_bytes` and `request_bytes` be one number? (One is
  corpus content counted with repetition and binds a cap; the other is the wire.
  Merging them makes "four times the unique cap" describe nothing.)
- Why is bytes the load-bearing dimension and not tokens? (Exact and
  tokenizer-independent, so the one-fifth licence argument needs no conversion.)

If the holes are large, change how the work is done from here: have the agent
explain the design and get your judgement *before* implementing, rather than
writing it up in a commit message afterwards. Half of what is already built —
provenance chains, three-layer manifests, gold protocols, disclosure limits — is
squarely Knowledge Management, which is your field and not a generic CS
candidate's.

### Resolved foundation: Task 11 ledger policy successors

Implemented by migration `003_egress_ledger_policy_successor.sql` and
`.venv/bin/python -m specpilot.cli egress rebind-policy`. The operator must name
both the exact active ledger UUID and its policy hash. The UUID is authoritative;
hash alone never identifies a completed retry, which is `unchanged` only when
the active epoch directly supersedes that exact UUID under the requested new
policy. A→B→A is legal and creates three distinct epochs. After success, use a
new evaluation-root ID; old roots remain bound to their original epoch. A
tighter successor policy blocks later reservations instead of deleting the
inherited audit trail.

Migration 003 is a separate versioned operations artifact from the repository
checkout; the current wheel/API image contains no migrations. Apply it from the
matching checkout with
`psql "$SPECPILOT_LEDGER_DSN" -v ON_ERROR_STOP=1 -f migrations/003_egress_ledger_policy_successor.sql`.

### 4. Annotation, resumed in the order the floors demand

L1 locked 5/25 and its unanswerable floor at 2/5; L2 3/20 with all three items
awaiting adjudication and its dev floor at 1/2. Take the unanswerable items
first: §8.1 requires them to be **deliberately constructed** — questions that are
out of scope, cross-specification, or need a version not in the corpus — and not
harvested from scenario-first items that happened to fail. Write the
consecutive-paragraph rule (open item 3) before the next batch rather than after,
so it governs the items instead of being retrofitted onto them.

### 5. Continue with W4 and W5

Add L2/Compliance and checkpoint recovery, then SSE/reconnect and the complete
fixture demo matrix. Preserve the roadmap boundary: locked test evaluation stays
first-run work for W6 and must not feed configuration changes.

### Not on this path

- **Do not re-run or extend the retrieval evaluation to make the numbers look
  better.** It is `diagnostic_only` on a dev split for a reason, and the locked
  splits are W6's, first-run once.
- **Do not raise a cap to make something fit.** Every cap traces to the recorded
  §3.2 compliance decision; raising one invalidates the premise and the decision
  must be made again rather than inherited.
