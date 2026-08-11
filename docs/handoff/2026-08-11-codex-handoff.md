# Handoff — 2026-08-11

Written when the author moved from Claude Code to Codex. Everything below was
measured by running the command shown, not recalled. **Re-run before trusting
any of it** — this file will start going stale the day after it was written, and
`AGENTS.md` tells you the same thing for the same reason.

The one thing that is not a number: this project exists to get its author an
Agent / LLM application engineering internship in China, 正式批, roughly
September–October 2026. That deadline is what makes the ordering in the last
section a recommendation rather than a menu.

---

## Verified state

### Repository

| | |
| --- | --- |
| Branch | `feat/w0-foundation` |
| HEAD | `4f40817` "fix: stop two quantities sharing one ledger column" |
| Working tree | clean |
| **Git remotes** | **none — 0 remotes, 0 pushes, ever** |
| Source | 17,184 lines under `src/` |
| Tests | 23,056 lines under `tests/` |

```bash
git remote -v          # empty
git log --oneline -1
find src -name '*.py' | xargs wc -l | tail -1
```

### Test suite

Three different answers depending on what is configured, which is the point:

| command | result |
| --- | --- |
| `pytest -q` | 1,246 passed, **42 skipped** |
| `+ SPECPILOT_TEST_DSN` | 1,271 passed, **17 skipped** |
| `+ SPECPILOT_TEST_QDRANT_URL` | **1,288 passed, 0 skipped** |

ruff clean, mypy `--strict` clean over 72 source files.

The full-green run appears to be the first time the whole suite has ever
executed. All three runs print "passed". The command that produces the real one,
on a **fresh** database so the migrations are exercised rather than assumed:

```bash
createdb specpilot_scratch \
  && SPECPILOT_TEST_DSN=postgresql:///specpilot_scratch \
     SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 \
     .venv/bin/python -m pytest -q ; \
  dropdb specpilot_scratch
```

**Two real defects were found inside that skip gap on 2026-08-11**, which is the
argument for closing it rather than a story about it:

- A stale assertion (`excerpt_tokens_exceeded`, renamed two commits earlier) that
  only a DSN-enabled run could reach.
- The test harness applied `001_egress_ledger.sql` by name and no other
  migration, so after `002` was added the test schema sat a version behind
  production. It passed locally only because the developer database had been
  migrated by hand; on a fresh database, commit `4f40817` fails. Both are fixed —
  the fixture now reads the whole `migrations/` directory in filename order — but
  the defect was committed and would have been caught by the CI that has never
  run.

### Annotation

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

### Retrieval — dev split, `diagnostic_only: true`, **n = 12 scored items**

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

### Corpus and manifests

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

### Ledger

`specpilot_live`, migrations 001 and 002 applied. It was not modified during
Task 11 verification. The branch adds migration
`003_egress_ledger_policy_successor.sql`; apply it through the normal migration
process before using the new operator command. Corpus-level disclosure so far:
**11 excerpts / 4,269 bytes of RFC 9110** against per-document caps of
314 / 76,113. The licence premise has plenty of headroom and the accounting
survives across evaluation roots.

### What runs, and what is a stub

**Runs end to end, verified live against a real provider on 2026-08-11 — both
directions:**

- *Refusal.* Asked for a status code RFC 9110 does not define, the model returned
  `sufficient: false` rather than supplying the 429 it certainly knows.
- *Answer.* "What must an origin server send with a 405 response?" →
  `answered`, `citation_faults: []`, citing §10.2.1 and §15.5.6, both resolving
  to the clauses that carry the Allow requirement.

**Stubs:** `src/specpilot/api/app.py` is 11 lines and
`src/specpilot/mcp_server/app.py` is 18. The MCP tool surface, the FastAPI L1
API, and the trace page do not exist yet. Per the roadmap that is W3, and it is
the half the job title names.

---

## Open items, and what each one actually blocks

1. **No remote, so CI has never run — not once.**
   `.github/workflows/ci.yml` is well built: it sets `SPECPILOT_TEST_DSN`, runs
   `make integration-db`, `make integration-qdrant` and `make fixture-smoke`, and
   forbids itself from downloading weights, calling a provider, or printing a
   quality number. None of it has ever executed, because there is no remote to
   push to. That is exactly how a stale assertion (`excerpt_tokens_exceeded`,
   renamed two commits earlier) survived: `make check` runs `tests/unit` only,
   and the CI that would have caught it never ran.
   *Also worth saying plainly:* ~37,000 lines of work exists in one directory on
   one disk, with no backup and no link to show anyone.

2. **Task 11 (resolved on the branch) — a corpus ledger bound to one policy had
   no way to be rebound.** Changing caps made the ledger row unusable
   (`policy_snapshot_mismatch`, correct) and the row cannot be deleted either
   (foreign key from `egress_reservation`, also correct). Together, a dead end.
   That incident remains the rationale for the fix: migration 003 and
   `specpilot egress rebind-policy` now create an immutable successor carrying
   the new `policy_hash`, inherited corpus and per-document usage, and a pointer
   to the epoch it supersedes. Ordinary mismatches still fail closed.
   *This is not theoretical* — it constrained a real decision on 2026-08-11 by
   ruling out renaming any policy field.

   Fresh branch evidence: the focused CLI/successor/no-plaintext command passed
   **18 tests**; `make check` passed **1,067** unit tests with **2 skipped** after
   clean Ruff and strict mypy; `make integration-db` passed **43** with **17
   Qdrant-only skips** and no missing-DSN skip; and separate-database
   `make fixture-smoke` passed **5** with **4 deselected**, using no real
   provider route. Commits: `e731ec3`, `767f2b8`, `55a99fe`, `594dcd5`,
   `3d34198`, `6c9fa4b`.

3. **No rule for requirements that span consecutive paragraphs.** The one known
   instance is *closed*: the §8.2.3 pooling completeness audit on 2026-08-09
   (run `603777f3…`) adjudicated all 20 L1 items, extended `l1-dev-010`'s gold
   with §15.4.5 ¶2, removed nothing, and left L1 `awaiting_adjudication` at 0.
   What was never written is the general rule. A clause is a paragraph and a
   forced choice takes one, so the next requirement split across an obligation
   and the list it introduces will be recorded wrong the same way — and only
   caught if another completeness audit happens to run. This is a protocol gap,
   not a data defect.

4. **`deep_review_coverage: 1.0` is not evidence a deep review happened**, and
   the live plan says so about its own design. Sampled items took a median of 24
   seconds against 22 for the rest — indistinguishable.

5. **Deferred integrity check** (`2026-08-08-gold-provenance-v2.md`): source-aware
   entry verifies Gold IDs but does not verify that each supplied
   `gold_section_paths` value matches its Gold ID's actual section path.

6. **Unanswerable floors unmet**: L1 locked 2/5, L2 dev 1/2. §8.1 requires these
   to be deliberately constructed, not harvested from failed scenario-first
   items.

---

## Recommended path

Ordered, with the reason each one sits where it does. Items 1 and 2 are cheap and
unblock or protect everything after them.

### 1. Push to a remote — first, and today

Everything else is at risk until this is done, and it costs minutes. It also
gives CI its first run, which will independently validate the tree instead of
taking a local `make check` on faith. Expect the first run to surface something;
that is the point.

`.gitignore` was checked on 2026-08-11 and already excludes everything that must
not leave: `artifacts/restricted/` (the RFC sources and annotation records),
`manifests/local/`, `data/` (weights and caches), and `tmp/`. Confirm with
`git status --ignored --short` before the first push rather than after it — a
push is not undoable by deleting the repository.

### 2. The defence pass — before any new depth

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

### 3. Task 11 completed — the corpus ledger successor row

Implemented by migration `003_egress_ledger_policy_successor.sql` and
`.venv/bin/python -m specpilot.cli egress rebind-policy`. The operator must name
the expected active policy hash, and a completed retry safely reports
`unchanged`. After success, use a new evaluation-root ID; old roots remain bound
to their original epoch. A tighter successor policy blocks later reservations
instead of deleting the inherited audit trail.

### 4. W3 — MCP tools, FastAPI L1 API, trace page

The largest remaining piece and the half the job title names. Both entry points
are stubs today (11 and 18 lines). The roadmap's W3 scope: expose the five
read-only capabilities over Streamable HTTP MCP, implement the typed
Orchestrator/Evidence flow with budgets and traces, and route every real provider
call through the existing ledger and enforcer — which already work, so this is
surface over a foundation rather than new foundation.

### 5. Annotation, resumed in the order the floors demand

L1 locked 5/25 and its unanswerable floor at 2/5; L2 3/20 with all three items
awaiting adjudication and its dev floor at 1/2. Take the unanswerable items
first: §8.1 requires them to be **deliberately constructed** — questions that are
out of scope, cross-specification, or need a version not in the corpus — and not
harvested from scenario-first items that happened to fail. Write the
consecutive-paragraph rule (open item 3) before the next batch rather than after,
so it governs the items instead of being retrofitted onto them.

### Not on this path

- **Do not re-run or extend the retrieval evaluation to make the numbers look
  better.** It is `diagnostic_only` on a dev split for a reason, and the locked
  splits are W6's, first-run once.
- **Do not raise a cap to make something fit.** Every cap traces to the recorded
  §3.2 compliance decision; raising one invalidates the premise and the decision
  must be made again rather than inherited.
