# W6 locked-run preflight checklist

One-shot boundary: 57 live invocations (L1 25 with refusals, L2 12, L2-adv 10
groups / 20). Everything on this list is cheap or free; the run itself is not.

## 0. The freeze decision, first

- [ ] Decide re-freeze vs run-and-disclose. Material:
      docs/reports/2026-08-16-w6-refreeze-decision-material.md. If re-freezing:
      regenerate identities at the commit that will run (models tuple and
      python-version string decided and recorded), freeze-candidate,
      freeze-confirm (author-owned). Nothing locked has executed, so the
      boundary is intact either way.
- [ ] The commit that runs is decided and named. No commits may land from here
      until the sweep finishes -- a mid-run commit voids the batch (already
      happened once in this rehearsal).

## 1. Machine and services

- [ ] colima start; docker start specpilot-qdrant-1; curl -s localhost:6333/readyz
- [ ] PostgreSQL specpilot_live reachable; migrations applied
- [ ] BGE-M3 weights present at data/cache/models/bge-m3 (hash-checked by the CLI)
- [ ] SPECPILOT_MAIN_API_KEY and SPECPILOT_JUDGE_API_KEY set, lengths checked
- [ ] pgrep -fl run_sweep.sh prints nothing

## 2. The only irreplaceable thing

- [ ] Restricted store backed up OFF this disk (artifacts/restricted/ and the
      d2998ff backup). The 60 annotated items and the freeze artifacts are not
      reconstructible; the code is.

## 3. The runs, in order

- [ ] L1 locked: bash scripts/run_sweep.sh --level l1 --split locked --expected 25 --include-unanswerable
- [ ] L2 locked: bash scripts/run_sweep.sh --level l2 --split locked --expected 12
- [ ] L2-adv locked: bash scripts/run_sweep.sh --level l2-adv --split locked --expected 10 --source-manifest <id>
      (the authorization is ledger bookkeeping, measured: the enforcer does not
      gate evidence documents; pick one id and record it)
- [ ] After each: driver printed the batch prompt identity and the unchanged HEAD

## 4. Seal before scoring

- [ ] Hash the artifact set; record git rev-parse HEAD unchanged; record wall
      clock and spend. No scoring until the artifact manifest exists -- the
      boundary between what the system produced and what we did with it is a
      recorded moment, not a recollection.

## 5. Downstream, already built

- [ ] Judge payloads: specpilot judge prepare --level l1/l2 --expected <answered count>
      (9253ffa; count assertion refuses a short batch)
- [ ] Author's judge-blind audit (section 8.4), then judge scoring
- [ ] Comparison A': specpilot comparison e-context (ee579a0; dev validation
      already measured 12/12 expansion, 0 identical)
- [ ] Comparison B: gate-only scorer over the persisted pre-verifier artifacts
      (711559d; the off arm is computed from sealed artifacts, no provider)

## 6. Report obligations the rehearsal already fixed in place

The W6 report must state: the same-family self-generated bias; the l2-dev-003
reasoning defect; the seven L2 wire-contract gaps with instance 7 partly open;
the adversarial findings (adjudication sheet:
docs/reports/2026-08-16-w6-adversarial-adjudication-sheet.md); which items
E-context never expanded; every operator re-run with its reason; and the SSL
transport classification shipped in 0e695d8, which removed one whole class of
batch abort.
