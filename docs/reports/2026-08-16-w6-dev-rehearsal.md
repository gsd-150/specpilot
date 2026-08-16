# W6 dev rehearsal — Task 3

Purpose: find the defects in the W6 sweep path while they are still free. The
locked run is one-shot and spends real budget; this one is neither.

**Status: partly complete.** Everything that does not spend budget has run and
is recorded below. The three live dev sweeps are prepared and belong to the
author — AGENTS.md reserves real provider calls, and the plan's own constraints
repeat it. The commands are at the end.

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

**3. All three levels run end to end under a stub provider.**

- `l1` — 10 answered wrote one artifact each; 2 refusals went to `refusals.log`
  and wrote no answer file, which is what keeps the judge scoring answered cases
  only.
- `l2` — 8 completed, one prompt identity across the batch.
- `l2-adv` — 6 groups produced 12 invocations, both halves adjacent and each
  line carrying its `group_id`.
- Transport retry — two `provider_unreachable` responses retried and the third
  attempt completed. The predecessor died here with `CODE: unbound variable`.
- `HEAD` compared before and after; a sweep spanning two trees fails.

## Prepared but not run: the three live dev sweeps

Author-owned. Run them in one session, from this worktree, with the key
exported. Expect roughly 35 provider round trips.

```bash
export SPECPILOT_MAIN_API_KEY='...'
```

```bash
bash scripts/run_sweep.sh --level l1 --split dev --expected 12
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
