# L1 dev refusal diagnosis — 2026-08-16

Fixture-free evidence, produced during the first author-run L1 dev pass and the
judge calibration that followed it. The dev run answered 9 of 13 answerable
L1 dev cases and refused 4; this records why each refusal happened, what was
checked, and what was deliberately left unchanged. It is a failure analysis,
not a repair log: every finding below was recorded as evidence, and none of it
tuned the system.

## Calibration coverage

The judge calibration evidence built on 2026-08-16 covers **9 of 13**
answerable L1 dev cases. The four refusals are outside it: a refusal has no
answer to score, and its disposition is recorded here instead. The dev set
also contains 2 unanswerable cases, which the refusal metrics own rather than
the judge.

## The four refusals, by class

### 1. Retrieval gap — `l1-dev-002`

The question speaks of resending a POST after a dropped connection; the gold
clause forbids automatically retrying non-idempotent requests without naming
POST. Measured per route:

| route | gold rank |
| --- | --- |
| BM25 | not in top-20 (lexical gap; question-gold Jaccard 0.065) |
| dense | 6 (one rank outside `final_top_k = 5`) |
| RRF (online) | 13 (the weak BM25 evidence dilutes the dense rank-6 signal) |

Disposition: recorded, not fixed. Any change to `dense_top_k`, `final_top_k`,
or `rrf_k` changes the frozen corpus manifest's retrieval protocol and
therefore its identity hashes — a deliberate gate, not a parameter someone
forgot to tune. One dev case does not justify moving it.

### 2. Multi-anchor retrieval limitation — `l1-dev-010`

The question spans two adjacent anchors of §15.4.5: the obligation paragraph
and the field list it introduces. Both are gold; retrieval finds exactly one
of the two at k=5 on every measured pass — the 2026-08-15 evaluation found the
list at rank 1 and missed the obligation paragraph, and the 2026-08-16
diagnostic found the obligation paragraph at rank 1 on both routes and missed
the list entirely. Shown one anchor, the model correctly refuses: the
obligation ends in a colon whose content is the missing list.

This is the one item behind the recorded `all_required_hit_rate = 0` on every
route, and it is the reason the §8.4 cross-reference expansion metric exists.
The expansion feature is out of first-release scope; the case stays a recorded
limitation.

Correction recorded here for the audit trail: an earlier diagnosis of this
case read the root annotation record instead of the chain head and concluded
the gold set was incomplete. The head already carried both gold clauses. An
`annotation amend` run under that mistaken diagnosis was a no-op for gold
(the store deduplicates added clause ids) and left only an audit-chain
successor whose note confirms the Task 12 consecutive-paragraph rule. The
chain is append-only and the note is true, so the successor stands.

### 3. Model-side conservatism — `l1-dev-011`, `l1-dev-016`

Both gold clauses fully settle their questions and were retrieved at rank 1 on
both routes — the evidence was on the wire, and the model still refused with
`evidence_insufficient`. That is a fail-closed refusal, not a crash and not a
retrieval fault. Disposition: recorded, not fixed — relaxing the answer prompt
for two cases would move every case, and the refusal behaviour is a designed
outcome the refusal metrics measure, not a defect to paper over.

## Numbers carried into the calibration record

Judge calibration over the 9 scored cases (prompt v1, model `glm-5.2`,
route `offline_judge`):

- key points: n = 18, agreement 18/18, Cohen's kappa 1.0;
- answer claims: n = 12, agreement 11/12 (0.917), Cohen's kappa 0.0 with the
  single disagreement on `l1-dev-008`'s SHOULD-strength claim (judge
  `supported`, author `insufficient`), severe-flag agreement 12/12.

The claim kappa of 0.0 beside an agreement rate of 0.917 is the unbalanced-
marginal kappa paradox — the judge rated every claim `supported`, so chance
agreement absorbed the observed agreement. §8.3.2 requires the four numbers
together (rate, kappa, confusion, counts) precisely because any one of them
misreads this distribution.

The evidence file sha256 is `392900c592f881102d90597ffd34ec6f9e5c51449fa7e9be45ccaa36fb51117a`.
