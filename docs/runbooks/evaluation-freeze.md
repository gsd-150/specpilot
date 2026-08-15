# Evaluation run-spec freeze

W5 prepares and validates an evaluation run-spec candidate. It does not run an
evaluation, inspect a locked case, call a provider, choose a scoring route, or
confirm the freeze. The final confirmation is an author-owned action for
`chunxue` after reviewing the aggregate validation evidence.

## Inputs

Use only aggregate or status JSON artifacts. Do not give these commands an
annotation store, a locked-set directory, a provider response, or an evaluation
result. The closed readers require:

- L1 progress of 40/40 (dev 15/15, locked 25/25) and L2 progress of 20/20
  (dev 8/8, locked 12/12);
- deep-review progress of 12/12;
- pooling totals with no blocked item, every registered item adjudicated, and
  every run sealed;
- L2-advanced dev/locked aggregate inventories with disjoint item IDs and
  disjoint registered families, plus the overlap-report hash;
- the exact source, corpus, collection, set, script, prompt, configuration,
  policy, provider, model, scoring, and environment hashes;
- a selected scoring route and its dev-only evidence hash;
- a clean Git worktree and the dependency lock used by the run.

All status schemas are extra-forbidding. The keys `question`, `claim`,
`excerpt`, `answer`, and `rationale` are recursively forbidden. A refusal is a
stable code on stderr and creates no candidate.

## Generate a candidate

Run from the clean repository whose commit will be frozen. Replace the paths
below with the reviewed aggregate/status artifacts; do not point them at locked
case or output directories.

```bash
python -m specpilot.cli evaluation freeze-candidate \
  --repository "$PWD" \
  --dependency-lock /absolute/path/to/requirements.lock \
  --progress-status /absolute/path/to/progress-status.json \
  --deep-review-status /absolute/path/to/deep-review-status.json \
  --pooling-status /absolute/path/to/pooling-status.json \
  --l2-adv-status /absolute/path/to/l2-adv-status.json \
  --identity-status /absolute/path/to/evaluation-identities.json \
  --dev-scoring-status /absolute/path/to/dev-scoring-status.json \
  --candidate-dir /absolute/private/path/evaluation-candidates
```

Success prints only `path`, `hash`, and aggregate `counts`. Review the candidate
and validation sources without running any locked set. Record the printed
candidate hash exactly.

## Author confirmation

Only `chunxue` runs the following command, with the exact candidate path and
hash printed above. The literal `--confirm-freeze` flag is mandatory.

```bash
python -m specpilot.cli evaluation freeze-confirm \
  --candidate /absolute/private/path/evaluation-candidates/CANDIDATE_SHA256.json \
  --expected-hash CANDIDATE_SHA256 \
  --author-id chunxue \
  --confirm-freeze \
  --repository "$PWD" \
  --output-dir /absolute/private/path/evaluation-run-specs
```

Confirmation verifies the candidate's exact bytes and the unchanged clean Git
commit/tree, then publishes a content-addressed final spec. It does not execute
evaluation code. An identical retry leaves the artifact bytes unchanged and
prints the same `path`, `hash`, and aggregate `counts`.

## Successor boundary

W6 is the first workflow allowed to execute locked L1, L2, or L2-advanced
cases. Locked results may not modify a frozen spec, prompts, routes, thresholds,
tools, configuration, or gold. Any change to an input hash, selected route, or
calibration evidence requires a new candidate and a separately confirmed
successor spec; never rewrite or replace the old content-addressed record.
