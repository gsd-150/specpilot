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

## Producing the dev scoring evidence (auto-judge route)

The author chose the auto-judge route (2026-08-15), so `--dev-scoring-status`
comes from a completed dev calibration, not from pre-registered human labels
alone. The production order, with the author-owned steps named:

1. **Dev runs** (author, real provider): one `VerifiedAnswer` per answerable
   dev case, saved as `<case_id>.json` in an answers directory.
2. **`judge score`** (author, real judge provider): one prepared judge payload
   per case through the `offline_judge` route; a stored record per case.
3. **`judge labels-template`**: one label sheet per case from the annotation
   store, the judge records, and the answers directory.
4. **Author labels** each sheet and **`judge labels-add`** stores it against
   its record.
5. **`judge calibrate`**: joins records and labels, refuses a mixed prompt or
   model population, writes the prose-free evidence bytes, and prints the
   evidence sha256 plus the two label sets' agreement and kappa.
6. Build `dev-scoring-status.json` by hand:
   `{"selected_route": "judge_calibrated", "evidence_sha256": "<sha256 of the
   evidence file>", "split": "dev"}` — extra keys are refused, and none of the
   prohibited keys may appear.

The evidence file itself is also prose-free by construction (hashes, counts,
agreement numbers, and the inlined calibration report only), so its bytes can
be reviewed and re-derived at W6 without exposing case material.

Coverage caveat recorded 2026-08-16: the first sealed evidence covers the
**8 of 12 live answerable L1 dev cases** that produced answers (the retired
`l1-dev-001` was removed after a dump-script defect let it through, and the
four refusals carry no answer to score — see
`docs/reports/2026-08-16-l1-dev-refusal-diagnosis.md`). L2 dev calibration is
added as a second pass once the L2 run harness lands; the report must state
whatever the sealed evidence actually covers and never a larger scope.

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

## Pre-freeze disclosures

The following five items were recorded during the 2026-08-15 L2-adv source
review and are part of the frozen tree. The W6 report must restate each one;
none of them is a defect, and each changes how a raw number may be read.

1. **Dimension skew.** The realized 16-group distribution is
   `normative_strength` x7, `document_attribution` x5, `role_attribution` x2,
   `request_vs_response` x1, `received_vs_generated` x1. The cause is a corpus
   property, not a sampling choice: no genuine topically-close role or
   direction gap could be found for the missing dimensions, and forcing one
   would have produced weaker groups. The author decided against swapping
   groups (2026-08-15): the skew is a finding about the corpus and is stated
   rather than papered over.
2. **Weak direct-feed negatives in the normative groups.** Their distractors
   are themselves SHOULD / MAY / ought-to clauses, so the evidence shown to
   the Verifier nearly refutes the claim and any correct Verifier refuses. A
   direct-feed miss-interception rate near zero therefore does not indicate a
   strong Verifier; it indicates low construction difficulty. The dimension's
   signal lives in the end-to-end negatives, where the corpus holds tempting
   MUST clauses.
3. **Non-minimal rewrites are structural for this axis.** A SHOULD clause
   cannot support `violating`, so the positive half must land on a different
   MUST requirement; "minimally rewritten" on the normative-strength axis
   degrades to "same section, different scenario, flipped strength".
4. **Strict document reading.** The four `document_attribution` negatives
   assert "violates RFC 9112" and are judged as "violates a requirement
   stated in RFC 9112's text". Reading an RFC number as the protocol suite
   name (RFC 9112 §1 defines HTTP/1.1 as the union of three documents) would
   void all four negatives; that reading is outside the judgement scope and
   the report must say so.
5. **Supporting-clause reuse inside the locked set.** The RFC 9110 §7.8
   Switching Protocols clause is shared by three locked groups (locked-003 and
   locked-004 positive support; locked-009 distractor and support) and the
   §7.6.2 Max-Forwards MUST clause by two (locked-002 positive support;
   locked-008 distractor and support). The registration gate only enforces
   dev-vs-locked disjointness, so this reuse is legal; it must still be
   disclosed as evidence-clause reuse.

## Successor boundary

W6 is the first workflow allowed to execute locked L1, L2, or L2-advanced
cases. Locked results may not modify a frozen spec, prompts, routes, thresholds,
tools, configuration, or gold. Any change to an input hash, selected route, or
calibration evidence requires a new candidate and a separately confirmed
successor spec; never rewrite or replace the old content-addressed record.
