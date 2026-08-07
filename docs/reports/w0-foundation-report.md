# W0 Foundation Report

**Commit:** `cc73e773c113a3891587d39ecf43bcae24b8d8b6`
**Branch:** `feat/w0-foundation`
**Date:** 2026-08-07
**Machine-readable evidence:** `../../artifacts/public/w0-verification.json`

**Route decision: `extend`. W1 does not begin.**

Nothing in this report is approval. The compliance conclusion it refers to is
the author's own self-assessment; no external party reviewed or cleared it.

## What actually blocks W0

The blocker is not compliance. The author completed the four-part assessment and
signed the main-chain conclusion, and the outbound gate accepts it.

The blocker is **ingestion**. Both chosen sources — TS 38.300 v18.10.0 and
TS 38.321 v18.10.0 — are refused by the safety boundary with
`embedded_active_content`, and both remain in quarantine. `data/real` holds zero
files. A source may be perfectly permitted to use and still be refused on its
own merits, and that is what happened here.

These are separate axes, and W0's evidence separates them cleanly. That is worth
saying plainly, because the intuitive reading of "a compliance week" is that the
compliance answer is the gate. It was not.

## Verification from a clean state

Every check below ran at the commit above, from cleared caches and an ephemeral
PostgreSQL database created for the run and dropped after it. All ten exited 0.

| Check | Result |
|---|---|
| Ruff | passed |
| mypy | 29 source files, no issues |
| Unit + CLI | 300 passed |
| PostgreSQL integration | 25 passed |
| Fixture smoke | 5 passed |
| Full suite | 330 passed, 0 skipped |
| Egress envelope smoke | passed |
| Fixture route smoke (main) | passed |
| Fixture route smoke (judge) | passed |
| Compose demo config | valid |

A fixture route smoke proves the transport, enforcer, and ledger are wired and
policy-bound. It proves nothing about any real provider, credential, or model,
and its own output says so in its `does_not_prove` field.

## Requirement checklist

| # | Requirement | Evidence |
|---|---|---|
| 1 | Unsafe archives and OOXML packages are refused and quarantined; no rejected input was repaired into an accepted one | 87 ingestion tests pass; both real sources refused with `embedded_active_content`; `data/real` empty, quarantine holds 2 originals unchanged |
| 2 | Initial source manifests are default-deny; a successor exists only where a completed assessment binds one route | 2 stored manifests, 0 successors, 0 with `cloud_egress_authorized` |
| 3 | A manifest that was never stored cannot authorize a route, even when internally consistent and claiming `authorized=true` | Enforcer resolves every manifest through the store it owns; covered by the unstored-source refusal test |
| 4 | The maximum legal envelope is accepted at exactly the documented totals, and one more excerpt, TOC node, token, and byte are each refused with a stable code | Envelope smoke accepts 29,696 transmitted tokens / 475,136 bytes and returns `excerpt_tokens_exceeded`, `excerpt_bytes_exceeded`, `root_unique_excerpts_exceeded`, `toc_run_exceeded` |
| 5 | Multi-round, retry, replay, over-reach, concurrency, and restart accounting hold against a real PostgreSQL | 25 integration tests pass against a live database, including 20-way concurrency for the last allowed excerpt and repository restart |
| 6 | Every refusal is a no-send | 13 transport fail-closed tests assert the fixture adapter's call count stayed zero |
| 7 | Compose and CI skeletons exist and publish no internal service ports | Compose demo config validates; 4 exposure tests pass |
| 8 | Fixture and CI output contains no quality-looking number | Smoke outputs carry none; the only occurrences of those words in the repository are the rules forbidding them |
| 9 | The compliance conclusion is written, signed with an `author_id`, and carries an expiry | Signed by `chunxue`, expires 2026-09-06T14:44:00Z, bound to `deepseek` / `online-main-deepseek-v4-flash-api` / `online_main` |

## Compliance state

Four source-bound assessment envelopes exist against two route-bound evidence
indexes built from 15 frozen official snapshots.

| Envelope | State |
|---|---|
| TS 38.300 → deepseek / online_main | complete |
| TS 38.321 → deepseek / online_main | complete |
| TS 38.300 → chatanywhere / offline_judge | unsigned |
| TS 38.321 → chatanywhere / offline_judge | unsigned |

The conclusion gate binds the provider's own API-governing documents —
`deepseek-api-docs`, `deepseek-privacy`, `deepseek-terms` — each matched to a
supplied evidence record by document hash, URL, and capture time, and none
captured after the conclusion was authored. It was exercised negatively as well:
a tampered hash, a missing required document, absent evidence, one document
impersonating three kinds, a swapped URL, and a back-dated capture time are each
refused.

An earlier version of that gate required a screenshot of the data-use toggle in
DeepSeek's consumer chat product. That toggle governs a different surface than
the API route being authorized, so it could neither establish nor refute how the
API handles the data. It was replaced. The account record remains in the index as
optional context.

Two uncertainties the author recorded are load-bearing and are not resolved by
anything in W0: DeepSeek's privacy policy is silent on training for the open
platform while its user agreement's training clause sits in a chapter that
covers the API, and ChatAnywhere discloses nothing either way about training
while routing inputs and outputs onward to third-party model providers.

## Why `extend` and not A, B, C, or D

- **A** requires separately bound authorized successor manifests, one per use.
  There are zero successors. Not met.
- **B** requires the cloud-egress conclusion to be **no**. It is yes. Not met.
- **C**'s second trigger does fire — the chosen corpus cannot be safely ingested.
  But the runbook requires the two cheaper variants first: a different 3GPP
  specification whose DOCX carries no embedded objects, then the ETSI PDF or
  3GPP HTML rendering of the same specification. Neither has been tried, so
  choosing C now would discard the telecom narrative before checking whether it
  can be kept for about an hour's work.
- **D** does not move W0 to a pass by its own definition, and remains priced
  against C rather than chosen.

`extend` is therefore the only valid record. It is the designed resting state,
not a failure: the gate is closed, nothing is authorized, and no real source text
can reach a provider.

## What W0 does not establish

- No quality claim of any kind. W0 measures nothing about answers.
- No evidence that the real 3GPP corpus parses, is retrievable, or is usable —
  the opposite is now measured, for these two documents.
- No provider latency or cost figure. The fixture route has neither.
- No claim that the outbound caps are the right caps, only that they are
  enforced, atomic, and durable. The corpus cap ships as a tripwire sized just
  above the all-distinct worst case; W5's dev dry-run produces the first real
  distinct-disclosure count.
- No external approval of anything.

## Next action

Route research, in this order, before any W1 work:

1. A different 3GPP specification whose DOCX carries no embedded objects.
2. The official ETSI PDF or 3GPP HTML rendering of the same specification.

Only if both are recorded as failed, and the corpus is recorded as genuinely
irreplaceable, may a separately reviewed derivative plan be written.
