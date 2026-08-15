# Handoff — 2026-08-16, at the evaluation freeze

Supersedes `2026-08-15-codex-handoff.md` for anything the two disagree on. As
AGENTS.md says: every number below was true when written, so re-run the command
rather than quoting the file.

## What changed since the last handoff

W5 closed, the evaluation run spec is frozen, the branch is published, and CI ran
for the first time in the project's history.

**The freeze.** Run spec
`a4b3f8ca1c34466b60bc407ded38c73d763e07fd3801047a1ea212eb5d5e7cc5`, confirmed by
`chunxue` over candidate `2b42ab42abd4935151bd6f00d25262c91eea151ae05682d309680d6047700b30`,
scoring route `judge_calibrated`. Its `code_sha256` is `sha256(commit + tree)` of
`d2998ff`, which carries tag `evaluation-freeze-2026-08-16`. **W6 checks out that
tag.** The digest names no ref by itself, which is why the tag exists.

**The gate at the freeze point.** `make w5-check` at `d2998ff`: ruff clean, mypy
over 133 source files, 1,824 unit, 225 CLI, 153 frontend, 2,388 full-service in
49.82s, 5 browser, `packaged_demo_gate=passed`. Transcript SHA-256
`119912307ba06aedd4566b1aefb998357be8c774cd4740005e892d65bbb9dde4`. `HEAD` did not
move during the run — an earlier attempt was voided because a commit landed
mid-run and the evidence spanned two trees.

**The branch is public.** `github.com/gsd-150/specpilot`, branch
`feat/w5-streaming-demo-freeze`, tag pushed. Before pushing, two tracked test
fixtures were found holding RFC clause prose verbatim, which §8.1 forbids and
which AGENTS.md notes is a licence condition under the IETF TLP. Both were
replaced. **That check is not in CI**, and it should be: it was done by grepping
tracked files for known clause sentences, by hand, once.

**CI, first run ever.** It found two defects a green local `make check` could
not. `test_identities.py` pointed at `artifacts/restricted/`, which is
gitignored, so five tests failed in every fresh checkout. The compose job never
set `SPECPILOT_READY_DIR_HOST`, a mount added in W5, so `compose config` died on
`:/run/specpilot/ready:ro` — the exact failure AGENTS.md warns about for local
runs. Both fixed in `8a5404b`.

## The L2 chain: seven wire-contract gaps

The shape AGENTS.md records — a value present in the code and absent from the
bytes that left — recurred four more times in this window, three of them while
repairing the previous one. Full account in
`docs/reports/2026-08-16-l2-chain-diagnosis.md`; the load-bearing summary:

4. The compliance model invented evidence ids matching no shown excerpt.
5. Repairing 4 imposed a quota ("evidence_ids must be exactly those shown
   identifiers") against a schema capping the list at four while cases are shown
   five or six, making the instruction unsatisfiable.
6. Repairing 5 left determinate candidates free to cite nothing.
7. Neither L2 instruction forbade reasoning from specification memory, a rule
   L1 has always carried.

**Instance 7 is only partly closed.** The prohibition changed the wording and not
the behaviour: on `l2-dev-003` the model now writes "the shown excerpts make the
proxy's obligations depend on…" and still names an obligation it was not shown.
It is the least detectable of the family — an `insufficient_evidence` candidate
must carry an empty evidence list, so the deterministic gate has nothing to
check, and the case still scores as a correct verdict.

Recommendation: do not repair this with a fourth prompt edit. Three rounds each
produced a differently-shaped recurrence, the prompt bytes are now bound by the
frozen spec's `prompts_sha256`, and detecting it mechanically needs a tool that
extracts the requirements a rationale invokes and compares them against the
disclosed set. That tool does not exist.

## Two accounts, kept apart

Conflating retrieval with judgement produced three wrong attributions during
this diagnosis, all from taking the model's own rationale as fact. Use the
ledger:

```bash
python -m specpilot.cli egress disclosures --ledger-dsn "postgresql:///specpilot_live" \
  --evaluation-root-id <root> --corpus-manifest <id> --corpus-manifest-dir <dir> \
  --xml <rfc9110.xml> --xml <rfc9112.xml>
```

It answers "was this clause actually disclosed" from what the enforcer recorded
leaving, independently of what the outcome artifact claims was shown. When the
two disagree, that disagreement is the finding.

On the canonical dev batch (single prompt identity `eecc5d4c0b98`): gold
disclosed 8/8, verdict matched 8/8, citations present in 6/8. `l2-dev-008` was
the single retrieval miss in every earlier batch and reached its gold only after
the planner document scope changed — not after any prompt edit.

## Open, and what each blocks

**The restricted store exists in two copies on one disk.** `artifacts/restricted/`
and `backups/specpilot-受限产物备份-冻结点d2998ff-2026-08-16/` (616 files,
`MANIFEST.sha256` verifies). Neither can go to the remote by design. The freeze
artifacts and the sixty annotated items are not reconstructible; the code is.
Blocks nothing today, loses W6's starting point if the machine does.

**The prose check is manual.** Nothing prevents the next clause sentence from
entering a tracked file. Blocks nothing until it happens, and the repository is
public now.

**Author decisions outstanding for W6.** No locked set has been executed —
L1 25, L2 12, L2-adv 10. The freeze binds the configuration; W6 is the first
permitted execution and must not read locked output to tune anything.

**`l2-dev-003`'s reasoning defect** is disclosed in
`docs/reports/2026-08-16-evaluation-freeze-disclosures.md` §7. Any statement of
L2 dev verdict accuracy has to carry it: 8/8 is a true verdict count and
overstates by one the cases whose reasoning stayed inside disclosed evidence.

## Recommended path

1. Watch CI on the current head; it is the only signal that is not this machine.
2. Add the clause-prose check to CI, so §8.1 is enforced rather than remembered.
3. Decide whether the restricted store leaves this disk, and where to.
4. Begin W6 from tag `evaluation-freeze-2026-08-16`, not from branch head.
