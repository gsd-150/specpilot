# Judge Scoring and Dev Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the §8.3 auto-judge scoring path — the closed output contract, the
versioned judge prompt, per-key-point and per-answer-claim calibration math, the
§8.4 answer-metric aggregation, the restricted judge/label stores, and the CLI —
so the author can calibrate the judge over the dev outputs and seal the
calibration as the dev scoring evidence the evaluation freeze requires.

**Context:** The author chose the auto-judge route on 2026-08-15 (recorded in
§8.3). The egress layer already carries the judge: `JudgePayload`, the judge
cap vectors, and the enforcer's judge branch exist. What does not exist is
everything between the payload contract and a usable calibration report. The
freeze gate blocks on `--dev-scoring-status`, whose evidence is this
calibration.

**Tech Stack:** Python 3.12+, Pydantic v2, the existing `PolicyBoundTransport`
judge route (`offline_judge`), and the existing `SecureRecordDirectory` store
machinery.

## Global Constraints

- The enforcer is the only path outward; every judge call is an
  `EgressRequest` with the `judge` payload kind.
- No clause prose in any git-tracked record. Judge and human-label records are
  content-addressed files under `artifacts/restricted/` (gitignored), and the
  freeze-facing evidence file is sanitized: it carries hashes, counts, and
  agreement numbers only — the freeze reader recursively rejects the keys
  `question`, `claim`, `excerpt`, `answer`, and `rationale`.
- The judge and the Verifier stay independent: separate prompt module, separate
  call path, different provider route/model (§8.3's `glm-5.2` note).
- Real judge calls belong to `chunxue`; this implementation ships fixture-only
  tests and refuses a missing route the same way the rest of the transport does.
- §8.3.2: every prompt change keeps the old prompt and the old numbers.

---

### Task 1: Closed scoring contracts

**Files:**
- Create: `src/specpilot/contracts/scoring.py`
- Modify: `src/specpilot/contracts/egress.py`
- Create: `tests/unit/contracts/test_scoring_contracts.py`

**Interfaces:**
- Produces `KeyPointHit`, `AnswerClaimJudgement`, `JudgeOutput`
  (`judge-output/v1`), `JudgeRecord` (`judge-record/v1`),
  `HumanKeyPointLabels`, `HumanAnswerClaimLabels`, `HumanDevLabels`
  (`human-dev-labels/v1`).
- `JudgePayload` gains the required `query` field: §8.3.1 lists the question as
  judge input, and both L1 payloads already carry `query`.

- [ ] **Step 1: Write RED tests**

  Cover: hit/miss with an optional bounded miss reason; claim triage restricted
  to `supported/contradicted/insufficient` plus the severe flag; a judge output
  whose point ids do not match its payload's scoring points is refused; a record
  whose hashes disagree with its fields is refused; human labels referencing an
  unknown point or claim id are refused; `JudgePayload` without `query` fails.

- [ ] **Step 2: Run RED**

  Run: `.venv/bin/python -m pytest tests/unit/contracts/test_scoring_contracts.py -q`

  Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement**

  Frozen extra-forbidding models. `JudgeRecord` stores `case_id`,
  `question_hash`, `final_answer_hash`, `prompt_hash`, `model_id`, the output,
  and a timestamp — no prose, so the freeze evidence can be derived from it
  without redaction.

- [ ] **Step 4: Verify and commit**

  Run the focused tests, Ruff, and mypy. Commit
  `feat: add the closed judge scoring contracts`.

---

### Task 2: Versioned judge prompt

**Files:**
- Create: `src/specpilot/judge/__init__.py`
- Create: `src/specpilot/judge/prompt.py`
- Create: `tests/unit/judge/test_prompt.py`

**Interfaces:**
- Produces `JudgePrompt` (identifier, version, body, `content_sha256`),
  `render_judge_prompt(prompt, payload) -> str`, and
  `prompt_identity(prompt) -> JudgePromptIdentity`.

- [ ] **Step 1: Write RED tests**

  Assert the rendered prompt contains every scoring point, every excerpt id, and
  the answer; the identity hash is the canonical hash of identifier, version,
  and body together; two prompts with different bodies never share an identity;
  the rendered text asks for one JSON object and nothing else.

- [ ] **Step 2: Run RED**

  Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement**

  Follow the `REPLY_INSTRUCTIONS` lesson: the reply schema the prompt prints is
  the exact schema the parser accepts, and the identifiers it names (`point id`,
  `excerpt id`) are the identifiers the payload prints. No other prose.

- [ ] **Step 4: Verify and commit**

  Commit `feat: version and hash the judge prompt`.

---

### Task 3: Calibration mathematics

**Files:**
- Create: `src/specpilot/judge/calibration.py`
- Create: `tests/unit/judge/test_calibration.py`

**Interfaces:**
- Produces `KeyPointAgreement`, `ClaimAgreement`, `CalibrationReport`
  (`judge-calibration/v1`).
- Pure functions over matched `JudgeRecord`/`HumanDevLabels` pairs; the two
  label sets are computed and reported separately, never mixed (§8.3.2).

- [ ] **Step 1: Write RED tests**

  Cover: perfect agreement, complete disagreement, empty inputs, a judge point
  hit with no human label (missing label → excluded with a count, not guessed),
  Cohen's kappa for binary and three-class labelings, the severe-flag agreement,
  and refusal of mixed-set inputs.

- [ ] **Step 2: Run RED**

  Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement**

  Cohen's kappa over the two raters' observed categories with the standard
  chance-agreement term; confusion matrices keep the natural label order;
  every report field carries its `n`.

- [ ] **Step 4: Verify and commit**

  Commit `feat: compute judge-human calibration agreement`.

---

### Task 4: §8.4 answer-metric aggregation

**Files:**
- Create: `src/specpilot/judge/aggregate.py`
- Create: `tests/unit/judge/test_aggregate.py`

**Interfaces:**
- Produces `AnswerMetrics` (`answer-metrics/v1`): per-question `KPRecall`,
  `Macro-KPRecall`, `unsupported_answer_claim_rate`,
  `gold_contradiction_rate`, and the count of questions with at least one
  severe error. Refusal metrics stay with the answer records, not the judge.

- [ ] **Step 1: Write RED tests**

  Cover the §8.4 formulas against hand-computed fixtures, the unsupported-rate
  denominator (all extracted claims), the gold-contradiction rate, and the
  per-question/aggregate shape.

- [ ] **Step 2: Run RED**

  Expected: FAIL.

- [ ] **Step 3: Implement**

  Deterministic aggregation only; every rate carries numerator, denominator,
  and question count.

- [ ] **Step 4: Verify and commit**

  Commit `feat: aggregate judge outputs into §8.4 answer metrics`.

---

### Task 5: Restricted judge and human-label stores

**Files:**
- Create: `src/specpilot/judge/store.py`
- Create: `src/specpilot/judge/evidence.py`
- Create: `tests/unit/judge/test_store.py`

**Interfaces:**
- Produces `JudgeRecordStore` and `HumanLabelStore` over
  `SecureRecordDirectory` under `artifacts/restricted/judge/` (0700, atomic,
  content-addressed, write-once).
- Produces `build_scoring_evidence(report, records) -> bytes` — the sanitized
  freeze-facing evidence: route id, prompt identity, model id, per-set
  agreement/kappa numbers, label counts, and record hashes. Recursively free of
  the keys the freeze reader forbids.

- [ ] **Step 1: Write RED tests**

  Cover store round-trips, write-once refusal, hash verification, and evidence
  bytes that both validate against the freeze reader's prose-key rejection and
  change whenever any record changes.

- [ ] **Step 2: Run RED**

  Expected: FAIL.

- [ ] **Step 3: Implement**

  Reuse `SecureRecordDirectory`; the evidence builder refuses (fails closed)
  if a report references a record whose hash is absent.

- [ ] **Step 4: Verify and commit**

  Commit `feat: store judge records and human labels content-addressed`.

---

### Task 6: Judge CLI

**Files:**
- Create: `src/specpilot/judge/cli.py` (handlers wired into `cli.py`)
- Create: `tests/cli/test_judge.py`

**Interfaces:**
- `judge labels-template --case-dir ... --out ...` emits the author's human
  label sheet for dev cases (case ids, gold key points, extracted claims —
  restricted output directory only).
- `judge calibrate --records-dir ... --labels-dir ... --evidence-out ...`
  joins records and labels, prints the report, writes the evidence file.
- `judge score --case ...` prepares and sends one `JudgePayload` through
  `PolicyBoundTransport` with the `offline_judge` route. Real calls are
  author-run; the CLI refuses a missing adapter exactly like the transport.

- [ ] **Step 1: Write RED tests**

  Fixture records/labels only, fake provider adapter; assert refusal codes for
  a missing adapter, malformed model output, unknown label ids, and mixed
  splits.

- [ ] **Step 2: Run RED**

  Expected: FAIL.

- [ ] **Step 3: Implement**

  Fail closed on every parse; print counts and hashes only.

- [ ] **Step 4: Verify and commit**

  Commit `feat: add the judge calibration CLI`.

---

### Task 7: Close the gate

**Files:**
- Modify: `docs/runbooks/evaluation-freeze.md`
- Modify: `SpecPilot_项目方案.md` (status annotation)
- Modify: `docs/handoff/2026-08-15-codex-handoff.md`

- [ ] **Step 1: Run `make check` and the focused suites**

- [ ] **Step 2: Record the shipped state and the author-owned remainder**

  The dev runs, the human labels, real judge calls, and calibration acceptance
  belong to `chunxue`. The freeze evidence is produced by `judge calibrate`
  once the author accepts the dev kappa.

- [ ] **Step 3: Commit**

  `docs: record the judge scoring delivery state`.
