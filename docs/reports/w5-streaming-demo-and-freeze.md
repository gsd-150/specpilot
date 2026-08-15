# W5 streaming, packaged demo, and gate evidence

**Date:** 2026-08-15 (Asia/Shanghai)

**Verification base:** `1112a38`
**Scope:** fixture-only engineering evidence; no live provider, locked output,
author freeze, or quality evaluation.

## Outcome

The W5 engineering package is closed. The owner-scoped SSE endpoint and React
client resume by sequence, four closed fixture scenarios cross API, worker,
MCP, PostgreSQL ledger, fake provider, verifier, and SSE, and `make w5-check`
now makes every required service and artifact surface mandatory. Base and real
Compose configurations remain unpublished; only the explicit demo override
binds the API to host loopback.

Packaged verification exposed and closed two fail-closed gaps that in-process
fixtures had hidden:

1. `fixture-init` bound the corpus to a default-deny source, so packaged API
   assembly raised `ValueError: source route is not authorized`. It now creates
   a deterministic authorized successor for exactly
   `fixture-provider/fixture-smoke/online_main`, and binds corpus, parse-QA, and
   ready records to that successor.
2. the committed synthetic RFC 9999 had no per-document cap in the shipped
   default policy, so the first packaged SSE run ended
   `egress_blocked/corpus_document_cap_missing`. A committed
   `fixture-overlay-v1.json` prices only that synthetic document and is selected
   only by `profile=fixture`; `EgressPolicy.load()` and the real profile remain
   unchanged.

## Reproducible gates

The final service command used fresh, explicitly named databases and required
Qdrant:

```text
SPECPILOT_TEST_DSN=postgresql://chunxue@127.0.0.1:55435/specpilot_w5_task9_scratch \
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6333 \
SPECPILOT_BROWSER_DSN=postgresql://chunxue@127.0.0.1:55435/specpilot_w5_task9_browser_scratch \
SPECPILOT_COMPOSE_ENV_FILE="$PWD/tmp/w5-compose.env" \
make w5-check SPECPILOT_PYTHON=../../.venv/bin/python SPECPILOT_W5_TIMEOUT_SECONDS=600
```

The two exact scratch database names above were created fresh before this one
command and removed afterward. The browser launcher keeps them in a literal
allowlist and rejects prefixes, suffixes, arbitrary environment names, and
non-loopback hosts. Result: **2199 passed, 0 skipped in 28.11s** in the full
service tree and **5 browser tests**. Focused initializer regression: **18
passed in 2.84s**. Fast evidence was Ruff clean, mypy **114 source files**, unit
**1661 passed in 6.06s**, CLI **202 passed in 2.19s**, frontend **151 passed**,
and a successful Vite production build.

`make package-check` built and inspected the wheel. Final wheel SHA-256:
`5c0c055d9e269cd54445d2f901310ea47dff3c14a2ad2bfb29df846a99971842`.
The wheel contains the trace HTML/assets, `default-v1.json`, and the
fixture-only overlay.

Compose config was rendered for base demo, explicit demo override, and real
override. Both base and real rendered configurations contained no published
host port. CI supplies PostgreSQL 17 and Qdrant 1.12.4, creates a distinct fresh
browser database, provides no provider key, rejects any pytest skip, runs the
same `make w5-check`, and builds API, MCP, fixture-init, real-init, and ingestion
images.

## Packaged demo evidence

Environment: macOS/Colima arm64 Docker classic builder; PostgreSQL 17-alpine;
Qdrant 1.12.4; unique project/resources prefixed `specpilot-w5-task9b`; API
published only as `127.0.0.1:18001`. The source/corpus/ready identities were:

- source successor: `fcad9aee23a33e741c933c86a1546bc8f6c3a691d3714aa8eeb1f94432dba454`
- corpus: `98a1ae2f0956de119caaca88534e307362acabf394c7af35c6c1f653123094af`
- ready: `68fa51f962989acd739b37725d70364b3ddfefaeaa3043be94c7169eb13594ea`
- collection: `specpilot_5d0ec446beb00e383b81547950159846`, 6 points
- inventory: `05bbdf5e731aed2a7fa853df017c13d129a7d5d4e4178bf45ae19dbf60fab047`
- fixture policy: `1bc7592fbf3653c878634dd80450a2622fc81687972d2d55e24c59893a7088d4`

Fresh fixture-init was **0.73s (n=1)**. Exact-ID repeat initialization was
**0.63s, 0.58s, 0.58s (warm n=3)**. MCP health returned `status=ok`; API health
returned `status=ok, postgres=ok, mcp=ok`.

The final forced no-cache five-image build was **209.63s (n=1)**. Resulting
image IDs:

- API: `sha256:74ec509865d9c7a5916fa36558af4a67c1a46fb321c7152d0e81f11e31e03bde`
- MCP: `sha256:6611d5605a5ad0b064f747db2b3b4ab5bd1996f8dab48ac1dc59cb9d0ae3b930`
- fixture-init: `sha256:1ad42e0d0c367444f9e369d847ac72ba37069d2d18f815c696f19c9af236209b`
- real-init: `sha256:233bfa7691b25cecb600416de1b064d3c32d6869fffadf3eae4515e10d99522a`
- ingestion: `sha256:13cded9cb23d4e51c7cf400e654f7699d0d90bc81942f87b5d05c6f44022896d`

The final unified gate rebuilt the documentation-only package metadata after
the no-cache timing run. Its final-tree tags were API/fixture-init
`sha256:ade3cd3220fb8e52d6a03cb77c22678e300f59b0857efdd376a2188307124e2a`,
MCP `sha256:b6f2d5b9f6d230cb46299da906b956ea8ebcd11f74ee267a72067d736af37e1e`,
real-init
`sha256:e763d1ab22617934e3e47306f0c2d7ff2bbcc4e778314e22ee9e220c028ff34b`,
and ingestion
`sha256:568b23c9bc4d26e3bdc2135019a2261b5def18389973f47e8487a44a00fbe5d6`.

Real packaged HTTP/SSE results (all sequences contiguous, required event kinds
present, submitted private marker absent):

| Scenario | Terminal | Events | Run ID |
|---|---:|---:|---|
| `l1_answered` | answered | 18 | `6eb95d8a-50f9-4f04-8776-e91dda373b79` |
| `l2_answered` | answered | 26 | `959b81c5-c429-4a78-bcf1-6532da36aba4` |
| `evidence_refused` | refused | 18 | `2e077239-4f47-4402-9b4b-ed8c064d6bec` |
| `verifier_recovered` | answered | 38 | `c9095633-f15c-486f-8ef2-88f94d11f9a0` |

Committed fixture input hashes remain:

- dense points: `aa24b5ba26953584ee98160121b8bfe1ca94739dd428a213c19b6867cddaa633`
- source XML: `b222d0d01b84d6c2041e871adc84d38dbcb85015b462a9aca858dc1cb34f3a4b`
- fixture manifest: `9c25f8dd33539da9140d52837aaa1380e53d6583204dafffa86564d3e09246d2`

One Colima bind-mounted container from the first project remained in `Created`
after bounded start/removal attempts:
`specpilot-w5-task9-fixture-init-run-da415b0bf8ee`. It was isolated and not
used for evidence. Safe later cleanup, without restarting the daemon, is:
`docker rm specpilot-w5-task9-fixture-init-run-da415b0bf8ee`, followed by
`docker compose -p specpilot-w5-task9 --profile demo down -v --remove-orphans`
when the daemon accepts the operation.

## Boundaries that remain open

- No live-provider call or credential was used; live route acceptance remains
  open.
- No real corpus was initialized in this run. The real-init image is built and
  its recovery/fail-closed behavior is covered by integration tests.
- No author decision was simulated. Scoring route, prompts, thresholds, final
  corpus binding, and evaluation `run_spec` freeze remain author-owned work.
- No locked L1, L2, or L2-adv output was read. W6 is still their first allowed
  execution.
- No recall, precision, accuracy, F1, score, or other answer-quality metric is
  claimed from fixture results.
