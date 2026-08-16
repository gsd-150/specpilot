# W6 dev rehearsal — Task 3

Purpose: find the defects in the W6 sweep path while they are still free. The
locked run is one-shot and spends real budget; this one is neither.

**Status: L1 done, L2 and L2-adv pending.** Everything not spending budget has
run, and the author ran the live L1 dev sweep on 2026-08-16. The two remaining
sweeps are prepared and belong to the author — AGENTS.md reserves real provider
calls. The commands are at the end.

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

## Prepared but not run: the two remaining live dev sweeps

Author-owned. The script resolves its own tree, so it works from any directory —
no `cd`, no `PYTHONPATH`. L1 is done; these two remain.

```bash
export SPECPILOT_MAIN_API_KEY='...'
```

```bash
bash scripts/run_sweep.sh --level l2 --split dev --expected 8
```

The adversarial level needs an authorization named explicitly: a group spans
documents by construction — document attribution is one of the five distractor
dimensions — so no single authorized manifest follows from the record.

```bash
bash scripts/run_sweep.sh --level l2-adv --split dev --expected 6 --source-manifest <id>
```

## What to record while they run, and why

The locked run is one-shot. Going into it without an expected duration means
having no way to tell a hung sweep from a slow one.

- wall clock per case, per level;
- provider spend for the 35, which scales to an estimate for the 57;
- retry rate and the failure classes actually seen — the retry path has now been
  exercised against a stub but never against a real provider;
- whether one prompt identity held across each batch;
- `git rev-parse HEAD` unchanged, which the driver now asserts itself.

## Open, and carried into Task 6 preflight

**Which authorization covers an L2 run.** `l2 run` takes one
`--source-manifest`, and the dev driver chose it from the item's `document_id`.
The L2 chain retrieves by BM25 over the **pooled** corpus, so a case authorized
under RFC 9110 can rank an RFC 9112 clause. Whether the enforcer refuses that or
permits it is not established here, and it is not a question to discover during
the locked run. It applies to the L2 main set as much as to L2-adv.

**Three frozen identities do not describe the freeze commit.** Recorded in the
W6 plan. `provider_sha256`, `scripts_sha256` and `sets_sha256` disagree with the
tree at `d2998ff`; `prompts_sha256`, `policy_sha256`, `config_sha256` and
`scoring_sha256` match. The remedy — re-freeze before Task 6, or run and
disclose — is the author's and is not settled by this rehearsal.
