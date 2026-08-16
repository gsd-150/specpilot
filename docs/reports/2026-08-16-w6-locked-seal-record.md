# W6 locked-run seal record — 2026-08-16

Verified facts of the locked default chain's execution, read back from the
artifacts and the ledger. No scoring here: this is the seal, not the report.
Every number below was recomputed from the ledger, not quoted from memory.

## L1 locked — 25/25 executed, across two passes

- 21 answer files, 4 refusals recorded.
- All 20 answerable items were answered; **l1-locked-023 (expected refusal)
  was answered** — one false trigger.
- Refusals: 004, 005, 025 refused with the designed reason
  (evidence_insufficient). **024 refused as source_manifest_document_mismatch**
  — the only code path that produces that refusal is the top fused hit
  belonging to a different document than the authorized manifest. It refused
  (fail-closed held) but not with the reason its gold predicts, so the refusal
  metrics for the locked set are 3 designed refusals and 1 by-different-gate.
- **The sweep did not run 001-025 in one pass.** The ledger timeline shows
  the sweep's consecutive roots through 023, then nothing for 024 — its
  refusal fired before any reservation, so it left no ledger row and, under
  the driver then in place, aborted the batch. **025 ran 16 minutes later as
  a separate manual invocation** (operator event; disclosed here, not hidden).
  The driver change that makes a verdict-refusal a logged result instead of a
  batch abort is the same one that adds the --resume guard.

## L2 locked — 10/12 executed, one blocked by the quota gate

- 10 outcomes, all with one prompt identity (eecc5d4c0b98) across the batch.
- All 10 pre-verifier artifacts present and hash-verified; the gate-only pair
  scorer over them reports no exclusions and no downgrades.
- Verdict distribution: 001-003 violating, 004-007 compliant, 008 compliant +
  insufficient pair, 009-010 insufficient. Unscored here — that is the judge's
  and the author's downstream step.
- **011 blocked at the compliance reservation by
  corpus_document_unique_bytes_exceeded.** The enforcer refuses before any
  reservation: the ledger shows only the planning reservation (succeeded) for
  the case, zero spend on the block, and the two clauses its evidence step had
  selected (both RFC 9112, both never disclosed before) are exactly what the
  remaining quota cannot admit.
- **012 was not run** because the sweep aborted at 011. 012 is bound to RFC
  9110, which still has quota, so it is not itself blocked — it is a
  not-executed case whose execution is an author decision, not a gate verdict.

## The quota accounting, exactly

Current head epoch of the corpus ledger (policy 1dc8c5f2, the frozen policy):

| document | lifetime unique bytes cap | used | remaining |
|---|---|---|---|
| ietf-rfc-9110 | 76,113 | 69,137 | 6,976 |
| ietf-rfc-9112 | 16,069 | 15,801 | **268** |

The caps are the one-fifth figures the section 3.2 authorization rests on.
The usage is cumulative over every disclosure ever made against this corpus —
dev sweeps, the seven-round L2 diagnosis, the rehearsal, tonight's locked L1
and L2. The quota is lifetime-unique: re-sending a clause already disclosed is
free, and every clause disclosed for the first time is a permanent charge.

## L2-adv locked — not executed

The adversarial groups span documents by construction. RFC 9112 has 268 bytes
of lifetime quota left, which admits zero new 9112 clauses; the dev rehearsal
measured exactly how much 9112 evidence these groups draw on. Running the set
now would either refuse on the first 9112 disclosure or, in the partial form
(option B), spend the remaining 9110 quota to shrink n to whatever happens to
fit — measuring a subset selected by budget exhaustion, not by design.

## What this seal records, and what it does not

It records that the locked default chain did not complete: L1 25/25, L2 10/12,
L2-adv 0/10. The blocking condition is the system's own data-minimization gate
working as designed against a quota that development activity had already
consumed. The gate's integrity is the product's claim; the report will state
this plainly rather than soften it. Raising the caps (option C) would
invalidate the section 3.2 one-fifth premise that the authorization decision
rests on — that decision would have to be remade, not inherited — and would
trip the cap-bound tests by design. Not taken.

The lesson for the next freeze, stated once: the development loop and the
measurement run share one lifetime-unique ledger, so rehearsing against the
frozen corpus spends the budget the locked run needs. A development-phase
budget model is W7 work; it is not fixed by touching these caps.
