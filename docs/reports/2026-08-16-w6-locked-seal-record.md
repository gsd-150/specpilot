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

## L2 locked — 12 cases accounted: 11 outcomes with results, 1 refused by the quota gate

- 11 outcomes with results, all with one prompt identity (eecc5d4c0b98)
  across the batch; 012 carries the same identity after the resume pass.
- All 11 pre-verifier artifacts present and hash-verified; the gate-only pair
  scorer over them reports no exclusions and no downgrades.
- Verdict distribution: 001-003 violating, 004-007 compliant, 008 compliant +
  insufficient pair, 009-010 insufficient, 012 insufficient. Unscored here —
  that is the judge's and the author's downstream step.
- **011 blocked at the compliance reservation by
  corpus_document_unique_bytes_exceeded.** The enforcer refuses before any
  reservation: the ledger shows only the planning reservation (succeeded) for
  the case, zero spend on the block, and the two clauses its evidence step had
  selected (both RFC 9112, both never disclosed before) are exactly what the
  remaining quota cannot admit.
- **012 completed on the resume pass** (driver with the first-run guard and
  --resume, head bf50828). Two transport retries first — provider_unreachable
  once at compliance and once at planning, both classified as retryable by the
  TLS fix and retried; the third attempt ran the full chain. Disclosed as an
  operator event, not absorbed. Final verdict insufficient_evidence, unscored
  here. The batch prompt identity held across all 12 artifacts including the
  resumed one.

## The quota accounting, exactly

Current head epoch of the corpus ledger (policy 1dc8c5f2, the frozen policy):

| document | lifetime unique bytes cap | used | remaining |
|---|---|---|---|
| ietf-rfc-9110 | 76,113 | 70,216 | 5,897 |
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

## Judge scoring — 32/32 completed, one mid-run commit disclosed

All 32 prepared payloads were scored through the calibrated judge route
(glm-5.2 via chatanywhere, judge prompt v1, one prompt identity across the
batch: 913d7098). The first attempt failed on a transient transport fault
that a system-proxy diagnosis explained (httpx reads the macOS system proxy,
curl does not); the NO_PROXY rerun scored all 32. One commit landed mid-run
— the proxy-hint documentation, docs-only, no code — and the runner's HEAD
guard reported it. The records themselves carry the prompt identity, so the
batch is coherent; the event is disclosed here rather than absorbed.

The section 8.4 audit was filled by claude-opus-5 (the same model that
proposed most of the gold — an independence defect the report must state),
judged before any judge result existed, against an agent draft with adoption
recorded per point and claim. Judge-vs-label comparison over the 32 joined
cases:

| | n | agreed | rate | kappa | exclusions |
|---|---|---|---|---|---|
| key points | 76 | 74 | 0.974 | 0.894 | 0 |
| claims (matched) | 35 | 31 | 0.886 | 0.293 | judge-only 56 |

Claim severe flags: both 1, human-only 1, neither 33. The low claim kappa
beside a high rate is the unbalanced-marginal paradox the dev calibration
already documented; all four numbers are reported per section 8.3.2. The 56
judge-only claims are the judge's over-extraction against the labeler's 35;
the alignment that joined them is recorded in the worksheets and
labels-rekeyed files.

The one claim both sides marked severe is the l2-locked-004 correlation-id
claim (the verified false confirmation); the one human-only severe is the
l1-locked-023 false trigger.

## What this seal records, and what it does not

It records that the locked default chain did not complete: L1 25/25, L2 12
accounted (11 outcomes with results, 1 empty outcome refused by the lifetime
quota — its artifact exists with results: 0), L2-adv 0/10. The blocking condition is the system's own data-minimization gate
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
