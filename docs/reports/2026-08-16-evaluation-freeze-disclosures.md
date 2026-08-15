# Evaluation freeze: what the frozen numbers do and do not mean

Companion to `w5-streaming-demo-and-freeze.md`, written at the freeze rather
than before it. Everything here qualifies a figure that the frozen run spec now
binds, and each item exists because reading the figure without it would support
a claim the evidence does not.

The freeze itself: run spec
`a4b3f8ca1c34466b60bc407ded38c73d763e07fd3801047a1ea212eb5d5e7cc5`, confirmed by
`chunxue` over candidate
`2b42ab42abd4935151bd6f00d25262c91eea151ae05682d309680d6047700b30`, scoring
route `judge_calibrated`, bound to commit `d2998ff` and tagged
`evaluation-freeze-2026-08-16`. That commit passed the packaged hard gate:
ruff clean, mypy over 133 source files, 1,824 unit, 225 CLI, 153 frontend,
2,388 full-service in 49.82s, 5 browser, `packaged_demo_gate=passed`, transcript
SHA-256 `119912307ba06aedd4566b1aefb998357be8c774cd4740005e892d65bbb9dde4`. `HEAD`
did not move during that run, so the evidence belongs to the frozen tree rather
than spanning two of them.

## 1. The L2-adv dimension distribution is skewed, and the skew is a finding

Sixteen groups: `normative_strength` 7, `document_attribution` 5,
`role_attribution` 2, `request_vs_response` 1, `received_vs_generated` 1. The
locked ten cover only two of the five axes.

This is not sampling laziness. A negative case needs the corpus to be genuinely
silent about the verdict it asserts, and only two gap shapes survived checking:
a normative strength that cannot reach a determinate verdict (SHOULD, MAY,
"ought to"), and a question asked of the wrong document. Role and direction gaps
mostly do not survive. Section 5.2 was abandoned after a scan showed all four
actors enumerated — sender, server on a request, proxy or gateway on a response,
user agent on a response — leaving no silence to build on.

So the distribution reports something true about RFC 9110/9112: they are precise
enough about role and direction that honest adversarial cases on those axes are
scarce. A balanced distribution would have required inventing cases whose
negatives the corpus actually settles.

## 2. The seven `normative_strength` groups have weak matched-pair power

Their distractor is itself a SHOULD, MAY, or "ought to" — evidence that visibly
declines to establish the asserted verdict. A competent Verifier passes these
almost by construction, so the matched-pair miss rate on this subset will run
near zero and that number will say nothing about Verifier quality.

The end-to-end negatives on the same groups retain value, because the corpus
does contain tempting MUST clauses nearby. But any miss-rate figure computed
over the `normative_strength` groups must not be read as evidence that the
Verifier is discriminating. The five `document_attribution` groups carry that
weight.

## 3. Positive and negative are not minimal rewrites on the strength axis

Section 8.1.1 describes the positive claim as the negative minimally rewritten.
On the `normative_strength` groups they are instead different scenarios from the
same section — for group 001, failing to bound chunk-extension length versus
rejecting an unrecognized extension.

That is structural, not sloppy. A SHOULD cannot support a determinate verdict in
either direction, so the positive half has to rest on a different clause, and a
different clause means a different scenario. The plan's wording and this axis's
construction cannot both hold; the construction is what the axis measures.

## 4. The `document_attribution` negatives assume a strict document reading

Groups 007–010 assert that a requirement stated in RFC 9110 does not establish a
violation "of a requirement stated in RFC 9112". Under the loose reading common
in protocol work — where "violates RFC 9112" means "violates HTTP/1.1" — RFC
9112 §1's statement that HTTP/1.1 is defined by the three documents together
would let a 9110 MUST reach the conclusion, and all four negatives would fail.

Three facts support the strict reading. RFC 9112 contains no counterpart
requirement in any of the four cases: zero occurrences of Max-Forwards, zero of
Upgrade, no `connection-option` MUST, and nothing binding Content-Length in a
HEAD response. The "defined by" statement makes HTTP/1.1 the union of three
documents without rewriting any clause's document attribution. And the system's
own discipline is document-bound throughout — clause ids carry document and
version, the enforcer checks manifest scope.

The claims were worded as "violates a requirement stated in RFC 911x" to close
the ambiguity in the claim rather than rely on this note. A Verifier holding the
loose reading would still fail these four, and that is a property of the axis,
not a defect.

## 5. Locked groups share supporting clauses among themselves

`33d2ef7d` supports three locked groups and `a39b8914` supports two. The freeze
gate checks only dev-versus-locked disjointness, so this is legal, but it means
the ten locked groups are not ten independent draws on the supporting side.

## 6. The L2 chain had seven wire-contract gaps, and three of my attributions were wrong

The recurring shape is the one AGENTS.md records: a value present in the code and
absent from the bytes that left. Instances four through seven all concerned the
same instruction:

1–3. Earlier instances (attribution line never rendered; reply contract exported
   and referenced nowhere; an identifier the model was asked to cite and never
   shown).
4. The compliance model invented evidence ids matching no shown excerpt, and the
   deterministic gate killed every candidate.
5. Repairing 4 introduced a quota: "evidence_ids must be exactly those shown
   identifiers" against a schema capping the list at four while cases are shown
   five or six, leaving the model an unsatisfiable instruction whose only
   consistent escape was `insufficient_evidence` with an empty list.
6. Repairing 5 left determinate candidates free to cite nothing, since no
   sentence said a compliant or violating candidate must cite at all.
7. Neither L2 instruction forbade reasoning from specification memory, a rule
   L1 has always carried.

Three attributions made during this diagnosis were wrong and are recorded
because the corrections are the useful part:

- **"Retrieval never surfaced the clause."** Wrong. The ledger shows gold reached
  the model in seven of eight dev cases; `citation_count: 0` meant the model
  declined to cite, not that it was shown nothing.
- **"The planner's queries were malformed."** Wrong, and built on the first
  error. BM25 over the case text alone ranks gold first or second in seven of
  eight cases.
- **"The outcome artifact records no evidence set."** Wrong; it had recorded one
  since 03:24 that day, and the field was misread.

All three shared a cause: treating the model's own rationale, or an incomplete
read, as fact without checking the ledger. `specpilot egress disclosures` exists
so that the question "was this clause actually disclosed" has an answer that does
not depend on anyone's recollection.

## 7. The dev batch scores 8/8 on verdict, and one of the eight is not clean

The canonical batch — eight cases, single prompt identity
`eecc5d4c0b98`, gold disclosed in all eight — matches gold on every verdict.

`l2-dev-003` should not be counted as a clean success. Its verdict is
`insufficient_evidence` and gold agrees, but its rationale asserts that "a server
responding to such a request must close the connection". No shown excerpt says
so: the case was given §6.3's intermediary rule, two §6.1 clauses, and §5.1.
The requirement is real in RFC 9112 and was never disclosed to the model, and
the rationale then uses it — "the report also does not record whether the
connection was closed" — to support the insufficiency finding.

The memory prohibition (item 6, instance 7) changed the wording and not the
behaviour. Before it, the model wrote "RFC 9112 requires…" directly; after, it
writes "the shown excerpts make the proxy's obligations depend on…" and still
names an obligation it was not shown.

This instance is the hardest of the family to detect and the reasons are
structural. A candidate whose verdict is `insufficient_evidence` must carry an
empty evidence list, so the deterministic gate has nothing to check — citing
nothing is correct there. Every earlier instance cited something wrong and died
on the wire. This one cites nothing and asserts anyway.

**Any statement of L2 dev verdict accuracy must carry this.** Eight of eight is
accurate as a verdict count and overstates the number of cases whose reasoning
stayed inside the disclosed evidence, which is seven.

## Retrieval and judgement are separate accounts

Kept apart because conflating them produced two of the three wrong attributions
above. On the canonical batch: gold disclosed 8/8, verdict matched 8/8,
citations present in 6/8. The two zero-citation cases are `l2-dev-003` (above)
and `l2-dev-008`, whose gold reached the model only after the planner document
scope changed — it was the single retrieval miss in every earlier batch and BM25
over the case text does not rank its gold in the top five.

A verdict that matches gold while citing nothing is not evidence that the chain
worked. It is the refusal behaviour landing on a case where refusal is correct.
