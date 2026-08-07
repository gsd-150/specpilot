# W0 go/no-go: evidence checklist and route decision

**Nothing here is approval.** The compliance assessment is the author's own
self-assessment; there is no external approver, and no command in this
repository can create one. Do not describe a completed checklist as clearance,
sign-off, or legal advice — to a reviewer, an interviewer, or yourself.

W0 ends with exactly one recorded state: **A**, **B**, **C**, or **extend**.
There is no undecided state, and W1 does not begin from one.

## Recorded decision — 2026-08-07

**`extend`**, at commit `cc73e773c113a3891587d39ecf43bcae24b8d8b6`.

Every checklist item below is met and all ten verification commands exit 0 from
a clean state, and the answer is still `extend`: A needs an authorized successor
manifest that does not exist, B needs a cloud-egress conclusion of *no* when the
recorded one is *yes*, and C's corpus trigger fires but its two cheaper variants
have not been tried.

The blocker is ingestion, not compliance. Both chosen sources are refused with
`embedded_active_content` and sit in quarantine, while the main-chain conclusion
is written and signed.

Evidence: `../../artifacts/public/w0-verification.json` and
`../reports/w0-foundation-report.md`.

## Evidence to gather

Re-run everything from a clean state and record the command, timestamp, code
hash, exit code, and test counts. Do not copy source text, payload text,
credentials, or raw logs into the record.

```bash
make lint && make typecheck && make unit
```

```bash
SPECPILOT_TEST_DSN=<throwaway-dsn> make integration-db
```

```bash
python -m specpilot.cli egress envelope-smoke
```

```bash
python -m specpilot.cli provider route-smoke --fixture-only --route main --ledger-dsn <throwaway-dsn>
```

### Checklist

- [x] Unsafe archives and OOXML packages are refused and quarantined, and no
      rejected input was ever repaired into an accepted one.
- [x] An initial source manifest is default-deny, and a successor exists only
      where a completed assessment binds one route.
- [x] A manifest that was never stored cannot authorize a route, even when it is
      internally consistent and says `authorized=true`.
- [x] The maximum legal envelope is accepted at exactly the documented totals,
      and one more excerpt, TOC node, token, and byte are each refused with a
      stable code.
- [x] Multi-round, retry, replay, over-reach, concurrency, and restart
      accounting all hold against a real PostgreSQL.
- [x] Every refusal is a no-send: the fixture adapter's call count stayed zero.
- [x] Compose and CI skeletons exist and publish no internal service ports.
- [x] Fixture and CI output contains no recall, accuracy, F1, or other
      quality-looking number.
- [x] The compliance conclusion is written, signed with an `author_id`, and
      carries an expiry.

## Route decision

Record one. The bar for each is evidence, not intent.

### Route A — cloud main chain

Allowed **only** when both are true:

- Both real provider routes have been fixture-smoked and their tool-calling and
  structured-output behaviour observed.
- Separately bound authorized successor manifests exist, one per use
  (`online_main` and `offline_judge`).

A fixture route smoke passing is **not** evidence for A. It proves the transport,
enforcer, and ledger are wired and policy-bound; it proves nothing about any real
provider, credential, or model. The smoke output says so in its own fields.

### Route B — local main chain

Allowed only when the cloud-egress conclusion is **no**, and local
structured-output, tool-calling, latency, and cost have been smoked and
evidenced on the target hardware.

### Route C — compatible corpus

Allowed when **either** holds:

- The cloud-egress conclusion is **no** and the target hardware cannot sustain B.
- **The chosen corpus cannot be safely ingested.** This is a separate trigger
  from the compliance conclusion, and the original table missed it: a source may
  be perfectly permitted to use and still be refused by the ingestion boundary
  on its own merits. TS 38.300 v18.10.0 is exactly this case — its DOCX carries
  119 embedded OLE objects and an external `attachedTemplate` relationship, and
  `inspect_docx` refuses it with `embedded_active_content`. That refusal is
  correct and is not a compliance question.

Write a new RFC-specific design and plan before W1 starts.

Product plan §3.2 pre-registered IETF RFC for this reason among others, in its
own words: plain text or XML distribution, which *省掉整套 OOXML 沙箱风险面* —
it removes the entire OOXML sandbox risk surface. That risk surface is now
measured rather than hypothetical, which strengthens the case rather than
weakening it.

Before choosing C, check the cheaper variants of the same idea in this order,
because each takes about an hour:

1. A different 3GPP specification whose DOCX carries no embedded objects.
2. The ETSI PDF or the 3GPP HTML rendering of the same specification.

Either keeps the telecom-specification narrative that C otherwise gives up.

### Route D — reviewed derivative

Build a separately reviewed, content-addressed derivative of the refused source
under ADR 0001, and ingest that instead of the original.

**Do not choose D without pricing it against C first.** D is a new subsystem —
an OPC graph analyzer, a deterministic transformer, a provenance record, and a
manifest schema version — comparable in size to all of W0. It is only the right
answer when the specific corpus is genuinely irreplaceable for the project's
narrative and options 1 and 2 above have both been tried and recorded as
failing.

Choosing D does not move W0 to a pass. The derivative unblocks parsing; it does
not create an authorized route, and Task 10 still records `extend` until a
successor manifest exists.

### extend

Anything else. If the evidence is incomplete, or the conclusion is not written,
or the route smoke is blocked rather than passed, the answer is `extend` and W1
does not begin.

**A blocked result is a valid outcome and must be recorded as blocked.** Never
convert a missing credential, an unreachable ledger, or an unfinished assessment
into a pass.

## What W0 does not establish

Worth writing down, because it is what a careful reader will ask:

- No quality claim of any kind. W0 measures nothing about answers.
- No evidence that the real 3GPP corpus parses, is retrievable, or is usable.
- No provider latency or cost figure. The fixture route has neither.
- No claim that the outbound caps are the *right* caps. It establishes that they
  are enforced, atomic, and durable — not that the numbers are well chosen. The
  corpus cap in particular ships as a tripwire sized just above the all-distinct
  worst case; W5's dev dry-run produces the first real distinct-disclosure count,
  and only then is there a basis for lowering it.
