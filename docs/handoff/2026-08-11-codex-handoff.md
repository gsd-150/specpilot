# Handoff — 2026-08-11, reconciled after W3 on 2026-08-13

Written when the author moved from Claude Code to Codex, then reconciled after
the W3 MCP/API/trace slice was completed and merged into `main`. Measurements
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
| Default branch | `main`; W3 merged by PR #1 at `96b13eb` |
| Current repository base | `ad342dc`, a docs-only annotation-plan commit; code tree matches the W3 merge |
| Tracking before these uncommitted corrections | local `main` is two commits ahead of `origin/main` (`842f978`, `ad342dc`) |
| Git remote | `origin` exists and local `main` tracks `origin/main` |
| Feature publication | PR [#1](https://github.com/gsd-150/specpilot/pull/1) merged and is closed |
| CI | PR, push, and merge-commit runs passed all seven jobs, including integration, browser, and image builds |

The W3 implementation is part of the default branch. There is no remaining W3
review/merge boundary. Two later docs-only commits, `842f978` and `ad342dc`, had
not yet been pushed to `origin/main` when this reconciliation was completed.

### Test suite

Fresh local checks rerun on the current code tree during the 2026-08-13
documentation reconciliation:

| command | result |
| --- | --- |
| `make check` | Ruff clean; strict mypy clean over 94 source files; 1,421 unit + 178 CLI = **1,599 passed** |
| PostgreSQL/Qdrant full gate | **Not rerun in this reconciliation:** both local services were unavailable |
| `npm test -- --run` / build | **Not rerun in this reconciliation:** `web/trace/node_modules` was absent; the last CI-backed record remains 106 Vitest tests plus a successful production build |

The CI-portability repair was previously verified against disposable PostgreSQL and
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

The code on `main` exposes five read-only tools over real Streamable HTTP
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

The pre-W3 API and MCP stubs have been replaced on `main`. The next
product slices remain SSE and reconnect semantics, L2/Compliance, checkpoint
recovery, the full W5 fixture matrix, and locked W6 evaluation. See
`docs/reports/w3-mcp-api-trace-report.md` for the W3 evidence and limitations.

---

## Closed during the 2026-08-13 reconciliation

- **W3 integration:** PR #1 merged at 04:50 as `96b13eb`; the merge-commit CI
  gate was green. An independent fresh-database/Qdrant rerun recorded 1,856
  passed, zero skipped, with Ruff and strict mypy clean over 94 source files.
- **Deep-review evidence:** this was a real defect in the first choice pass, but
  it is no longer open. Five separately stored `DeepReviewFinding` records now
  cover the 5/5 preregistered sample; the aggregate command reports
  `deep_review_coverage=1.0`, median 91 seconds and minimum 34 seconds. The old
  24-second-vs-22-second comparison remains useful incident history, not current
  evidence.
- **Consecutive-paragraph Gold rule:** Task 12 now defines Gold as the minimal
  clause set required to answer the question, makes the paragraph carrying the
  normative keyword mandatory, and uses multi-clause Gold when the answer truly
  spans clauses. The rule already caught the analogous stem/list shape at
  RFC 9110 §12.5.5 during the second L1 drafting pass.

## Open items, and what each one actually blocks

1. **Deferred integrity check** (`2026-08-08-gold-provenance-v2.md`): source-aware
   entry verifies Gold IDs but does not verify that each supplied
   `gold_section_paths` value matches its Gold ID's actual section path.

2. **Formal-store unanswerable floors unmet**: L1 locked remains 2/5 and L2 dev
   remains 1/2. The second L1 drafting pass contains three deliberately checked
   locked unanswerables and therefore meets 5/5 at proposal level, but they are
   not Gold until the author reviews them. L2 dev still needs one deliberately
   constructed unanswerable.

3. **Post-W3 product work remains.** SSE and reconnect semantics, L2/Compliance,
   checkpoint recovery, the complete W5 demo matrix, and locked W6 evaluation
   are not part of the completed W3 slice.

---

## Recommended path

Ordered by delivery risk and the September–October interview deadline.

### 1. Keep the default branch and its state documents aligned

W3 is already integrated. Review and publish the local documentation commits
when intended; do not reopen or recreate the feature PR. Before any push,
continue confirming that `artifacts/restricted/` (RFC sources and annotation
records), `manifests/local/`, `data/` (weights and caches), and `tmp/` remain
ignored and untracked.

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

L1's formal store remains locked 5/25 with its unanswerable floor at 2/5, but
the remaining 20 locked proposals are drafted and verified; their three new
unanswerables meet the 5/5 floor at proposal level. The next L1 act is the
author-owned Task 7 review, not more drafting. L2 remains 3/20 with all three
items awaiting adjudication and its dev floor at 1/2; its next batch still needs
one deliberately constructed unanswerable rather than one harvested from a
scenario-first item that happened to fail.

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
