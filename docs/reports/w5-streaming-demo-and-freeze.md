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

At the time of the original run, one Colima bind-mounted container from the
first project remained in `Created` after bounded start/removal attempts:
`specpilot-w5-task9-fixture-init-run-da415b0bf8ee`. It was isolated and not
used for evidence. Its later review status and the remaining exact cleanup are
recorded in the superseding section below. The original safe cleanup was:
`docker rm specpilot-w5-task9-fixture-init-run-da415b0bf8ee`, followed by
`docker compose -p specpilot-w5-task9 --profile demo down -v --remove-orphans`
when the daemon accepts the operation.

## Review round 1: authoritative packaged hard-gate replay

This section supersedes the earlier gate counts and packaged identifiers above.
The implementation tested from a clean worktree was commit
`1ec786ea7580c26e6f43ac6f0a183d95c6999a75`, tree
`74f1b5ac4f1c14407aee981a8910c74389d005a8`. The implementation commit is
separate from the evidence-only documentation commit containing this section;
the latter is intentionally reported by Git history rather than embedded as a
self-referential hash.

The two browser/service databases were recreated with these exact commands
before the single gate and removed with the matching exact `dropdb` commands
afterward:

```text
dropdb --if-exists --host=127.0.0.1 --port=55435 --username=chunxue specpilot_w5_task9_scratch
createdb --host=127.0.0.1 --port=55435 --username=chunxue specpilot_w5_task9_scratch
dropdb --if-exists --host=127.0.0.1 --port=55435 --username=chunxue specpilot_w5_task9_browser_scratch
createdb --host=127.0.0.1 --port=55435 --username=chunxue specpilot_w5_task9_browser_scratch
# run the gate below
dropdb --if-exists --host=127.0.0.1 --port=55435 --username=chunxue specpilot_w5_task9_scratch
dropdb --if-exists --host=127.0.0.1 --port=55435 --username=chunxue specpilot_w5_task9_browser_scratch
```

The authoritative invocation was one literal `make w5-check`; `PYTHONPATH`
was not supplied externally. Each Python submake fixed it internally to the
current worktree and `src` directory.

```text
env SPECPILOT_PYTHON=../../.venv/bin/python \
  SPECPILOT_TEST_DSN=postgresql://chunxue@127.0.0.1:55435/specpilot_w5_task9_scratch \
  SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6333 \
  SPECPILOT_BROWSER_DSN=postgresql://chunxue@127.0.0.1:55435/specpilot_w5_task9_browser_scratch \
  SPECPILOT_COMPOSE_ENV_FILE=fixtures/demo/w5-gate.env \
  SPECPILOT_W5_TIMEOUT_SECONDS=1800 \
  make w5-check
```

It exited zero in **113s (n=1)**. Its transcript SHA-256 is
`71df468dd9a9616c0185b32e5a273c2508dc9da2d0b38e138fd8aae4440e3d8b`.
Results were Ruff clean; mypy **114 source files**; unit **1681 passed in
6.99s**; CLI **202 passed in 2.72s**; frontend **151 passed** and production
build; full service tree **2221 passed, zero skipped, in 28.09s**; browser **5
passed in 6.4s**; three Compose render checks; wheel inspection; all five
images; initializer history/filesystem/CLI checks; and the packaged gate below.
The final wheel hash was
`0a9664fbf18b26eb13137b0049f2a276d4dc22184fb3a8c5ffa20c609281b909`.

The standard `image-check` is a normal five-target build, followed by hard
history, CLI, and filesystem checks proving that `real-init` and
`fixture-init` contain neither Node/npm lineage nor trace assets. A separate
`image-cold-check` retains the forced no-cache evidence operation and is not a
routine/CI dependency. The successful cold replay used commit `37fa866` and
produced ingestion `5206e18161a9`, real-init `ce2a2c82a267`, fixture-init
`e6d52c96f960`, MCP `5cd0c845ac67`, and API `1c520825d780`. Its whole gate
transcript was **402s (n=1)** with SHA-256
`8c74e5dbdff777e86d43a2fe1b43aa6db3c6fa378d6b04bcb82e9153c0f83e2b`;
the five image creation timestamps ran from 14:01:34 to 14:05:09 +08:00 and
the sole npm phase reported 48s. The final and cold-tested
`docker/api.Dockerfile` blob is identically
`a05f4af1a942ae5c38cba4b334c1bf082eb446cb`, so the later gate-only changes do
not weaken that cold-build provenance.

The final normal-build image IDs were API
`sha256:369d07bd65c14e0e9dd88b14bb616a0a6b386857fa0ac14740d0e189c4bb6305`,
MCP `sha256:eb7b367413c87cfe2f011edd11ccc74530f347a9cff57557b4dd9e1c2b0a98b2`,
fixture-init
`sha256:3a863f5aef59b9cbfdb1114ac322e4ef83401d3168dbaf010ed542b579b64f35`,
real-init
`sha256:75cee16d126066d9fcdb1db7860d2d96e245c6418beda11401eadf05b7c039c1`,
and ingestion
`sha256:fde2ed3ca0a735571800c14c74ccfd6dbc217cf559caf0f87a66179e95b5e10f`.

The packaged replay generated its own non-overridable project
`specpilot-w5-task9-packaged-66607-09f87972` and loopback API port `63858`.
It used Docker client 29.7.2/server 29.5.2, Compose 5.4.0, PostgreSQL 17.10,
and Qdrant 1.12.4. Compose image rendering/build took 11.02s; named artifact
volume initialization 0.19s; fresh PostgreSQL/Qdrant startup 3.73s; migrations
`001` through `016` 0.29s; and first fixture initialization 0.76s. The exact
fixture identities were source
`fcad9aee23a33e741c933c86a1546bc8f6c3a691d3714aa8eeb1f94432dba454`,
corpus `1e4b200e822cf29512efacf1be93f75cfc930a9c25209598fef936be29e8d7a1`,
ready `743baa5f6eafb2428de2dfe2fb76f9a16412f84b601c2c1c567d07bcd2140876`,
collection `specpilot_5d0ec446beb00e383b81547950159846`, six points,
and inventory
`05bbdf5e731aed2a7fa853df017c13d129a7d5d4e4178bf45ae19dbf60fab047`.

The 0.630s repeat initializer was routed through the image-contained audit
proxy. It made exactly five Qdrant reads and zero mutations: `GET /`, `GET
/collections/<collection>`, `POST /collections/<collection>/points/count`,
`POST /collections/<collection>/points/scroll`, and `GET
/collections/<collection>/snapshots`. IDs/inventory were identical and the
full six-point payload/vector snapshot remained
`eccc623aa774ffdc5adf6273d01c8ba21abae9d1a3d91ed928a0b7b3140a7281`.
The general ledger bootstrap replayed the same corpus ledger ID
`b268dd61-4507-4550-b0fe-4749b25d3cf2` twice in 0.31s each under fixture-only
policy `1bc7592fbf3653c878634dd80450a2622fc81687972d2d55e24c59893a7088d4`.
The real/default profile still selects `default-v1`; it never selects this
fixture policy.

Packaged MCP/API startup and health took 7.94s plus a 0.15s MCP health probe.
All HTTP/SSE sequences were contiguous, all required events were present, and
the private marker was absent:

| Scenario | Terminal | Events | HTTP/SSE time | Run ID |
|---|---:|---:|---:|---|
| `l1_answered` | answered | 18 | 0.311s | `b00f0cc1-3955-440b-881b-096eeae35116` |
| `l2_answered` | answered | 26 | 0.302s | `4731eec8-8453-48c0-976d-11ca293035c4` |
| `evidence_refused` | refused | 18 | 0.302s | `435fe64c-cee4-4a0e-b419-da76ac533d9b` |
| `verifier_recovered` | answered | 38 | 0.298s | `12a86129-2687-4cbd-98aa-a0fea0d59141` |

Finally, the gate removed exactly five containers, six volumes, two networks,
and four unique project image tags; each removal completed in 0.01-0.13s.
Post-run label/tag checks and the two exact database checks were empty. The
older project is separate: container `6d7aadeec65a` remains `Created` and
`c3672b036a7d` is `Exited`; the previously reported `da415b0bf8ee` and
`4f59968b18d5` objects are absent. They were not used by this replay. Safe,
exact later cleanup (without daemon restart) is:

```text
perl -e 'alarm shift; exec @ARGV' 30 docker rm --force 6d7aadeec65a
perl -e 'alarm shift; exec @ARGV' 30 docker rm --force c3672b036a7d
docker compose -p specpilot-w5-task9-packaged --profile demo down -v --remove-orphans
```

Committed fixture hashes remain dense points
`aa24b5ba26953584ee98160121b8bfe1ca94739dd428a213c19b6867cddaa633`,
source XML `b222d0d01b84d6c2041e871adc84d38dbcb85015b462a9aca858dc1cb34f3a4b`,
and fixture manifest
`9c25f8dd33539da9140d52837aaa1380e53d6583204dafffa86564d3e09246d2`.
No answer-quality metric was computed or inferred from these timings.

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
