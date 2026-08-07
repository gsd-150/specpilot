# SpecPilot Master Implementation Roadmap

**Canonical product plan:** `../../../SpecPilot_项目方案.md` (v5, 2026-08-06)

**Project root:** `/Users/chunxue/Documents/resume_project/specpilot`

**Delivery rule:** Each week is executed from its own detailed, test-first plan. A later week does not begin until the preceding week’s hard gate is evidenced or the product plan’s pre-registered fallback is selected.

## Current state — 2026-08-07

W0 is in progress on `feat/w0-foundation`. Tasks 1–9 are implemented and their
plan steps are checked; Task 8 Step 4 (the author's own compliance research and
assessment prose) is the open item, and Task 10 has not started.

No successor manifest exists, both source manifests remain default-deny, and no
real provider has been called.

**The recorded route decision is `C` — the corpus moves to IETF RFCs.** Both
chosen 3GPP sources are refused by the ingestion boundary with
`embedded_active_content`, and three further specifications measured the same
way, so the blocker is ingestion rather than compliance. Everything
provider-side carries over; the source side needs new manifests and a
source-terms assessment against BCP 78. W1 does not begin until an
RFC-specific design and plan exists.

Verification as of this date: 329 tests pass with a local PostgreSQL DSN set
(24 of them are skipped without one), Ruff and mypy are clean, and the envelope
and both fixture route smokes pass. A fixture route smoke proves the transport,
enforcer, and ledger are wired and policy-bound; it proves nothing about any
real provider, credential, or model, and its own output says so.

## Milestone sequence

1. **W0 — Safety, manifests, and egress enforcement**
   - Safe outer-ZIP handling and isolated OOXML inspection.
   - Immutable source manifests and compliance-decision successors.
   - Atomic PostgreSQL egress ledger and the only provider transport.
   - Fixture-only provider smoke tests and Compose/CI skeleton.
   - Go/no-go evidence for route A, B, or C.
   - Detailed plan: `../superpowers/plans/2026-08-06-w0-safety-egress-foundation.md`.

2. **W1 — Parsing smoke and annotation workflow**
   - Parse one frozen source through the safe boundary.
   - Build independent-path L1/L2 annotation schemas and review logs.
   - Measure local embedding throughput without committing model weights.

3. **W2 — Frozen corpus and retrieval baseline**
   - Complete both parsers, clause/tree/table/reference QA, parent-child chunks.
   - Build versioned Qdrant dense data plus independent BM25 and RRF.
   - Run pooling-only baseline before locking the main evaluation splits.
   - Freeze a read-only `corpus_manifest` and verify its inventory root.

4. **W3 — MCP, L1 agent, API, and real-ledger integration**
   - Expose the five read-only capabilities through Streamable HTTP MCP.
   - Implement the typed Orchestrator/Evidence flow, budgets, traces, and L1 API.
   - Connect every real provider call to the W0 ledger/enforcer.
   - Freeze mutually exclusive L2-adv dev/test cases before Verifier tuning.

5. **W4 — L2, Verifier, and recovery package**
   - Add Compliance Agent, deterministic citation/manifest checks, semantic gate, and one directed recovery.
   - Add run ownership and the minimum checkpoint state.
   - Exercise only development sets and publish explicitly labelled dev evidence.

6. **W5 — Demo, trace UI, and evaluation freeze**
   - Complete four deterministic fixture scenarios, SSE, and the minimal React trace page.
   - Make fixture-init and real-init idempotent and manifest-scoped.
   - Dry-run the two core comparisons on dev, then freeze the final `run_spec`.

7. **W6 — Locked evaluation and release evidence**
   - First-run the locked L1/L2 and L2-adv test sets.
   - Run the two paired core comparisons three times without treating repeats as extra independent samples.
   - Seal run/report manifests, manual audit, cold-cache cost/latency, README, report, video, and resume evidence.

## Non-negotiable global constraints

- Real 3GPP source text, full indexes, complete clauses, and quotations are never committed.
- CI/demo fixtures never emit quality metrics; all reported quality numbers come from the frozen real corpus.
- No provider route is callable outside `EgressPolicyEnforcer` plus the atomic ledger.
- Test data remains locked according to the product plan; W6 results never feed back into the frozen configuration.
- Dify and L3 remain post-release backlog.
