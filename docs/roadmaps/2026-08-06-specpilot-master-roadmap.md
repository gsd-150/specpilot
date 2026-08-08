# SpecPilot Master Implementation Roadmap

**Canonical product plan:** `../../../SpecPilot_项目方案.md` (v5, 2026-08-06)

**Project root:** `/Users/chunxue/Documents/resume_project/specpilot`

**Delivery rule:** Each week is executed from its own detailed, test-first plan. A later week does not begin until the preceding week’s hard gate is evidenced or the product plan’s pre-registered fallback is selected.

## Current state — 2026-08-07

W0 and R0 are complete on `feat/w0-foundation`. All ten W0 tasks are done except
Task 8 Step 4's box, which the plan marks as the author's own and deliberately
leaves unchecked; Task 10 recorded its route decision.

**The recorded route decision is `C` — the corpus moves to IETF RFCs.** Both
chosen 3GPP sources are refused by the ingestion boundary with
`embedded_active_content`, and three further specifications measured the same
way, so the blocker is ingestion rather than compliance.

**R0 has since carried out that decision.** RFC 9110 and 9112 are frozen in
both renditions, both pass the same ingestion boundary that refused all five
DOCX distributions, and both hold default-deny `source-manifest/v2` records
alongside an author-written BCP 78 source-terms assessment. Sections and
cross-references now come out of the v3 XML as elements — 288 sections and
2,519 cross-references for RFC 9110, none dangling.

The archive and OOXML boundary is retained unchanged, limits included. It is
the evidence that produced route `C`, and deleting it would erase a
demonstrated capability. The 3GPP manifests and their source-terms assessment
stay as records of what was assessed.

W1 may now begin against the RFC corpus. No successor manifest exists, every
source manifest is default-deny, and no real provider has been called.

Verification as of this date: 376 tests pass with a local PostgreSQL DSN set
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

2. **R0 — RFC corpus foundation** *(inserted by route `C`; complete)*
   - `source-manifest/v2` for sources with no archive and no DOCX.
   - RFC XML verification boundary, refusing DTDs, entities, and external
     references in two independent layers.
   - Sections and cross-references extracted from v3 XML as elements.
   - RFC 9110 and 9112 frozen, plus a BCP 78 source-terms assessment.
   - Detailed plan: `../superpowers/plans/2026-08-07-r0-rfc-corpus-foundation.md`.

3. **W1 — Annotation workflow and embedding throughput**
   - R0 already parses both frozen sources through the safe boundary and
     produces sections and cross-references, so W1 starts from structure rather
     than from a parsing smoke.
   - Model clauses on top of extracted sections; decide what a citable unit is
     when the source numbers sections but the outbound caps count tokens.
   - Build independent-path L1/L2 annotation schemas and review logs.
   - Measure local embedding throughput without committing model weights.
     Measured: the whole 1712-clause corpus encodes in 40–80 s on this machine,
     and batch order matters where batch size does not —
     `../reports/w1-embedding-throughput.md`.
   - Detailed plan: `../superpowers/plans/2026-08-07-w1-annotation-and-embedding.md`.

4. **W2 — Frozen corpus and retrieval baseline**
   - One XML parser rather than two DOCX parsers: section/tree/table/reference
     QA and parent-child chunks over the v3 vocabulary.
   - Build versioned Qdrant dense data plus independent BM25 and RRF.
   - Run pooling-only baseline before locking the main evaluation splits.
   - Freeze a read-only `corpus_manifest` and verify its inventory root.

5. **W3 — MCP, L1 agent, API, and real-ledger integration**
   - Expose the five read-only capabilities through Streamable HTTP MCP.
   - Implement the typed Orchestrator/Evidence flow, budgets, traces, and L1 API.
   - Connect every real provider call to the W0 ledger/enforcer.
   - Freeze mutually exclusive L2-adv dev/test cases before Verifier tuning.

6. **W4 — L2, Verifier, and recovery package**
   - Add Compliance Agent, deterministic citation/manifest checks, semantic gate, and one directed recovery.
   - Add run ownership and the minimum checkpoint state.
   - Exercise only development sets and publish explicitly labelled dev evidence.

7. **W5 — Demo, trace UI, and evaluation freeze**
   - Complete four deterministic fixture scenarios, SSE, and the minimal React trace page.
   - Make fixture-init and real-init idempotent and manifest-scoped.
   - Dry-run the two core comparisons on dev, then freeze the final `run_spec`.

8. **W6 — Locked evaluation and release evidence**
   - First-run the locked L1/L2 and L2-adv test sets.
   - Run the two paired core comparisons three times without treating repeats as extra independent samples.
   - Seal run/report manifests, manual audit, cold-cache cost/latency, README, report, video, and resume evidence.

## Non-negotiable global constraints

- Real source text, full indexes, complete clauses, and quotations are never
  committed, whatever the corpus. The terms behind this rule changed with route
  `C` — 3GPP reserves rights by default while the IETF Trust pre-grants a public
  licence to reproduce unmodified portions with attribution — but the practice
  did not. The RFC source-terms assessment records as an open uncertainty
  whether sending an excerpt to a third-party API is one of the acts §3.c.iii
  licenses, and a rule is not relaxed on the strength of an unresolved question.
- CI/demo fixtures never emit quality metrics; all reported quality numbers come from the frozen real corpus.
- No provider route is callable outside `EgressPolicyEnforcer` plus the atomic ledger.
- Test data remains locked according to the product plan; W6 results never feed back into the frozen configuration.
- Dify and L3 remain post-release backlog.
