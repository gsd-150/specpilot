# Working on SpecPilot

Read this before planning anything. It is the operating manual, not the project
description — for what SpecPilot *is* and why each decision was made, the two
sources of truth below are authoritative and this file is not.

## What this is

Verifiable clause QA over frozen specification documents (RFC 9110 / 9112).
Every determinate answer carries a citation a reader can check, and the system
refuses when the evidence does not support one. The claim only means something
because the system can *fail* it, so most of the engineering is in the parts that
say no: the disclosure gate, the atomic ledger, the citation checker.

## Sources of truth, in order

1. **`SpecPilot_项目方案.md`** — the product plan (~1,170 lines, Chinese).
   Section numbers referenced everywhere else in the repo (§3.2, §8.1, §12.3)
   point here. Annotated with `[已变更]` / `[已完成]` / `[已实测]` blockquotes
   that supersede the prose beneath them.
2. **`docs/superpowers/plans/*.md`** — execution logs. Each records what was
   attempted, what broke, and why decisions that look arbitrary from the code
   are not. **`2026-08-09-assisted-annotation-and-review.md` is the live one**
   and carries the numbered open tasks.
3. **`docs/handoff/`** — dated state snapshots. Most recent first.

Code comments are unusually load-bearing here: where a decision has a reason,
the reason is in the docstring. Do not compress them away.

## Before you plan

- Read the live execution log's open tasks. Several look like bugs and are not.
- **Verify state, do not trust a snapshot.** Every number in `docs/handoff/` was
  true when written. Re-run the command, do not quote the file.

## Local stack

Anything touching retrieval or the answer path needs all of this up:

```bash
colima start
docker start specpilot-qdrant-1        # the container already exists; fastest path
```

First time, or after the container is removed — the `--env-file` is required
since W3, because compose validates *every* service even when you name one, and
the `mcp`/`api` bind mounts expand to `:/run/specpilot/corpus:ro` without it:

```bash
docker compose --env-file .env.example -f compose.yaml -f compose.index.yaml up --wait qdrant
```

- **Qdrant** `localhost:6333`, frozen collection
  `specpilot_ff4841e2d846388014efa06870fbbdb7`, 1,922 points. It runs under
  Colima, so it dies whenever the Colima VM stops — and the container stays
  stopped after a host reboot even once Colima is back, which reads as
  `DenseBackendUnavailable` / `Connection refused` from code that worked before.
- **Reinstall after pulling**: `.venv/bin/python -m pip install -e ".[dev]"`
  from the **main checkout**, never from a worktree. W3 added `mcp` and
  `uvicorn`; a stale venv fails as ~12 collection errors and a mypy
  `import-not-found`, both of which look like the branch is broken.
- **PostgreSQL** `specpilot_live` — the egress ledger. Schema is
  `migrations/*.sql`, applied in order.
- **BGE-M3 weights** at `data/cache/models/bge-m3` (2.1 GB) — *not* the
  HuggingFace cache, which is empty. Its hash must match the corpus manifest's
  `embedding_weights_sha256` or the CLI refuses.
- **API keys by environment only**: `SPECPILOT_MAIN_API_KEY`,
  `SPECPILOT_JUDGE_API_KEY`. Never in a config file, never in a commit.

## Commands

```bash
make check              # lint + typecheck + unit + CLI tests — the fast loop
make lint               # ruff
make typecheck          # mypy --strict over src
make unit               # tests/unit only
make cli                # tests/cli only; no service dependency
make integration-db     # needs SPECPILOT_TEST_DSN
make integration-qdrant # needs Qdrant up
make fixture-smoke      # needs SPECPILOT_TEST_DSN
```

**`make check` does not run the integration, Qdrant or smoke suites, and neither
does a bare `pytest`.** The suite reports three different results and all three
say "passed":

| command | result on 2026-08-11 |
| --- | --- |
| `pytest -q` | 1,246 passed, **42 skipped** |
| `+ SPECPILOT_TEST_DSN` | 1,271 passed, **17 skipped** |
| `+ SPECPILOT_TEST_QDRANT_URL` | **1,288 passed, 0 skipped** |

A stale assertion survived two commits inside that gap. Before believing a green
run, produce the third line — with Qdrant up, and on a **fresh** database so the
migrations are exercised rather than assumed:

```bash
createdb specpilot_scratch \
  && SPECPILOT_TEST_DSN=postgresql:///specpilot_scratch \
     SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 \
     .venv/bin/python -m pytest -q ; \
  dropdb specpilot_scratch
```

The fixture applies every file in `migrations/` in filename order. Reusing a
database you migrated by hand hides a missing migration — that is not
hypothetical, it happened on 2026-08-11.

One live question through the whole gate (see "What is the author's" below):

```bash
export SPECPILOT_MAIN_API_KEY='...' && bash tmp/ask.sh "your question"
```

## Invariants — breaking these is never a fix

- **Fail closed.** Any unreadable state, ambiguous commit, cap violation or
  missing authorization refuses. A refusal is a verdict, never an exception to
  route around. See `docs/adr/0001-fail-closed-boundaries.md`.
- **No source text in a committable record.** No clause prose, no full indexes,
  no quotations in anything git tracks. §8.1. This also happens to discharge an
  IETF TLP condition, so it is a licence rule now as well as a hygiene one.
- **`policy_hash` covers the policy's *field names*** — it is
  `_canonical_hash(model_dump())`. Renaming a cap field invalidates every corpus
  ledger row bound to it, and there is currently no path to rebind one (open
  Task 11). Do not rename policy fields casually.
- **The enforcer is the only path outward.** Nothing constructs a provider
  payload except through it.
- **An `evaluation_root_id` is one question**, not one session. §3.2 sizes the
  root as the online chain plus the judge sub-ledger for a single evaluation
  case: L1 10 = 5 + 5, L2 17 = 12 + 5.
- **The model cites an `evidence_id`** — the content hash of the exact bytes it
  was shown — and never a `clause_id`, which no payload prints. The payload
  label, `REPLY_INSTRUCTIONS`, the parser and `check_citation` are only correct
  together; changing one silently reproduces a fault that reads like the model
  disobeying.
- **`transmitted_*` and `request_*` are different quantities.** `transmitted` is
  corpus content counted with repetition — what the caps bind, computed at
  reserve time. `request_*` is the measured wire size of one attempt, which no
  cap reads. They were the same name once and two writers disagreed about which
  they were storing.

## Failures that look like code bugs and are not

- `dense_index_unavailable` from a system that worked an hour ago → Colima
  stopped. Check the stack before reading code.
- `No module named 'specpilot'` → a `pip install -e .` was run from inside a git
  worktree, rewriting the shared venv's editable path. Repair with
  `.venv/bin/python -m pip install -e . --no-deps --no-build-isolation`.
- `root_unique_excerpts_exceeded` → the accounting root is spent, because a root
  is one question. Read the ledger before reading the code:
  `psql -d specpilot_live -x -c "SELECT evaluation_root_id, jsonb_pretty(usage_snapshot) FROM egress_evaluation_root;"`
- `policy_snapshot_mismatch` → the caps changed under a corpus that already has
  recorded usage. Correct behaviour, and currently a dead end (Task 11).

## Testing, the way this project has been bitten

Every defect found by running the real path was invisible to a passing unit
suite, and they were all the same shape: **a value present in the code and
absent from the bytes that actually left.** An attribution line that was never
rendered. A reply contract that was written, exported, and referenced nowhere.
An identifier the model was asked to cite and never shown. A ledger column two
callers filled with two different quantities.

So:

- **Get the real path running end to end as early as the work allows**, even
  against a deliberately invalid key — that alone exercises rendering, ordering,
  the ledger and every guard except the provider itself.
- **When a double stands in for something real, match its shape exactly** —
  including whether an attribute is a property, and including fields it can leave
  at a default. Three separate doubles here were wrong: a `token_counter` method
  where the real one is a property, a canned reply citing an identifier no
  payload prints, and a fake provider that never set `request_bytes` and so
  reported zero for every offline send.
- **Prefer a test that crosses the join** over one more test per component. The
  regression guard for the citation contract renders the payload, scrapes the
  identifier out of the *rendered text*, and feeds it to the parser. Reading it
  off the payload object would test the objects again and miss the same gap.

## What is the author's, not an agent's

- **Live provider calls.** They spend a real budget against a real key and put
  RFC excerpts on the wire. Prepare the command; let the author run it.
- **Compliance decisions.** §3.2 authorization is a recorded human conclusion.
  The current one expires **2026-11-08** and names `chunxue` as author.
- **Any number that will be shown to someone.** The author's standing rule: no
  number goes on a résumé or into demo material until it has actually been
  measured, and it carries its `n` and its scope when it does.
- **Changes to a shipped output field or a cap value.** Recommend, explain the
  consequence, and let the author decide.

## Conventions

- Python 3.12–3.14, `.venv` in-repo, `make setup` to create it.
- ruff (`E,F,I,B,UP,SIM`, line length 88) and mypy `--strict` over `src`.
- Commit messages: `type: imperative sentence naming the substance`, then a body
  explaining *why* and what class of defect it belongs to. Look at
  `git log` — the body is where the reasoning lives.
- Docs default to Chinese when addressed to the author, English inside the repo
  (plans, docstrings, commit messages) to match what is already there.
