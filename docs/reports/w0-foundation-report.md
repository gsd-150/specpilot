# W0 Foundation Report

**Commit:** `cc73e773c113a3891587d39ecf43bcae24b8d8b6`
**Branch:** `feat/w0-foundation`
**Date:** 2026-08-07
**Machine-readable evidence:** `../../artifacts/public/w0-verification.json`

**Route decision: `C` — compatible corpus. The corpus moves to IETF RFCs.
W1 does not begin until a new RFC-specific design and plan exists.**

This supersedes the `extend` first recorded at commit `cc73e77`, and the
sequence matters more than the final letter. `extend` was correct when written:
C's corpus trigger had already fired, but the runbook requires two cheaper
variants to be tried first, and an untried variant is incomplete evidence. Both
were then tried and recorded — variant 1 failed outright, variant 2 turned out
available at a price the author declined — which is the condition C asks for.

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

## Why `C` and not A, B, or D

- **A** requires separately bound authorized successor manifests, one per use.
  There are zero successors. Not met, and unaffected by the corpus change.
- **B** requires the cloud-egress conclusion to be **no**. It is yes. Not met.
- **C** is selected. Its second trigger fired — the chosen corpus cannot be
  safely ingested — and both cheaper variants have now been tried and recorded.
- **D** does not move W0 to a pass by its own definition, and costs a new
  subsystem comparable in size to all of W0. C is cheaper and was pre-registered.

### Variant 1 — a different 3GPP DOCX: failed

Three further Release 18 specifications were inspected. All were refused and all
carry embedded objects: TS 38.331 (73), TS 38.322 (18), TS 38.323 (28). With
38.300 (119) and 38.321 that is five distributions across architecture, MAC,
RRC, RLC, and PDCP. Embedded Visio and OLE objects are normal in this format.

TS 38.331 is worth naming, because it was the tempting one: same `ia0` version
line as the frozen corpus, and RRC is the most valuable specification here. It
fails with `xml_too_large` rather than `embedded_active_content`, which reads
like a tunable. Re-running with the XML limit at 128 MB returns
`embedded_active_content`. The limit was not raised — doing so would have traded
one honest refusal for another while weakening a real resource boundary.

### Variant 2 — ETSI PDF or 3GPP HTML: available, declined

The 3GPP HTML rendering does not exist; that URL is a metadata portal page. The
ETSI PDF does exist and is comparatively clean — a byte-level token scan found
no `/JavaScript`, `/Launch`, `/EmbeddedFile`, `/AA`, `/RichMedia`, `/GoToR`, or
`/SubmitForm`. It costs a version step back, since ETSI publishes up to v18.9.0
while the corpus is frozen at v18.10.0, and it costs a PDF layout-recovery
parser that product plan §3.2 excluded on purpose. The author declined that
trade.

### What the RFC corpus measures

Product plan §3.2 pre-registered IETF RFC partly because plain-text or XML
distribution removes the OOXML sandbox risk surface. That was a hypothesis. It
is now measured on both sides.

RFC 9110, 9111, and 9112 — the HTTP core suite — carry 291, 65, and 59 numbered
sections and 86, 51, and 56 cross-document section references respectively,
reproducing the dense normative cross-referencing that 38.300 and 38.321 were
chosen for. RFC 9110's XML carries 305 `<section>` and 2,519 `<xref>` elements,
so cross-references arrive machine-readable rather than recovered from text
patterns, and it contains zero DOCTYPE, entity declarations, external-entity
references, or stylesheet processing instructions.

The ingestion path shortens from `ZIP → DOCX → OOXML part graph → embedded
objects and external relationships` to a direct fetch of text or structured XML.

### What carries over

Everything provider-side is corpus independent and survives: the egress policy
and its caps, the atomic ledger, the policy-bound transport, both evidence
indexes, the API-policy conclusion gate, and both providers' assessed
retention, training, region, and subprocessor findings.

The source side does not. The 3GPP and ETSI terms assessments describe a corpus
no longer in use; they stay as records of what was assessed. The RFC corpus
needs its own source manifests and its own source-terms assessment against
BCP 78 and the IETF Trust Legal Provisions.

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

Write the RFC-specific design and plan. W1 does not begin without it. It has to
settle at least:

1. Which RFC suite is frozen, at which format and which published revision.
2. Source manifests for RFC documents, and a source-terms assessment against
   BCP 78 and the IETF Trust Legal Provisions.
3. Whether the parser consumes `.txt`, the v3 XML, or both — the XML carries
   sections and cross-references as elements, the text does not.
4. What replaces the OOXML inspection boundary. The archive and OOXML risk
   surface is gone, but XML parsing keeps its own; `defusedxml` stays.
5. Which existing tests describe 3GPP-shaped inputs and must be retargeted
   rather than deleted.
