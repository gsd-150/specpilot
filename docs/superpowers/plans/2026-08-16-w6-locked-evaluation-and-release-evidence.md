# W6 Locked Evaluation and Release Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the locked L1, L2, and L2-adv sets exactly once against the frozen run spec, score and audit that output, run the two registered core comparisons, and seal a release report whose every number carries the conditions under which it is true.

**Architecture:** Promote the author's `tmp/` batch drivers into reviewed, tested, split-aware code before a single locked call is spent; rehearse the whole W6 sweep shape on the dev split; then run locked once. The two core comparisons are separate registered treatments that run only after the default chain's first-run number exists, and never mix into the audit population.

**Tech Stack:** Python 3.12+, Pydantic v2, psycopg 3, PostgreSQL 17, Qdrant 1.12.4, BGE-M3 local weights, cloud `deepseek-v4-flash` main route, calibrated judge route.

---

## Why this plan is longer than the roadmap line

The roadmap gives W6 three bullets: first-run the locked sets, run the two paired comparisons three times, seal the evidence. Reading the tree against that line found five gaps, and four of them are build work rather than execution:

1. **There is no locked-set runner.** The entire dev batch machinery lives in `tmp/`, which is gitignored: `run_l1_dev.sh`, `run_l2_dev.sh`, `run_judge_dev.sh`, `run_l2_judge.sh`, `dump_dev_items.py`, `prepare_judge_payloads.py`. None of it is in the repository, none of it has a test, and both sweep scripts hardcode `split == "dev"`. This is the same shape AGENTS.md already records for `ask.sh` — a documented command pointing at a file a fresh checkout never had.

2. **`tmp/run_l2_dev.sh` has a defect that only fires under load.** Its retry branch prints `${CODE}`, which the script never assigns, under `set -u`. The first transport-level retry therefore kills the sweep with `CODE: unbound variable` instead of retrying. The branch is reachable only on `provider_unreachable`, `invalid_tool_plan`, or `provider_timeout` — that is, during exactly the long live sweep W6 is.

3. **Neither sweep asserts its expected count.** An item filter that matches nothing prints `running 0 cases` and exits 0. On a one-shot locked run, "expected 12, got 0, exit 0" is the failure mode that looks like success.

4. **Nothing executes an `AdversarialGroup`.** `l2 run` takes `--question` and `--case-id`; the L2-adv contract stores a negative claim and a positive claim per group, with their own clause sets and their own expected verdicts. The 10 locked groups have no execution path at all.

5. **Neither core comparison is implemented.** `grep` finds no `E-context` evidence-expansion arm (§8.5.2 A′) and no gate-only path (§8.5.3 B). Both are W6 locked-set deliverables with zero code behind them.

Executing locked before closing 1–4 means hand-driving 57 live invocations through unreviewed scripts against a one-shot boundary. That is the single largest avoidable risk in this milestone.

## Found while doing Task 1: three frozen identities do not describe the freeze commit

`evaluation freeze-candidate` reads `evaluation-identities.json` through
`_read_pinned_file`, which is careful about the wrong thing. It proves the file
is a bounded regular file whose identity did not change *during the read* — and
never checks that its contents describe the current tree. The identity status
was generated at 00:55 on 2026-08-16 and never regenerated; the freeze ran at
06:47 and pinned it as written.

Recomputed against the tree at `d2998ff`, three of the twelve fields disagree
with the frozen run spec:

| field | covers | why it is stale |
|---|---|---|
| `provider_sha256` | `src/specpilot/providers` | five commits between 03:51 and 06:14 — every L2 wire-contract repair, instance 7 included |
| `scripts_sha256` | `evaluation`, `agents`, `runs` | stale independently of this plan's Task 1; recomputed with the two new files excluded, it still disagrees |
| `sets_sha256` | annotation and group stores | one annotation record written at 01:02 |

`prompts_sha256`, `policy_sha256`, `config_sha256`, `scoring_sha256` all match.

**What this does and does not mean.** The L2 compliance and verifier instruction
text lives in `providers/http.py`, which `_PROMPT_PATHS` does not cover and
`_PROVIDER_PATHS` does — so the stale field is the one carrying the instructions
those seven repairs rewrote. The independent protection held: every L2 outcome
artifact records `compliance_prompt_sha256` at run time, and the sweep verifies
one prompt identity across a batch. What is lost is the freeze's own claim, that
the run spec names the configuration W6 is approved to execute.

**Recommended remedy, author's call.** Regenerate the identity status and
re-freeze at the head of Task 5, before Task 6 runs. Nothing locked has
executed, so no first-run boundary is at stake, and a freeze taken at the
commit that will actually run is worth more than a freeze taken earlier and
explained afterwards. The alternative — run under the existing spec and disclose
the three fields — leaves a report that has to explain a mismatch forever.

Either way `freeze-candidate` should refuse an identity status that does not
match the tree it is freezing. Reading a stale file carefully is not a check.

## Global Constraints

- **The locked default chain runs first, once.** No comparison arm, no re-run, and no reading of locked output happens before that sweep completes and its artifacts are sealed. Everything else in W6 is downstream of that ordering.
- **Everything is validated on dev first.** Any script, arm, or scoring path touches the dev split until it is proven, then locked.
- **Real provider calls belong to `chunxue`.** Prepare the command; the author runs it. `SPECPILOT_MAIN_API_KEY` and `SPECPILOT_JUDGE_API_KEY` by environment only.
- **The freeze is the configuration.** Start from tag `evaluation-freeze-2026-08-16` (commit `d2998ff`), run spec `a4b3f8ca1c34466b60bc407ded38c73d763e07fd3801047a1ea212eb5d5e7cc5`, scoring route `judge_calibrated`. Prompt bytes are bound by `prompts_sha256`; editing a prompt invalidates the freeze rather than fixing anything.
- **Repeats are not samples.** The independent unit is the case or case group: L1-test `n=25`, L2-test `n=12`, L2-adv-test `n=10 groups`. Three paired runs are within-case repeats, and a matched pair is not two independent items. Report per-run raw counts and per-case agreement across the three; never widen `N`.
- **Comparison repeats stay out of the audit population** (§8.5.3). If a comparison's answer-quality score is written as a headline, it needs its own pre-registered manual audit.
- **The report states the same-family bias in prose**, not only in a field. Roadmap non-negotiable, 2026-08-14: 17 of 20 L2 items and all 40 L1 items had scenario, gold, or candidate label proposed by a model of the same family as the system under test, then reviewed item-by-item by a human against the frozen source. These are not unbiased performance estimates, and `label_origin: mixed` recording the fact does not discharge the obligation to say it.
- **Any L2 accuracy statement carries `l2-dev-003`.** Disclosure report §7: the case reaches a correct verdict through a requirement the system never disclosed. Verdict counts are true and overstate by one the cases whose reasoning stayed inside disclosed evidence.
- Preserve every `AGENTS.md` invariant: fail-closed, the enforcer as the only outward path, no source prose in a committable record (`make lint` runs `scripts/check_clause_prose.py`), `evidence_id` never `clause_id`, one `evaluation_root_id` per question.
- No answer, question, rationale, claim, or excerpt enters a git-tracked file. Locked output lives under `artifacts/restricted/`, mode 700.

---

### Task 1: Promote the Batch Drivers into the Repository — DONE (`567fa17`)

**Files:**
- Create: `src/specpilot/evaluation/sweep.py`
- Create: `scripts/run_sweep.sh`
- Create: `tests/unit/evaluation/test_sweep.py`
- Create: `tests/cli/test_sweep_selection.py`
- Modify: `src/specpilot/cli.py`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: `AnnotationStore`, `AdversarialGroupStore`, a split, a level.
- Produces: a deterministic, counted, split-labelled work list, and one artifact per case.

- [x] **Step 1: Write the failing selection tests**

Assert, against synthetic stores: selection by `(level, split)` returns exactly the expected item ids; retired items and superseded records are excluded; an expected-refusal L1 item is included only under an explicit flag; a selection whose size differs from a caller-supplied `expected` refuses with `sweep_count_mismatch` rather than returning a short list; a selection of zero refuses.

The count assertion is the point. `tmp/run_l1_dev.sh` computes `COUNT`, prints it, and never checks it.

- [x] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/unit/evaluation/test_sweep.py -q`

Expected: FAIL, `specpilot.evaluation.sweep` does not exist.

- [x] **Step 3: Implement selection**

`select_cases(store, *, level, split, expected, include_unanswerable=False)` returning an ordered tuple of case descriptors. Sorted by `item_id` so two invocations produce the same order and the ledger's roots stay comparable. No default for `split` — §8.5 keeps the locked splits unread until W6, and a split that can be omitted is one that gets omitted.

- [x] **Step 4: Write the shell driver, fixing both live defects**

`scripts/run_sweep.sh` supersedes `tmp/run_l1_dev.sh` and `tmp/run_l2_dev.sh`. Carry over what those got right — a fresh evaluation root per case, retries only on transport-class failures, the batch prompt-identity coherence guard, refusals logged rather than written as answers — and fix what they got wrong:

- assign the failure code before printing it (the `${CODE}` unbound-variable abort);
- pass `--expected N` through to selection so a miscounted sweep refuses before the first call;
- take `--split` and `--level` as required arguments, echoed into the run log.

- [x] **Step 5: Verify against the dev split, then commit**

Re-run the L1 dev sweep through the new driver and diff the artifact set against the existing `artifacts/restricted/judge/answers`. Identical item coverage is the acceptance condition; answers themselves will differ and that is expected of a live route.

Record in `AGENTS.md` that the sweep driver is `scripts/run_sweep.sh` and that `tmp/` copies are superseded.

```bash
git add src/specpilot/evaluation/sweep.py scripts/run_sweep.sh tests AGENTS.md src/specpilot/cli.py
git commit -m "feat: run an evaluation split from the repository, not from tmp"
```

### Task 2: Execute an Adversarial Group — DONE (`567fa17`)

**Files:**
- Create: `src/specpilot/evaluation/adversarial_run.py`
- Create: `tests/unit/evaluation/test_adversarial_run.py`
- Modify: `src/specpilot/cli.py`
- Modify: `scripts/run_sweep.sh`

**Interfaces:**
- Consumes: an `AdversarialGroup`, the frozen L2 chain.
- Produces: two outcomes per group, joined by `group_id`, plus a pair-level result.

- [x] **Step 1: Decide and record what a group run means**

A group is one independent unit and two invocations. The negative claim must reach `insufficient_evidence`; the positive claim must reach `proposed_verdict`. The pair result is `both`, `negative_only`, `positive_only`, or `neither` — reported as a matched-pair confusion matrix, never as 20 independent items (§8.5.4).

Each claim gets its own `evaluation_root_id`: a root is one question (§3.2), and the ledger refuses a second question under a reused root.

- [x] **Step 2: Write the failing tests**

Assert: a group produces exactly two outcomes with distinct roots and distinct run ids; the two outcomes are joinable by `group_id`; a group whose negative claim returns a determinate verdict is recorded as a false confirmation rather than an error; a missing outcome refuses the pair rather than scoring the half that ran.

- [x] **Step 3: Run RED**

Expected: FAIL, no adversarial execution path.

- [x] **Step 4: Implement, and wire `--level l2-adv` into the sweep driver**

- [x] **Step 5: Rehearse on the six dev groups**

The dev groups exist precisely so the locked ten are not the first thing this code ever runs. Any defect found here is free; the same defect found on locked costs the first-run boundary.

```bash
git commit -m "feat: run a matched adversarial pair as one unit"
```

### Task 3: Dress-Rehearse the Whole W6 Sweep on Dev — DONE

All three live dev sweeps run — L1 12, L2 8, L2-adv 6 groups. Four driver
defects found and fixed before any locked call (worktree manifest resolution,
a prompt-identity guard that passed against zero artifacts, an interpreter that
would have run the main checkout's code, a refusal that created a directory
under `locked/`). See `docs/reports/2026-08-16-w6-dev-rehearsal.md`.

**Files:**
- Create: `docs/reports/2026-08-16-w6-dev-rehearsal.md`

**Interfaces:**
- Consumes: the promoted drivers, the dev splits.
- Produces: a timing, cost, and failure-rate estimate for the locked run, and a list of everything that broke while it was still cheap.

- [ ] **Step 1: Run all three dev sweeps end to end in one session**

L1 dev 15 (12 answerable + 3 expected-refusal), L2 dev 8, L2-adv dev 6 groups = 12 invocations. Thirty-five invocations, the same shape as locked's fifty-seven.

- [ ] **Step 2: Record what locked will cost**

Wall-clock per case, tokens per case, provider spend, retry rate, and the failure classes actually observed. The locked run is one-shot; going in without an expected duration means no way to tell a hung sweep from a slow one.

- [x] **Step 3: Verify the prompt identity is single across the batch**

One `compliance_prompt_sha256` and one `verifier_prompt_sha256` for every artifact. A mixed batch is not a result, and the guard exists because a mid-sweep code change once produced one.

- [x] **Step 4: Confirm HEAD does not move during a sweep**

The W5 gate was voided once for this. Record `git rev-parse HEAD` before and after.

### Task 4: Implement Core Comparison A′ — Evidence Budget Allocation — DONE (`ee579a0`)

**Files:**
- Create: `src/specpilot/retrieval/expansion.py`
- Create: `tests/unit/retrieval/test_expansion.py`
- Modify: `src/specpilot/cli.py`

**Interfaces:**
- Consumes: retrieved clauses, the frozen egress caps.
- Produces: an `E-context` excerpt set that fills the same quota differently.

- [ ] **Step 1: Write the failing tests**

`E-narrow` is current behaviour: each hit clause goes out alone. `E-context` expands each hit to its section-adjacent clauses in section order until it reaches 5 excerpts or 2560 tokens, whichever comes first.

The caps are not a parameter of this comparison. Assert that `E-context` never exceeds 512 tokens or 8 KiB per excerpt, never exceeds the run-level 5 / 2560, and that the gate needs no modification. §8.5.2 rejected `E-1024` precisely because an arm that requires widening the gate is an arm that destroys the project's central claim; an arm that fills an under-used quota does not.

Assert the stratification key: whether the item's gold clause has an adjacent clause inside its section. Items where the two arms emit byte-identical payloads must be counted and listed, not silently averaged in.

- [ ] **Step 2: Run RED, then implement**

- [ ] **Step 3: Validate the stratification on dev**

Report how many dev items `E-context` actually expands. If that number is small, the effect-size ceiling is small, and the report says so in the same sentence as the result — the same failure §8.5.2 already suffered once, when the original W-head/W-query arms turned out to have an empty stratum because no retrievable unit exceeds 512 tokens.

```bash
git commit -m "feat: add the E-context evidence budget arm"
```

### Task 5: Implement Core Comparison B — Verifier Gate-Only — DONE (`711559d`)

**Files:**
- Create: `src/specpilot/verifier/gate_only.py`
- Create: `tests/unit/verifier/test_gate_only.py`
- Modify: `src/specpilot/runtime/l2.py`
- Modify: `src/specpilot/cli.py`

**Interfaces:**
- Consumes: a persisted pre-Verifier candidate artifact.
- Produces: paired `on`/`off` verdicts over one identical artifact hash.

- [ ] **Step 1: Persist the pre-Verifier candidate**

Claim, `proposed_verdict`, evidence ids and hashes, rationale — hashed. Both arms consume the same artifact and neither re-retrieves, which is what isolates the semantic gate from retrieval. Assert the artifact hash is identical across the pair.

- [ ] **Step 2: Write the failing tests**

`off` runs the same deterministic checks as `on` — citation existence, manifest and content-hash, applicability — and then keeps the candidate verdict. `on` runs those checks and then the semantic support gate, downgrading to `insufficient_evidence` on failure. Artifacts failing the deterministic checks are excluded from the pair and counted separately with their reason.

- [ ] **Step 3: Run RED, then implement**

- [ ] **Step 4: Write the reporting constraint into the module docstring**

The `off` arm's L2-adv result is close to construction-determined: that subset is built so citations exist, versions match, and semantics do not support — so the deterministic checks pass by design and `off` necessarily returns a determinate verdict. The measured quantity is the `on` arm's semantic accuracy, which §8.1.1's direct-fed matched pairs already measure under hand-picked distractors. This comparison adds the end-to-end reading with real retrieved evidence, and the report merges all three into one narrative rather than listing them as three selling points.

Putting this in the docstring rather than only in the plan is deliberate: the person writing the report will be reading the code.

```bash
git commit -m "feat: add the Verifier gate-only comparison arm"
```

### Task 6: Execute the Locked Default Chain — First Run

**Author-owned. Real provider calls. This is the one-shot.**

**Files:**
- Produces: `artifacts/restricted/locked/l1/`, `artifacts/restricted/locked/l2/`, `artifacts/restricted/locked/l2-adv/`

- [ ] **Step 1: Preflight**

Checked out at tag `evaluation-freeze-2026-08-16`. Clean tree. `colima start`, Qdrant up, `specpilot_live` migrated. `SPECPILOT_MAIN_API_KEY` set. Restricted store backed up (see Task 11 — do this before, not after). Verify the running code's prompt hashes match the frozen `prompts_sha256`.

- [ ] **Step 2: Run the three sweeps**

L1 locked 25 (20 answerable + 5 expected-refusal), L2 locked 12, L2-adv locked 10 groups / 20 invocations. Fifty-seven live invocations.

```bash
bash scripts/run_sweep.sh --level l1     --split locked --expected 25 --include-unanswerable
bash scripts/run_sweep.sh --level l2     --split locked --expected 12
bash scripts/run_sweep.sh --level l2-adv --split locked --expected 10
```

- [ ] **Step 3: Seal immediately**

Hash the artifact set, record the manifest, record `git rev-parse HEAD` unchanged, record wall clock and spend. Do not begin scoring until the artifacts are sealed — the boundary between "what the system produced" and "what we did with it" has to be a recorded moment, not a recollection.

- [ ] **Step 4: Record any operator failure honestly**

If a case has to be re-run for an operator or transport reason, it is disclosed with its reason in the report. A silently repeated case is a first run that is not a first run.

### Task 7: Score the Locked Output

- [ ] **Step 1: Prepare judge payloads for the locked answers**

Promote `tmp/prepare_judge_payloads.py` and `tmp/prepare_l2_judge_payloads.py` the same way Task 1 promoted the sweeps, with the same count assertion.

- [ ] **Step 2: Run the calibrated judge route**

Route `judge_calibrated`, as frozen. `SPECPILOT_JUDGE_API_KEY`, separate from the main key.

- [ ] **Step 3: Compute the reported metrics**

Macro-KPRecall, unsupported and contradiction claim raw counts, retrieval Macro-Recall stratified by `question_gold_jaccard` (the stratification frozen in W1 for exactly this report), L2 verdict accuracy, L2-adv matched-pair confusion matrix.

- [ ] **Step 4: Run `egress disclosures` over the locked roots**

The independent account of what actually left. Where it disagrees with an outcome artifact, the disagreement is a finding and goes in the report.

### Task 8: Manual Audit — Author-Owned, Judge-Blind

- [ ] **Step 1: Adjudicate all 25 L1 and 12 L2 locked outputs without looking at judge results**

Two independent label sets, per §8.4: per-key-point hit/miss, and per structured answer claim supported/contradicted/insufficient plus a severe-error flag.

- [ ] **Step 2: Report both sets separately**

Agreement rate, Cohen's kappa, confusion matrix, and label count for each. Do not pool them into one number.

- [ ] **Step 3: Stop**

The audit explains how much to trust this report's scoring. It does not license a change to the judge or the main system. Any change found necessary here is W7 work against a new freeze.

### Task 9: Run the Two Core Comparisons on Locked

Only after Tasks 6–8 are complete and sealed.

- [ ] **Step 1: A′ on the L1 locked set, three paired runs per arm**

Report Macro-KPRecall, unsupported/contradiction raw counts, cost, and P50/P95, each stratified by whether `E-context` actually expanded, with the identical-output items listed.

- [ ] **Step 2: B on the 10 L2-adv locked groups, with the 12 L2 locked cases observing false rejection, three paired runs**

Report end-to-end false confirmations, false rejections, the matched-pair confusion matrix, and raw verdicts. Merge with the direct-fed result into one narrative.

- [ ] **Step 3: Report per-run raw counts and per-case three-run agreement**

`n` stays at 25, 12, and 10 groups. Descriptive engineering evidence only — no "statistically significant", no strong causal wording.

### Task 10: Cold-Cache Cost and Latency — DONE

- [ ] **Step 1: Measure with the response cache cold**

The cache is a development affordance (§9). A latency or cost number measured warm describes the cache, not the system.

- [ ] **Step 2: Report P50/P95 per level and total project spend against the budget cap**

### Task 11: Seal the Release Evidence

**Files:**
- Create: `docs/reports/2026-08-XX-w6-locked-evaluation.md`
- Create: `docs/handoff/2026-08-XX-w6-handoff.md`
- Modify: `README.md`, `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`, `SpecPilot_项目方案.md`

- [ ] **Step 1: Back up the restricted store off this disk — before Task 6, not here**

Listed last because it seals, sequenced first because it protects. The 60 annotated items and the freeze artifacts are not reconstructible; the code is. They currently exist twice on one drive.

- [ ] **Step 2: Write the report**

Every number carries its conditions. Mandatory in the body, not in a footnote or a field:

- the same-family self-generated bias, stated in prose with its actual counts;
- `l2-dev-003`'s undisclosed-requirement reasoning, and what it does to an L2 accuracy claim;
- the L2 chain's seven wire-contract gaps, with instance 7 recorded as partly open and why it was not repaired an eighth time;
- the dimension skew in L2-adv, and why it is evidence the corpus was read rather than evidence of lazy sampling;
- which items `E-context` never expanded;
- every operator re-run from Task 6 Step 4.

- [ ] **Step 3: Update the four documents that state project status, and add the regression test**

`tests/cli/test_documented_progress.py` already exists for this; extend it so the locked counts cannot drift across the four documents.

- [ ] **Step 4: Final gate and tag**

`make w5-check` on the author machine, transcript hashed and recorded. Tag the release evidence commit. CI green.

---

## The ordering that matters most

Tasks 1–5 are build and cost nothing but time. Task 6 is irreversible. Tasks 7–10 are downstream of it.

The temptation will be to skip to Task 6 because it is the interesting one and the drivers "already work on dev". They work on dev in a gitignored directory, hardcoded to dev, with a retry path that has never executed. Fifty-seven live invocations is where that gets discovered.
