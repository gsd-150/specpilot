# W6 adversarial adjudication sheet — author-owned, judge-blind

Material for the section 8.4 read of the five findings the sealed l2-adv dev
sweep (head 0e695d8) produced. This sheet names cases, verdicts, and the
question each finding asks; it deliberately quotes nothing. The claim text,
rationales, and evidence references live in the restricted artifacts named
below, which the adjudicator reads from the restricted store, not from here.

Sealed artifacts: artifacts/restricted/dev/l2-adv/*.json (12 outcomes).
Group records: artifacts/restricted/l2-adv/adv-dev-00N.json.

## The five findings, one question each

| case | design | sealed result | question for the read |
|---|---|---|---|
| 001-neg | negative must reach insufficient_evidence | violating, verified | Does the request-direction clause the artifact cites settle the claim outright? If it does, the group is decidable by construction and the false confirmation is a group defect, not a system defect. Stable across three runs. |
| 002-neg | negative must reach insufficient_evidence | insufficient after the semantic gate downgraded a determinate proposal | Was the downgrade right -- do the shown excerpts fail to settle the claim? (Across the three live runs this case returned violating, insufficient, compliant, in that order.) |
| 003-pos | positive must reach a determinate verdict | insufficient_evidence | The claim names an RFC 9110 rule; the artifact shows RFC 9112 evidence only. Confirm the governing clause is what the claim's section reference says, then decide: planner/retrieval defect, or a claim whose document attribution is genuinely ambiguous? Stable across every attempt. |
| 005-pos | positive must reach a determinate verdict | insufficient after the semantic gate downgraded a correct-looking violating | Do the shown excerpts support the proposed violating verdict? If they do, the ON arm's gate is a false rejection on this claim. |
| 006-pos | positive must reach a determinate verdict | two verified candidates survived side by side: one compliant, one violating | Are both candidates' citations valid against the shown excerpts? If both verified correctly, the aggregation admits contradiction and that is a system defect. |

## What a decided sheet must record, per section 8.4

For each of the five rows: the adjudicator's verdict (group construction /
system defect / neither), the clause sections read, and the date. The sheet is
evidence for the W6 report; it does not change the frozen configuration --
any change it motivates is W7 work against a new freeze.

The locked run proceeds with whatever the frozen configuration produces;
these five rows are what the report will have to say about the adversarial
set, decided here rather than under release pressure.
