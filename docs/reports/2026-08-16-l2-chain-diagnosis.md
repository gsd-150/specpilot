# L2 dev chain diagnosis — 2026-08-16 (second pass, canonical batch)

This supersedes the first-pass L1 refusal diagnosis as the record of the L2
dev path. It documents the layered accounting the author required, the five
wire-contract defects the first live L2 runs exposed, and the labelling
standard the calibration labels were judged under.

## Layered accounting (canonical batch `outcomes-72a27a57`)

The canonical batch is every L2 dev case re-run under the settled HEAD after
all fixes below, versioned in its own directory and verified for prompt
identity (`compliance_prompt_sha256 = eecc5d4c…` on every case; the runner now
refuses a batch whose cases disagree).

| ledger | result |
| --- | --- |
| retrieval: gold fully in the shown evidence set | **8/8** |
| verifier: verdict matches expected, over gold-shown cases only | **8/8** |

The two cases that previously failed retrieval (l2-dev-002, l2-dev-008) came
into range only after the memory prohibition landed: once the model could not
state requirements from memory, the planner and evidence stages had to find
the governing clauses, and did. The report artifact is
`artifacts/restricted/l2-dev/outcomes-72a27a57/layered-accounting.json`.

## Five wire-contract defects found by the first live runs

Each was invisible to the fixture suite because every fixture provider returns
the contract-compliant reply the prompt asks for — the class of failure this
project's testing doctrine warns about, five times over:

1. The compliance instruction never said an insufficient candidate must carry
   no evidence ids, while the closed contract requires it.
2. The planning instruction never explained the tool-call budget arithmetic
   (a `take` step costs its `take`), so the first live plan cost 10 against 8.
3. The L2 instructions never carried the L1 citation-echo rule, and the model
   invented evidence identifiers.
4. The echo wording was ported as equality where L1 has always said subset —
   unsatisfiable beside the schema cap of four and the five to six excerpts a
   case is routinely shown.
5. The instruction never said a determinate candidate must cite at least one
   shown excerpt, so after fix 4 the model began returning determinate
   candidates with empty evidence lists.

Fixes 1–5 are in the instruction stack plus regression tests that pin each
instruction beside the contract rule it must satisfy. A sixth rule — never
state a requirement the shown excerpts do not carry — was added by the author
(commit `a5a865b`), aligned with the L1 reply contract, and is what moved the
retrieval ledger from 6/8 to 8/8.

## The labelling standard (recorded, as required)

A claim is judged by whether the cited clause **establishes the specific
proposition the claim asserts** — not whether the design is globally
compliant. Global compliance is a universal-negative assertion no finite
excerpt set can support; the Verifier's own job is per-claim support, and the
labels follow the same relation.

Consequences recorded here: claims that restate a requirement no shown excerpt
carries (for example l2-dev-003's connection-close assertions, or l2-dev-008's
Via-field assertions) are judged `insufficient` even when the claim's wording
is the model's own overstatement. That overstatement is a claim-wording defect
and is tracked separately from the verdict ledger — it does not drag down a
case whose gold was shown and whose verdict was right.

## Combined judge calibration over the canonical batch

Sixteen cases (8 L1 + 8 L2), prompt v1, model `glm-5.2`:

- key points: n = 40, agreement 39/40 (0.975), Cohen's kappa 0.844;
- answer claims: n = 45, agreement 40/45 (0.889), Cohen's kappa 0.590;
- severe flags: judge-only 3, both 0, human-only 0 — the judge flagged three
  claims severe that the author did not; reported separately per §8.3.2.

Evidence sha256 `69b69c2b74179ceda7f0f1bde98cc9ae0c62295a0fa8d409257addff379bfd14`,
prose-free, with the layered accounting numbers embedded beside the
calibration report. `dev-scoring-status.json` now carries this hash under
route `judge_calibrated`, split `dev`.
