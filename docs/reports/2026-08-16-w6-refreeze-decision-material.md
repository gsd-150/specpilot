# W6 re-freeze decision material — 2026-08-16

Regenerated the twelve identity hashes at HEAD 9253ffa and compared them with
the frozen run spec a4b3f8ca (git d2998ff). Everything below is computed, not
quoted from an earlier snapshot.

## Field-by-field, fresh vs frozen

| field | fresh vs frozen | why |
|---|---|---|
| source_sha256 | match | both base source manifests unchanged |
| corpus_sha256 | match | derived corpus unchanged |
| collection_sha256 | match | vector inventory unchanged |
| config_sha256 | match | pyproject + policies unchanged |
| policy_sha256 | match | caps unchanged |
| prompts_sha256 | match | reply/contracts/judge prompt text unchanged |
| models_sha256 | match | see the anomaly below |
| sets_sha256 | **diff** | one annotation record written at 01:02, after the 00:55 status the freeze pinned (known) |
| scripts_sha256 | **diff** | known at d2998ff; unchanged since (evaluation/agents/runs untouched by today's commits) |
| provider_sha256 | **diff** | known at d2998ff (the seven L2 wire repairs); moved again with 0e695d8 (SSL classification) |
| scoring_sha256 | **diff** | **new today**: src/specpilot/judge/prepare.py added in 9253ffa |
| environment_sha256 | **diff** | the freeze-time python-version string is not reproducible; see below |

## Two anomalies the re-freeze should settle

**1. models_sha256 binds only the drafter.** The frozen value is the digest of
('claude-opus-5',) alone. The runtime models the run spec is about --
deepseek-v4-flash (main chain) and glm-5.2 (judge) -- are not in the binding.
Either the freeze-time input named the wrong model, or the intent was to bind
the drafter and the runtime models were supposed to ride along; either way the
spec's models field does not describe the run. Decide the tuple and record it.

**2. environment_sha256 — RESOLVED, the string is `3.12.11`.** ~~cannot be
rehashed~~. The earlier search covered `3.14`, `3.14.0`, `3.12` and `3.13` and
missed the micro version. A sweep of 336 candidates (majors 3.9–3.15, micros
0–21, with and without a `Python ` prefix) reproduces the frozen digest at
exactly one point:

```
environment_sha256 = _digest_of_values((sha256(requirements.lock), "python:3.12.11"))
```

So the field is fully reproducible, the lock is unchanged (its sha256 still
equals `dependency_sha256`), and this is **not** a reason to re-freeze. Record
`--python-version 3.12.11` at re-freeze so the next reader does not have to
brute-force it.

That leaves anomaly 1 as the only substantive freeze question.

## Recommended path

Re-freeze at the commit that will actually run Task 6, with the two inputs
above decided. The command shape (author-owned confirmation, not this report):

```bash
.venv/bin/python -m specpilot.cli evaluation identities \
  --out artifacts/restricted/evaluation/evaluation-identities.json \
  --repository . --dependency-lock requirements.lock \
  --corpus-manifest 1abafff7... --corpus-manifest-dir <main>/manifests/local/r0/corpus \
  --source-manifest af230fed... --source-manifest 3a752dd9... \
  --group-dir artifacts/restricted/l2-adv \
  --annotation-dir artifacts/restricted/annotations \
  --model-id <decided tuple> --python-version 3.12.11
```

The tuple is the one open input. Confirmed by search: the frozen value is the
digest of `('claude-opus-5',)` and nothing else. The run's actual models are
`deepseek-v4-flash` (main chain), `glm-5.2` (judge) and `bge-m3` (encoder,
whose weights are hashed separately); `claude-opus-5` is the drafter that
proposed 49 of the 61 gold sets and wrote much of this code.

Both readings are defensible and they answer different questions. Binding the
runtime models makes `models_sha256` describe *what produced the answers*, which
is what a run spec is for. Binding the drafter records *who wrote the test*,
which belongs in the bias disclosure rather than in the configuration identity.
They are not alternatives: naming all four is strictly more informative than
either, and the field is an unordered digest of strings, so it costs nothing.

Recommended: `--model-id deepseek-v4-flash --model-id glm-5.2 --model-id bge-m3
--model-id claude-opus-5`, with the roles written into the confirmation note —
because the digest preserves the names and loses the roles.

then freeze-candidate / freeze-confirm as the W5 runbook describes, at a clean
tree, with no sweep running. Nothing locked has executed, so no first-run
boundary is at stake; a freeze taken at the commit that actually runs is worth
more than one explained afterwards.
