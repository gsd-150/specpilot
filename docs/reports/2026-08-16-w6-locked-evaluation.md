# W6 Locked Evaluation and Release Evidence — draft for author review

Status: DRAFT. Every number below was read back from the ledger, the
artifacts, or the audit stores on 2026-08-16. Placeholders the author must
fill are marked [AUTHOR]. This document is not the release until the author
signs it.

## The first sentence the report owes its reader

The locked default chain did not complete. The L2 set lost one case to the
system's own lifetime data-minimization quota: the corpus ledger's unique
content budget for RFC 9112 had been consumed by development and rehearsal
activity, and l2-locked-011 was refused at the compliance reservation with
corpus_document_unique_bytes_exceeded — 15,801 of 16,069 bytes already used,
268 remaining, zero spend on the block. The gate worked exactly as designed.
The plan that the gate executed did not account for the development period
spending the measurement budget. That is the finding, stated plainly, and
it is a release blocker under section 8.6, not an embarrassment to soften.

## What ran, and what it produced

### L1 locked — 25/25 executed across two passes

- 21 answers, 4 refusals. All 20 answerable items were answered.
- One false trigger: l1-locked-023 (an expected refusal) was answered.
- Refusals: 004, 005, 025 with the designed reason (evidence_insufficient);
  024 refused as source_manifest_document_mismatch — the top fused hit
  belonged to the other document, fail-closed held, and the refusal metric
  for the set is 3 designed and 1 by-different-gate.
- Operator events, disclosed: the sweep aborted at 024 (the then-driver
  treated a verdict-refusal as a fault) and 025 ran 16 minutes later as a
  separate manual invocation. The driver now logs verdict-refusals as
  results (bf50828).

### L2 locked — 12 accounted: 11 outcomes with results, 1 refused

- Verdict distribution: 001-003 violating, 004-007 compliant, 008 a
  compliant/insufficient pair, 009, 010, 012 insufficient. Prompt identity
  held across all artifacts.
- l2-locked-011: refused by the lifetime quota; its outcome artifact exists
  with results: 0.
- l2-locked-012 completed on the resume pass after two transport retries
  (provider_unreachable, now a retryable class). Disclosed.
- **l2-locked-004 is a verified false confirmation**: the chain returned a
  verified compliant verdict on the correlation-id claim against gold that
  requires the client to associate responses by order sent. The governing
  clause was not among the disclosed evidence. This is the locked run's
  most serious finding.
- Gold disclosure: 7 of 11 gold sets fully disclosed. The four undisclosed
  sets are 004 (verdict wrong), 009, 010, 012 (verdicts right, but the
  rationales rest on evidence that never left the machine — the
  l2-dev-003 family, measured again on the locked set).

### L2-adv locked — not executed

0 of 10 groups. The groups span documents by construction and RFC 9112 has
268 lifetime bytes left; any new 9112 disclosure is impossible. The partial
option was rejected because it would spend irreplaceable quota to shrink n
into whatever happens to fit — a subset selected by budget exhaustion.

## The quota account, exactly

| document | lifetime unique bytes cap | used | remaining |
|---|---|---|---|
| ietf-rfc-9110 | 76,113 | 70,216 | 5,897 |
| ietf-rfc-9112 | 16,069 | 15,801 | 268 |

The caps are the one-fifth figures the section 3.2 authorization rests on;
raising them would invalidate that recorded decision (it would have to be
remade, not inherited) and trip the cap-bound tests by design. Not taken.
The lesson for the next freeze: the development loop and the measurement
run share one lifetime-unique ledger, so rehearsing against the frozen
corpus spends the budget the locked run needs. A development-phase budget
model is W7 work.

## The section 8.4 audit

Labels by claude-opus-5 — the same model that proposed 49 of 61 gold items
— judged before any judge result existed, against an agent draft with
adoption recorded per point and per claim. The independence defect is
stated here in prose, not in a footnote: these labels are not independent
of the gold they audit, and every agreement number below inherits that.

- Draft-final adoption: 71/76 key points (0.934) and 33/35 claim verdicts
  (0.943); 7 recorded overturns, none rubber-stamped.
- Judge vs labels (32 cases joined, both sides reported separately):
  key points n=76, agreement 0.974, Cohen's kappa 0.894; claims n=35
  (matched), agreement 0.886, kappa 0.293 with 56 judge-only excluded
  claims — the unbalanced-marginal paradox the dev calibration documented,
  so all four numbers are reported per section 8.3.2.
- Severe flags on claims: both 1 (l2-locked-004's correlation-id claim),
  human-only 1 (l1-locked-023's false trigger), neither 33.

## What an L2 accuracy statement must carry

- l2-dev-003's undisclosed-requirement reasoning (freeze disclosure §7): a
  verdict can be right while its rationale names a requirement no excerpt
  showed. The locked set reproduced the family on 010 and 012.
- The L2 chain's seven wire-contract gaps, instance 7 only partly closed:
  the prohibition on reasoning from specification memory changed wording
  without changing behaviour, and it was deliberately not repaired a
  fourth time because three prompt edits each produced a differently-shaped
  recurrence and the prompt bytes are now frozen.
- The same-family self-generated bias: 17 of 20 L2 items and 40 of 40 L1
  items had scenario, gold, or candidate labels proposed by a model of the
  same family as the system under test, each reviewed item-by-item by a
  human against the frozen source. These are not unbiased performance
  estimates.

## The comparison arms

- A' (E-context): built and dev-validated (12/12 dev items expand, 0 emit
  byte-identical payloads). The locked-side live runs are impossible: they
  would disclose new clauses against a spent budget. Reported as built
  only.
- B (gate-only): the off arm is computed from the 11 persisted
  pre-verifier artifacts, no provider, no quota; on the locked L2 set every
  pair is "same" — the gate changed nothing there, which is itself the
  finding: the one determinate error the gate should have caught (004) it
  verified instead.

## [AUTHOR] still to fill before release

- [AUTHOR] The L2-adv dimension skew statement (the plan requires it and
  why it reads as evidence the corpus was read).
- [AUTHOR] The re-freeze decision and, if taken, the confirmed run spec id.
- [AUTHOR] make w5-check transcript at the release commit; CI green.
- [AUTHOR] The numbers for any résumé/demo use, with n and scope.
