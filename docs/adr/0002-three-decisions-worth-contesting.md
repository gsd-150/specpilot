# ADR 0002: Three decisions that look arbitrary from outside

**Status:** accepted, W5
**Date:** 2026-08-16

## Context

Three decisions in this project read as over-engineering, pedantry, or laziness
until you know what the alternative costs. Each has a plausible-sounding
alternative that a careful reader will propose within a minute of meeting it.
None of the three is written down anywhere as a *rejected* alternative, only as a
finished implementation — which means the reasoning exists only in the head of
whoever made the call, and rationale that lives in one head is rationale the
project does not actually own.

Each section below states the decision, the alternative, and why the alternative
loses. Where the alternative has a real cost that this project simply accepts,
that cost is stated rather than argued away.

---

## 1. `disclosure_id` hashes a tuple, not the excerpt text

**Decision.** `disclosure_id` is
`sha256(json([corpus_manifest_id, content_hash, quote_hash, normalized_excerpt_span]))`
— `src/specpilot/egress/policy.py:165`. The excerpt's own bytes reach the hash
only indirectly, through `content_hash`.

**The alternative.** Hash the excerpt text. One field instead of four, obviously
correct, and identical text gets an identical id.

**Why it loses.**

*The identity of a disclosure is not the identity of a sentence.* The caps
answer "how much of **this corpus version** has left the machine." Two corpus
snapshots can carry byte-identical clause text and still be different
disclosures, because the compliance premise that authorized sending was
evaluated against one manifest and not the other. Text-only hashing would let a
corpus rebuild silently reuse a budget that was never approved for it.

*The same text at a different span is a different disclosure.* §8.5.2 makes
window selection an explicit variable — when a clause exceeds the per-excerpt
limit, which 512 tokens went out is part of what was disclosed. A text hash
cannot express that, so the excerpt span is in the tuple.

*The tuple is what makes the audit possible without storing text.* This is the
load-bearing reason. `build_disclosure_index` recomputes ids **from the corpus
side** and matches them against ids the ledger recorded
(`src/specpilot/egress/disclosure_audit.py:104`). The ledger therefore never
holds a single character of source text, and the question "was this clause
actually disclosed?" still has a mechanical answer. Under text hashing the
ledger would either store text — violating §8.1, which is a licence condition
under the IETF TLP as well as a hygiene rule — or store a hash nothing could
reconstruct without already having the excerpt, which defeats the audit.

**The cost this accepts.** Four fields must stay canonical forever. Reordering
them or changing the span model invalidates every recorded id, and there is no
rebinding path today. `_CONTENT_ID_FIELDS` handles the analogous problem for
self-referential hashes; this one is simply frozen.

**How to attack it, honestly.** The tuple's ordering is convention, not derived
from anything, and a JSON encoding decision (`separators`, `ensure_ascii`) is
load-bearing for a cryptographic identity. That is a real fragility. It is
mitigated by the encoding being in one function that everything calls, and by
nothing else in the system constructing a disclosure id.

---

## 2. `full-service` fails on any skipped test, and a CI job was deleted rather than relaxed

**Decision.** The `full-service` gate treats a skipped test as a failure. When
that made the gate unsatisfiable in CI, the CI job was removed
(`.github/workflows/ci.yml:12-21`) instead of allowing skips there.

**The alternative.** Allow skips in CI only. The restricted corpus genuinely is
not in a public repository; skipping the tests that need it is not dishonest,
it is accurate. Keep the job, get the signal, note the exception.

**Why it loses.**

*The rule exists because of a specific incident.* This suite reports three
different results that all print "passed" — unit, CLI, and full-service. A stale
assertion once survived two commits inside that gap: it was skipped in the run
people looked at, and the run that would have caught it was the one nobody ran.
The zero-skip rule buys exactly one thing, which is that "passed" cannot mean
"did not run."

*An exception for the environment where the rule is inconvenient is not a rule.*
The moment skips are permitted in CI, the class of defect the rule was written
for — a test that quietly stops running — is invisible again, in the one place
that runs on every push. The exception would be granted precisely where it does
the most damage.

*The gate did not lose its home.* It runs on the machine that holds the corpus,
its transcript is hashed, and the hash is recorded in the handoff. What CI
attests and what the author's machine attests are now two clearly separated
claims instead of one blurred one.

**The cost this accepts, stated plainly.** The packaged demo gate is not
attested by CI. A reader who trusts only CI has to take the author's recorded
transcript on faith. That is a genuine weakening of the public evidence, it is
written into the workflow header and the handoff so no one has to discover it,
and it is the price of the rule meaning something.

**How to attack it.** "You deleted a failing check" is a fair first reading, and
the burden is on the project to show the check was structurally unsatisfiable
rather than merely red. The workflow header carries that argument; if it is not
convincing there, this decision is not defensible by asserting it harder.

---

## 3. L2-adv's dimension distribution is skewed, and was not corrected

**Decision.** The 16 adversarial groups distribute across the five distractor
dimensions as: `normative_strength` 7, `document_attribution` 5,
`role_attribution` 2, `request_vs_response` 1, `received_vs_generated` 1. Uniform
would be about three each. No group was rewritten to flatten it.

**The alternative.** Balance the design. Construct three or four per dimension so
the subset is a defensible sample and per-dimension results are reportable.

**Why it loses.**

*The construction rule is binding, and the corpus decides what satisfies it.* An
adversarial group needs a negative claim that a specific real clause **appears**
to support and genuinely does not. That near-miss has to already exist in RFC
9110 and 9112. The skew is the shape of those two documents showing through the
rule:

- The pair partitions semantics from syntax and restates related obligations at
  different normative strengths, which manufactures genuine near-misses for
  `normative_strength` and `document_attribution` in quantity.
- HTTP core usually states the request/response asymmetry inside the same clause
  that carries the obligation, so a scenario that flips it tends to be *obviously*
  wrong rather than a near miss — and an obviously wrong negative tests nothing.

*Flattening would have meant fabricating.* To reach three `received_vs_generated`
groups, someone would have to write near-misses the corpus does not contain. That
converts a measurement of the system into a measurement of the item author's
imagination, in a subset whose entire purpose is to be hard for a real reason.

*The claim is falsifiable, which is the actual defense.* Every group names the
exact distractor clause id it rests on. Anyone holding the frozen corpus can pull
that clause and check whether it really fails to support the negative claim. The
skew is not asserted to be principled; it is checkable.

*Construction failed five times in a way only reading catches.* During review,
five of the sixteen drafted groups were rejected because the negative claim was
in fact supported by a clause adjacent to the cited distractor — `dev-004` on a
User-Agent MUST, `dev-005` and `dev-006` in §6.2, `locked-005` in §9.3,
`locked-006` on soft wording. Each rejection required someone to have read the
neighbouring clauses. That failure rate is the evidence that the distribution
reflects the corpus rather than convenience.

**The cost this accepts.** With `n=10` locked groups at 7/5/2/1/1, three
dimensions have two or fewer items. **Per-dimension results are not reportable**,
and the W6 report must say so rather than presenting a five-bar chart built on
counts of one. The subset supports an aggregate matched-pair result and nothing
finer.

**How to attack it.** "You picked the easy dimensions" is the obvious challenge
and it cannot be answered with the distribution itself — only by opening the
cited clauses. A reader without corpus access has to take the falsifiability on
trust. That is why the clause ids are recorded per group and why `adv-add`
verifies every one against the frozen corpus before storing
(`adversarial_clause_not_in_corpus`), after an earlier draft was found carrying
digests that had been reconstructed from 16-character prefixes and were simply
wrong.

---

## Consequences

These three are written down because each is a place where the honest answer is
longer than the challenge. That asymmetry is normal for design decisions and
fatal in conversation: the person who has to reconstruct the reasoning live will
lose to the person who only has to ask the question.

If a future change makes one of these arguments false — a rebinding path for
disclosure ids, a CI runner that can hold restricted corpus material, an
expanded corpus that supplies the missing adversarial dimensions — the decision
should be revisited rather than defended. None of the three is a principle. All
three are trades against conditions that are recorded here so the trade can be
re-examined when the conditions change.
