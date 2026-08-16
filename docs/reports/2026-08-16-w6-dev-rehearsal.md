# W6 dev rehearsal — Task 3

Purpose: find the defects in the W6 sweep path while they are still free. The
locked run is one-shot and spends real budget; this one is neither.

**Status: all three dev sweeps run live and sealed.** L1 and L2 dev ran clean
on 2026-08-16; the L2-adv dev sweep needed three attempts before a clean run
(sealed at `0e695d8`). The rehearsal did what it exists to do: everything below
was found on dev, where it costs nothing.

## Environment, checked 2026-08-16

| | |
|---|---|
| Colima | running, macOS Virtualization.Framework, aarch64 |
| Qdrant | `/readyz` OK on `localhost:6333` |
| PostgreSQL `specpilot_live` | reachable, 11 public tables |
| BGE-M3 weights | present, 2.1 GB, `data/cache/models/bge-m3` |
| Frozen renditions | RFC 9110 1,223,686 B and RFC 9112 262,821 B |
| Corpus/source manifests | **not in the worktree** — see finding 1 |
| `SPECPILOT_MAIN_API_KEY` | not set here; the author supplies it |

## Selection, verified against the restricted store

No provider involved: `sweep plan` reads the stores and prints the work list.

| level | split | groups | invocations |
|---|---|---|---|
| l1 | dev | — | 12 answerable (15 with `--include-unanswerable`) |
| l2 | dev | — | 8 |
| l2-adv | dev | 6 | 12 |
| l1 | locked | — | 20 answerable, 25 with refusals |
| l2 | locked | — | 12 |
| l2-adv | locked | 10 | 20 |

Locked totals 57 invocations, which is what Task 6 will spend. The dev
rehearsal is 35 and exercises the same three code paths.

## What the rehearsal found before spending anything

**1. The driver could not resolve the manifests from the freeze worktree.**
`manifests/` is gitignored, so a git worktree does not carry it and the store
stays in the main checkout. `scripts/run_sweep.sh` defaulted to the local path,
found nothing, and exited 3 on its first run. The predecessor script handled
this with a hardcoded `cd ../..`; the replacement resolves it through
`git rev-parse --git-common-dir` and refuses if neither location has it. Fixed
and verified from inside the worktree.

**2. The batch prompt-identity guard passed against zero artifacts.**
Inherited from `tmp/run_l2_dev.sh`, which skipped any artifact lacking a
`compliance_prompt_sha256` and then printed "batch prompt identity verified".
On a directory holding no case outcomes that is a pass earned by the absence of
evidence — the guard reports success precisely when it has checked nothing. It
now counts what it checked and refuses if that is fewer than the case count.

Both failure modes are now covered by a stub run rather than an argument:

| scenario | before | now |
|---|---|---|
| no outcome files | `verified` | `checked 0 of 8`, exit 1 |
| one artifact from a different prompt generation | mismatch, exit 1 | unchanged |
| all artifacts coherent | `verified` | `verified across 12`, exit 0 |

**3. The driver would have run the wrong tree's code.** The same defect family
as finding 1, and the most serious of the three. A git worktree carries no
`.venv`; the environment lives in the main checkout as an editable install. So
an unqualified `import specpilot` from that interpreter resolves to the **main
checkout's** source — currently commit `7858002`, from before W5 began. Every
superseded driver in `tmp/` set `PYTHONPATH="$PWD/src"` by hand for exactly this
reason and the replacement did not.

The script now resolves its own repository root from `${BASH_SOURCE[0]}`, changes
into it, resolves the interpreter, exports `PYTHONPATH`, and then **asserts**
that the interpreter's `specpilot` lives under that root, refusing otherwise.
Verified by invoking it from an unrelated directory with no `cd` and no
`PYTHONPATH`: it resolved the tree, loaded the weights, retrieved five clauses
and reached the provider. There is no `cd` to remember.

**4. A refused invocation created a directory under `locked/`.** The output
directory was made before arguments were validated, and the default path carries
the split — so `--split locked` with a missing `--source-manifest` left
`artifacts/restricted/locked/l2-adv/` behind, in the namespace that must stay
empty until W6 executes. The directory is now created only after every check and
the plan have passed. Both refusal paths verified to leave the filesystem
untouched.

**5. All three levels run end to end under a stub provider.**

- `l1` — 10 answered wrote one artifact each; 2 refusals went to `refusals.log`
  and wrote no answer file, which is what keeps the judge scoring answered cases
  only.
- `l2` — 8 completed, one prompt identity across the batch.
- `l2-adv` — 6 groups produced 12 invocations, both halves adjacent and each
  line carrying its `group_id`.
- Transport retry — two `provider_unreachable` responses retried and the third
  attempt completed. The predecessor died here with `CODE: unbound variable`.
- `HEAD` compared before and after; a sweep spanning two trees fails.

## One operator event, disclosed

Verifying finding 3 meant invoking the real path with a placeholder key. It
reached the provider and was rejected, and the fail-closed design behaved
exactly as designed: one `egress_reservation` row for root
`case-1786869178-l1-dev-002`, recorded `state = failed_known`, no attempt
consumed, and `egress_corpus_ledger_head` still reading its previous
`updated_at` of 2026-08-15 23:47 — so **no cap budget was drawn and no money
was spent**. No answer artifact was written. Two empty directories it created
were removed, restoring the prior state.

Recorded here rather than omitted because Task 6 Step 4 requires operator events
to be disclosed, and a discipline that only starts at the locked run is not a
discipline.

## L1 dev, run live 2026-08-16

Twelve answerable cases, twelve provider calls, twelve `succeeded` attempts in
the ledger. Nothing was cached — the response cache is opt-in by argument and no
argument was passed.

**Item coverage is identical to the superseded driver's batch**: the same eight
answered and the same four refused, with the same reason. That was Task 1 Step
5's acceptance condition and it is met. Four of the eight answers are byte-
identical to the earlier run and four differ, so the chain is substantially but
not fully reproducible; a first sample of two happened to be identical and read
as determinism, which it is not.

### Cost and latency, measured

| | |
|---|---|
| cases | 12 |
| wall clock | 108 s, ≈ 9.0 s per case |
| provider duration | p50 4.28 s, p95 6.07 s, range 3.39–6.08 s |
| request tokens | avg 744, range 577–883, total 8,926 |
| request bytes | avg ≈ 2,830, range 2,138–3,342 |

Wall clock is roughly twice provider duration: about 5 s per case is local work —
BGE-M3 encoding, BM25, and the Qdrant round trip — which no provider budget
covers and no cache would shorten.

**Extrapolation to locked L1 only**: 25 cases at 9.0 s ≈ 3.8 minutes and ≈ 18,600
request tokens. L2 and L2-adv are multi-stage chains and cannot be extrapolated
from a single-call level; their dev sweeps have to supply their own figures.

### The four refusals split two and two, and the split is the finding

All four cases were annotated answerable, so a refusal is a miss. Asking the
ledger what actually left the machine — not what the model said about it —
separates them cleanly:

| item | verdict | gold clauses | disclosed | gold shown |
|---|---|---|---|---|
| l1-dev-002 | refused | 1 | 3 | **no** |
| l1-dev-010 | refused | 2 | 5 | partial |
| l1-dev-011 | refused | 1 | 5 | yes |
| l1-dev-016 | refused | 1 | 5 | yes |
| the other 8 | answered | 1 each | 3–5 | yes, all |

Two are retrieval failures: the system never saw the clause it needed, and
`evidence_insufficient` is the correct verdict on the evidence it had.

Two are judgement failures: the gold clause was disclosed and the system still
reported insufficient evidence. Those are false refusals, and they are the ones
worth attention — retrieval can be improved without touching the frozen chain,
a false refusal cannot.

So on L1 dev: gold fully disclosed in 10 of 12, and given gold, answered 8 of
10. Both figures belong in any statement of L1 behaviour, because quoting the
8/12 answer rate alone attributes a retrieval miss to the model and a model
miss to retrieval.

Corroborating §8.5.2 A′ incidentally: three cases disclosed fewer than the five
allowed excerpts (two at three, one at four). Idle quota is exactly what the
E-narrow/E-context arm was rewritten to measure.

## L2 dev, run live 2026-08-16

Eight cases, all completed, head `bed25c4`. One transport retry on
`l2-dev-005` (`invalid_tool_plan`) — the retry path's first live exercise,
which the predecessor script would have killed with its unassigned `${CODE}`.

| | |
|---|---|
| cases | 8 |
| wall clock | 9 min 36 s, ≈ 72 s per case |
| attempts | 23 (planning + compliance + verifier, all `succeeded`) |
| request tokens | 44,792 total |
| prompt identity | `eecc5d4c0b98…` verified across 8 — the same identity the freeze handoff records for the canonical dev batch |

Final verdicts: 001 violating, 002 compliant, 003 insufficient_evidence, 004
violating, 005 compliant, 006 violating, 007 compliant, 008
insufficient_evidence. Citations present in 6 of 8; the two insufficient cases
carry empty evidence lists, which is what the closed contract requires.
Scoring this batch against gold is a judge step and is not done here.

## L2-adv dev — three attempts, one sealed run

### Attempt 1 — aborted by the author (`c42813e7…`, RFC 9110 authorization)

Five cases completed before the author stopped the run. Its value: measured the
cross-document question this report carried as open. Under a single-document
authorization the L2 chain disclosed evidence from the *other* document —
reservations all `succeeded`, nothing refused. **The enforcer does not refuse
cross-document evidence on the L2 path**; the authorization gates the route
binding, not the evidence's document. The open question is answered: the
`--source-manifest` choice is a ledger bookkeeping decision, not an evidence
gate.

### Attempt 2 — killed by an unclassified TLS fault (`b74abd04…`)

Hard abort at `adv-dev-003-pos`: planning succeeded after 61 s, then the
compliance send failed in 980 ms as `provider_unclassified_error` — the
transport's bucket for exceptions outside `ProviderError`. The sweep driver
deliberately does not retry that class, so the batch died.

Diagnosis: a probe with the author's real key reproduced it, and a temporary
local stderr traceback showed the raw fault:

    ssl.SSLError: DECRYPTION_FAILED_OR_BAD_RECORD_MAC

mid-response-body, in the TLS stream read. Two facts made this a finding rather
than a mystery: the same deterministic planning bytes succeeded at 17:36 and
failed at 18:02, and a control case (`adv-dev-001-pos`) completed between two
failures — so the path was healthy and the fault is an intermittent transport
break, not a payload or provider-contract defect. Fail-closed held throughout:
every failure recorded `failed_known` with zero tokens measured, no budget
leaked.

(One probe in the series failed with `provider_unauthorized` first: the copied
command contained the literal placeholder key `...`. Operator error, zero cost,
disclosed here because Task 6 Step 4 will demand the same honesty.)

**The classification gap was real and is now fixed.** `ssl.SSLError` is not
wrapped by httpx into `HTTPError`, so it missed `provider_unreachable` and fell
into the unclassified bucket the driver refuses to retry. Commit `0e695d8` maps
it beside `httpx.HTTPError` to `provider_unreachable` — transport-level,
payload-independent, retryable — with a regression test driving an
`ssl.SSLError` through the MockTransport. 1850 unit tests green.

### Attempt 3 — sealed (`b74abd04…`, head `0e695d8`)

Twelve invocations, all completed, no retries, one prompt identity
(`eecc5d4c0b98…`) across 12, `HEAD` unchanged. The attempt in between — which
completed 12/12 at `bed25c4` — was voided by the driver's own HEAD assertion:
the fix commit landed mid-sweep and the batch spanned two trees. Same lesson as
the W5 gate: **no commit may land while a sweep runs.**

| | |
|---|---|
| cases | 12 (6 groups × neg/pos) |
| wall clock | 23 min 46 s, ≈ 119 s per case |
| attempts | 37, all `succeeded` |
| request tokens | 62,730 total |

Final verdicts after the semantic verifier, against the group design
(negative → `insufficient_evidence`, positive → determinate):

| case | expected | final | note |
|---|---|---|---|
| 001-neg | insufficient | violating (verified) | false confirmation |
| 001-pos | compliant | compliant | ✓ |
| 002-neg | insufficient | insufficient | ✓ — the gate downgraded a determinate proposal |
| 002-pos | compliant | compliant | ✓ |
| 003-neg | insufficient | insufficient | ✓ |
| 003-pos | violating | insufficient | planner retrieved from the wrong document |
| 004-neg | insufficient | insufficient | ✓ |
| 004-pos | violating | violating | ✓ |
| 005-neg | insufficient | compliant (verified) | false confirmation |
| 005-pos | violating | insufficient | the gate downgraded a correct determinate verdict |
| 006-neg | insufficient | compliant (verified ×2) | false confirmation |
| 006-pos | violating | compliant + violating (both verified) | contradictory pair survived |

Two of six groups (002, 004) behaved exactly as designed. The rest are findings,
not accidents:

- **`001-neg` is a stable false confirmation** — the same `violating` in every
  run. The shown evidence settles the claim, so the negative side is decidable
  by construction. This is a group-construction defect, not a system defect.
- **`003-pos` is a stable retrieval miss** — the claim is an RFC 9110 rule and
  the planner keeps retrieving RFC 9112 clauses. Same family as `l2-dev-008`.
- **Verdicts drift across runs at temperature 0.** `002-neg` ran violating →
  insufficient → compliant across the three live runs; `005-neg` insufficient →
  compliant. Per-run raw counts must be reported separately; no run may be
  averaged into another.
- **`005-pos` is a false rejection by the semantic gate** — a correct
  determinate verdict was downgraded to insufficient. The gate's
  precision/recall on the adversarial set is itself a measured quantity now.
- **`006-pos` let a verified `compliant` and a verified `violating` survive
  side by side** — the multi-candidate aggregation admits contradiction.

None of these were tuned. Dev rehearsal exists to surface them; the locked run
will carry whatever the frozen configuration produces, and the W6 report must
state every one of the five bullets above beside its numbers.

### Extrapolation to locked (author's decision, not a commitment)

Dev 32 measured invocations ≈ 35 minutes wall (108 s + 9 min 36 s + 23 min 46 s)
and ≈ 116,448 request tokens across the three sealed batches. The locked 57 with
refusals is the same shape at 1.6× the count; the SSL classification fix removes
one whole class of batch abort.

## Open, and carried into Task 6 preflight

**Three frozen identities do not describe the freeze commit.** Recorded in the
W6 plan. `provider_sha256`, `scripts_sha256` and `sets_sha256` disagree with the
tree at `d2998ff`; `prompts_sha256`, `policy_sha256`, `config_sha256` and
`scoring_sha256` match. `provider_sha256` moved again with `0e695d8`. The
remedy — re-freeze before Task 6, or run and disclose — is the author's and is
not settled by this rehearsal.

**The adversarial set needs §8.4 adjudication before the locked run.** The five
findings above distinguish group-construction defects (001-neg) from system
defects (003-pos retrieval, 005-pos gate, 006-pos aggregation), and only a
human read against the frozen source can make that call official. They do not
block the locked run — they are what the report will have to say about it.
