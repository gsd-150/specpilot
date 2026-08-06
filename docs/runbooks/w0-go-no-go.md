# W0 go/no-go: evidence checklist and route decision

**Nothing here is approval.** The compliance assessment is the author's own
self-assessment; there is no external approver, and no command in this
repository can create one. Do not describe a completed checklist as clearance,
sign-off, or legal advice — to a reviewer, an interviewer, or yourself.

W0 ends with exactly one recorded state: **A**, **B**, **C**, or **extend**.
There is no undecided state, and W1 does not begin from one.

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

- [ ] Unsafe archives and OOXML packages are refused and quarantined, and no
      rejected input was ever repaired into an accepted one.
- [ ] An initial source manifest is default-deny, and a successor exists only
      where a completed assessment binds one route.
- [ ] A manifest that was never stored cannot authorize a route, even when it is
      internally consistent and says `authorized=true`.
- [ ] The maximum legal envelope is accepted at exactly the documented totals,
      and one more excerpt, TOC node, token, and byte are each refused with a
      stable code.
- [ ] Multi-round, retry, replay, over-reach, concurrency, and restart
      accounting all hold against a real PostgreSQL.
- [ ] Every refusal is a no-send: the fixture adapter's call count stayed zero.
- [ ] Compose and CI skeletons exist and publish no internal service ports.
- [ ] Fixture and CI output contains no recall, accuracy, F1, or other
      quality-looking number.
- [ ] The compliance conclusion is written, signed with an `author_id`, and
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

Allowed only when the cloud-egress conclusion is **no** and the target hardware
cannot sustain B. Write a new RFC-specific design and plan before W1 starts.

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
