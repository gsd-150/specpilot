# W6 Locked Evaluation and Release Evidence — draft for author review

Status: DRAFT. Every number below was read back from the ledger, the
artifacts, or the audit stores on 2026-08-16. Placeholders the author must
fill are marked [AUTHOR].

There is no signature line. The configuration is attested where it can be
checked — the run spec's author-confirmed `confirmation` block — and the
author's own contribution is recorded as a list of specific acts rather than
as an endorsement. A reader should trust the parts of this report that name
what was done and by whom, and discount the rest accordingly.

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
- Gold disclosure, recomputed per case and recorded in each worksheet's
  `evidence.gold_coverage`: **5 full, 3 partial, 3 none**. The method matters
  because a first pass got it wrong: `clause_id` and `content_hash` are
  different identifier spaces, and comparing them directly returns "none" for
  every case — a uniform result that was itself the tell. The mapping is
  `gold clause_id -> sha256(unit.text) -> disclosed content_hash`.
  - **none**: 004 (verdict wrong), 009, 010 — verdicts right, rationales
    resting on evidence that never left the machine, the l2-dev-003 family
    measured again on the locked set.
  - **partial**: 002 and 008, where the withheld gold restates a norm the
    disclosed clause already carries (002) or covers the client branch of a
    proxy scenario (008) — the governing clause reached the model in both, so
    both read as well-founded; 012, where the withheld non-origin-role gold is
    the rule the rationale calls absent.

### L2-adv locked — execution refused by the quota gate

0 of 10 groups, and not because they were skipped: the gate refused the
budget that would let them run. The groups span documents by construction
and RFC 9112 has 268 lifetime bytes left; any new 9112 disclosure is
impossible. The partial option was rejected because it would spend
irreplaceable quota to shrink n into whatever happens to fit — a subset
selected by budget exhaustion. A gate verdict, not an omission.

## The quota account, exactly

| document | lifetime unique bytes cap | used | remaining | excerpts |
|---|---|---|---|---|
| ietf-rfc-9110 | 76,113 | 71,968 (94.6%) | 4,145 | 237 / 314 |
| ietf-rfc-9112 | 16,069 | 15,801 (98.3%) | 268 | 44 / 70 |

Read back from `egress_corpus_ledger` at the release commit; the 9110 figure
moved after the first draft because scoring and audit work disclosed further
clauses. Any statement of remaining budget is true only as of its timestamp,
which is the point of the lesson below.

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

**What the claims kappa of 0.293 can and cannot mean.** The two label sets do
not decompose the answers the same way. The judge produced 56 claims the human
pass never wrote, close to three times as many claims overall, because it split
at a finer grain. So the 35 matched pairs compare verdicts on the subset where
both sides happened to draw the same boundary, and the disagreement inside that
subset mixes two things this design cannot separate: genuine disagreement about
whether a claim is supported, and disagreement about where one claim ends and
the next begins. A low kappa here is not evidence that the judge and the human
pass disagree about the specification.

Two further conditions bound it. The marginals are extremely unbalanced — 33 of
35 human verdicts are `supported` — which is the arithmetic that collapses
kappa while agreement stays at 0.886, the same paradox the dev calibration
documented and the reason §8.3.2 requires all four numbers together. And the
join runs through `labels-rekeyed`, a copy of the human labels whose claim ids
were remapped onto the judge's numbering; verified here, the remapping changes
identifiers only and leaves all 32 cases' verdict multisets identical to the
worksheets. Every figure in this section was recomputed from
`judge/locked/records` and the audit worksheets and reproduced exactly.

Fixing this properly means pre-registering the claim decomposition so both
sides label the same units. That is W7 work; it cannot be repaired after the
fact without relabelling, and relabelling against a known judge output would
destroy the blindness the audit was built for.

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

Both core comparisons are quota-blocked on the locked side — a gate verdict,
not a schedule decision. What exists is build work and offline previews, and
the report says so in the same breath as each result.

- A' (E-context): built and dev-validated (12/12 dev items expand, 0 emit
  byte-identical payloads). The locked-side live runs are refused by the
  spent budget before they can start: they would disclose new clauses.
- B (gate-only): the locked three-paired-run comparison did not execute —
  the live ON arm would spend quota the gate has refused, and for L2-adv
  there is nothing to pair with, because that set never ran. What exists is
  an offline preview of the OFF arm over the 11 persisted pre-verifier
  artifacts (free, no provider): every pair computes to "same" — the gate
  changed nothing there. The preview's one determinate finding stands on
  its own: the semantic gate verified the 004 false confirmation instead of
  catching it. The comparison itself remains unexecuted.

## The L2-adv dimension distribution

The plan requires this stated rather than left to a field. The ten locked
groups distribute across the five distractor dimensions as:

| dimension | locked groups |
|---|---|
| `normative_strength` | 6 |
| `document_attribution` | 4 |
| `role_attribution` | 0 |
| `request_vs_response` | 0 |
| `received_vs_generated` | 0 |

Ten groups, ten distinct families. **The locked set covers two of five
dimensions.** The three thin dimensions live entirely in the six dev groups,
so ADR 0002's caution — that at `n=10` three dimensions hold two items or
fewer and per-dimension results are not reportable — understates it here:
in the locked set those three are empty and cannot be mentioned at all.

The skew is the corpus showing through the construction rule rather than a
sampling convenience. A group needs a real near-miss: a clause that appears
to support the negative claim and does not. RFC 9110 and 9112 partition
semantics from syntax and restate related obligations at different normative
strengths, which manufactures such near-misses in quantity; HTTP core states
the request/response asymmetry inside the clause carrying the obligation, so
a scenario flipping it is obviously wrong rather than a near miss, and an
obviously wrong negative tests nothing. Flattening the distribution would
have meant inventing near-misses the corpus does not contain.

Since the locked adversarial set never executed, this describes construction
only. There are no results to stratify.

## The re-freeze

Taken before the locked run, at the commit that executed it.

| | |
|---|---|
| run spec | `270f79570551d6be68f122b7ebd035c508baf953b827de259e3e618b28b7a5ac` |
| git commit | `7650ebe` |
| confirmed by | `chunxue` |
| scoring route | `judge_calibrated` |

Two inputs were decided rather than inherited. `models_sha256` now binds all
four models — `deepseek-v4-flash` (main chain), `glm-5.2` (judge), `bge-m3`
(encoder), `claude-opus-5` (drafter of 49 of 61 gold sets) — where the
previous spec bound the drafter alone and so did not describe the run.
`--python-version 3.12.11` was recovered by search over 336 candidates after
an earlier attempt concluded the field was unreproducible; the new spec's
`environment_sha256` is byte-identical to the old one, which confirms the
recovery independently and makes the field rehashable by any reader.

## What the author did personally

This section replaces a signature line. A signature on a solo project attests
nothing a reader can check, and the configuration already carries a real
attestation — the run spec's `confirmation` block, author-confirmed and
machine-verifiable. What no other record carries is which judgements in this
report are the author's rather than the model's, and that is the one thing a
reader of a largely model-authored evaluation needs.

- Reviewed the sixteen drafted L2-adv groups clause by clause and **rejected
  five** — dev-004 on a User-Agent MUST, dev-005 and dev-006 in §6.2,
  locked-005 in §9.3, locked-006 on soft wording — each rejection requiring
  the adjacent clauses to have been read. A 31% rejection rate on
  model-drafted adversarial items is the strongest available evidence that
  the human review layer is real.
- Corrected the model's account of which excerpts case 002 was shown, from
  the artifacts rather than from its summary.
- Designed the audit ordering that makes `agreed_with_draft` meaningful:
  judge from the answer and criteria first, open the drafts only afterwards.
  Without that ordering the adoption rate measures nothing.
- Decided the re-freeze, the four-model tuple, and the refusal to raise or
  rebind the caps when the quota blocked the remaining work.

### What the annotation stores record, including the part that is not flattering

The four items above are drawn from this session. The annotation stores hold a
longer and more mixed record, and it belongs here because a section about human
review that reports only its successes is the endorsement this section exists
to replace.

| record | count |
|---|---|
| review decisions, all `reviewer_id: chunxue` | 41 |
| of which adopted the model's proposal (`chose_proposal`) | **40** |
| of which rejected it | 1 |
| reviews that edited a key point (`key_points_edited`) | **0** |
| items marked unanswerable during review | 8 |
| deep reviews, all `gold_complete` | 13 |
| clauses examined across them | 135, over 1,187 seconds |
| items retired | 1 |

**The adoption rate in annotation review is 40 of 41, and no key point was ever
edited.** Against that, the L2-adv construction review rejected 5 of 16 groups.
Both numbers are real and they describe different passes: accepting a proposed
gold clause for an item, versus attacking a constructed adversarial pair to see
whether its negative is genuinely unsupported.

The pattern is the finding. Human review in this project is load-bearing where
the reviewer is looking for a specific failure — a negative claim that a
neighbouring MUST quietly supports — and close to pass-through where the task
is to confirm a proposal that looks right. A reader should weight the two
accordingly, and should not read "human-reviewed" as a uniform guarantee across
the 61 items.

This also bounds what the §8.4 numbers above can mean. The labels there were
produced by the same model that drafted most of the gold; the annotation review
that was supposed to be the independent check adopted 40 of 41 proposals. The
one layer in this project that has demonstrably overturned model output at rate
is adversarial construction review, and that layer never touched the locked
evaluation labels.

## [AUTHOR] still to fill before release

## Cost and latency, cold

Measured rather than arranged: the local response cache is opt-in by argument
and `scripts/run_sweep.sh` never passes one, so every figure below is a
cold-cache figure by construction. There is no warm comparison to report,
which is the honest form — a latency number taken warm describes the cache.

| level | provider calls | request tokens | p50 | p95 |
|---|---|---|---|---|
| L1 locked | 24 | 18,674 | 5.54 s | 13.7 s |
| L2 locked | 66 | 82,012 | 9.85 s | 85.2 s |

L2 by stage, which is where the cost actually sits:

| stage | calls | request tokens | p50 |
|---|---|---|---|
| planning | 13 | 46,666 | 48.9 s |
| compliance | 11 | 10,306 | 16.0 s |
| judge | 11 | 8,274 | 32.5 s |
| verifier | 10 | 6,086 | 3.4 s |

**Planning is 57% of L2's tokens and by far its slowest stage** — the planner
reasons over the tool catalogue before its first call, which is why the main
route carries a 300-second timeout rather than the 60 seconds the L1 payloads
never approached. The verifier, the stage that exists to catch the errors, is
the cheapest thing in the chain at 3.4 s and 7% of tokens. On this evidence the
semantic gate is not where the budget goes, and case 004 shows it is also not
where the errors stop.

Wall clock ran roughly twice provider duration across the dev rehearsal, the
difference being local BGE-M3 encoding, BM25 and the Qdrant round trip — work
no provider budget covers and no cache would shorten.

## The release gate

Run at `cbaad01` on the machine that holds the corpus. `HEAD` was recorded
before and after and did not move; the tree was clean throughout.

The commit that records this gate is not the commit the gate ran on, and
cannot be: writing the result down changes the tree. The gap is one commit
touching this file only — `cbaad01` is the tree that was tested, and every
later commit named in this section edits documentation and nothing the gate
covers. Closing the gap properly means stopping commits after the gate, which
is what tagging a release is for, not something a re-run achieves.

| target | result |
|---|---|
| ruff, mypy | clean |
| unit | 1,872 passed |
| CLI | 243 passed |
| frontend | 153 passed across 5 files |
| **full-service** | **2,454 passed, 0 skipped**, 58.47 s |
| browser | 5 passed, 8.6 s |
| packaged demo | `packaged_demo_gate=passed` |

Transcript SHA-256
`301c2419efe267c2a65efe0deb5acc8b30501828dac3b80dc7a75b39bf7d8c6d`, 3,365
lines. Fresh scratch databases were created before the run and dropped after,
because a reused migrated database hides a missing migration — that is not
hypothetical here.

CI at the same commit: run `31968097661`, all seven jobs green. What CI attests
and what this machine attests remain different claims; `make w5-check` is
structurally unsatisfiable in CI, for the reason the workflow header records.

**It took three attempts, and the two refusals were both guards doing their
job.** The first run reported 2,446 passed with 8 skipped and the zero-skip
rule refused it: `tests/cli/test_l2_adv_registration.py` had skipped entirely
because `manifests/` is gitignored and a worktree does not carry it, so the
override that exists for exactly this case had to be set. Without that rule the
run would have gone green while the eight tests covering adversarial
registration — including the one that refuses a fabricated clause digest —
never executed. The second run was refused by the browser fixture's DSN
allowlist, which accepts two literal database names because it drops and
re-migrates whatever it is given; the correct response was to use a sanctioned
name rather than widen the allowlist.

Against the W5 freeze point's 2,388 full-service tests, the release commit
carries 2,454. The 66 additional tests are the W6 build: sweep selection,
adversarial pair execution, the two comparison arms, and judge preparation.
- [AUTHOR] The numbers for any résumé or demo use, with `n` and scope. Every
  headline in this report is bounded — L1 `n=25`, L2 `n=12` of which one was
  quota-refused, L2-adv `n=0`.
