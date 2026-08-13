# W4 Compliance, Verifier, and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-resumable L2 compliance path whose determinate verdicts pass frozen-corpus deterministic checks and an independent semantic gate, with exactly one directed retrieval recovery and no durable question or excerpt prose.

**Architecture:** Extend the existing AnyIO worker, PostgreSQL run store, MCP Evidence Agent, and sole `PolicyBoundTransport` boundary. Keep Compliance output as an untrusted candidate, verify its evidence locally against `LocalCorpus`, send only verified candidates through a separately prompted semantic stage, and checkpoint only hashes, identifiers, budgets, reservations, reconstruction generations, and stage state. An interrupted owner resumes the same run by resubmitting the same question; locally reconstructible stages are not repeated, while lost model results are resent under a new reconstruction-generation key and charged to the unchanged ledger caps.

**Tech Stack:** Python 3.12–3.14, Pydantic v2, FastAPI, AnyIO/asyncio, psycopg 3/PostgreSQL, MCP Streamable HTTP, Qdrant-backed existing retrieval, pytest, ruff, mypy strict.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-08-14-w4-compliance-verifier-recovery-design.md`; when this plan and that file differ, stop and amend the plan before coding.
- Fail closed on unreadable state, ambiguous reservations, manifest/config/prompt drift, invalid stage transitions, exhausted budgets, or owner/query mismatch.
- The enforcer remains the only path outward; Compliance and semantic Verifier calls must use `PolicyBoundTransport` with `EgressStage.COMPLIANCE` and `EgressStage.VERIFIER`.
- Do not persist question, atomic-claim, rationale, tool-query, excerpt, provider-response, credential, path, or exception prose.
- Preserve existing L1 behavior and the meaning of `transmitted_*`, `request_*`, `policy_hash`, and one-question `evaluation_root_id`.
- L2 accepts at most three atomic claims, spends at most eight MCP attempts including recovery, and permits one run-scoped directed retrieval recovery.
- Process resume is client-assisted and may recur after later process loss; it never resets the checkpoint's tool usage, `recovery_attempted`, reservation IDs, or egress ledger.
- Provider operations use keys derived from `(run_id, stage, claim_id, recovery_attempt, reconstruction_generation)`. The generation changes only when a checkpoint proves a model result existed but its prohibited prose was lost; the resent transmission is fully charged to the same ledger caps.
- Python remains `>=3.12,<3.15`; add no LangGraph or other workflow dependency.
- Tests use real production joins and exact-shaped doubles. Every production change follows RED → observed expected failure → minimal GREEN → refactor.
- Never run a live provider call. The author alone runs calls that spend a real key or send RFC excerpts.
- No source clause prose, full index, or quotation may enter a committable record.
- Keep code comments that explain safety decisions; do not compress load-bearing rationale.

## File Structure

- `src/specpilot/contracts/verdict.py` — public/internal L2 candidate, verdict, semantic decision, and final result contracts. The existing `contracts/compliance.py` remains the W0 source/provider authorization assessment contract.
- `src/specpilot/agents/compliance.py` — render/send/parse the Compliance candidate stage.
- `src/specpilot/verifier/deterministic.py` — pure frozen-corpus evidence integrity and scope checks.
- `src/specpilot/verifier/semantic.py` — independent semantic support send/parse stage.
- `src/specpilot/verifier/recovery.py` — closed fault-to-one-tool recovery selection and execution.
- `src/specpilot/checkpoints/contracts.py` — prose-free checkpoint state and legal transition graph.
- `src/specpilot/checkpoints/postgres.py` — transactional checkpoint/resume persistence.
- `migrations/006_w4_checkpoint_resume.sql` — attempts, checkpoint, resume idempotency, and new closed trace event shapes.
- `src/specpilot/runtime/l2.py` — one L2 attempt, checkpoint boundaries, one recovery, and final projection.
- `src/specpilot/runtime/worker.py` — task-level dispatch while preserving the L1 path and lease behavior.
- `src/specpilot/api/contracts.py`, `dependencies.py`, `app.py`, `runtime.py` — L2 create plus owner/query-bound resume.
- `src/specpilot/providers/http.py`, `fake.py` — stage-specific prompts and deterministic fixture replies.
- Existing `runs/contracts.py` and `runs/postgres.py` — sanitized W4 events, attempt-aware leases, owner read projection.

---

### Task 1: Closed L2 contracts and stage-specific wire format

**Files:**
- Create: `src/specpilot/contracts/verdict.py`
- Modify: `src/specpilot/providers/http.py`
- Modify: `src/specpilot/providers/fake.py`
- Test: `tests/unit/contracts/test_verdict.py`
- Test: `tests/unit/providers/test_http_adapter.py`

**Interfaces:**
- Consumes: existing `Citation`, `EvidenceExcerpt`, `L2DesignPayload`, `L2AtomicClaimPayload`, `ProviderResponse`, and `PolicyBoundTransport` payload projection.
- Produces: `ComplianceVerdict`, `ComplianceCandidate`, `ComplianceBatch`, `IdentifiedCandidate`, `normalized_claim_id`, `SemanticReason`, `SemanticEvidenceDecision`, `SemanticDecision`, `ComplianceResult`; `COMPLIANCE_REPLY_INSTRUCTIONS`; `SEMANTIC_REPLY_INSTRUCTIONS`.

- [ ] **Step 1: Write contract REDs for the maximum, evidence invariant, and final publication invariant**

Create `tests/unit/contracts/test_verdict.py` with real Pydantic construction tests. The core cases must be:

```python
def candidate(verdict: str = "compliant", evidence_ids: tuple[str, ...] = ("a" * 64,)) -> dict[str, object]:
    return {
        "claim": "A sender always emits the field.",
        "proposed_verdict": verdict,
        "evidence_ids": evidence_ids,
        "rationale": "The candidate is not yet verified.",
    }


def test_compliance_batch_rejects_a_fourth_atomic_claim() -> None:
    with pytest.raises(ValidationError, match="at most 3"):
        ComplianceBatch(candidates=tuple(candidate() for _ in range(4)))


def test_determinate_candidate_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="determinate candidate requires evidence"):
        ComplianceCandidate.model_validate(candidate(evidence_ids=()))


def test_insufficient_candidate_cannot_claim_evidence() -> None:
    with pytest.raises(ValidationError, match="insufficient candidate has no evidence"):
        ComplianceCandidate.model_validate(candidate("insufficient_evidence"))


def test_determinate_result_requires_semantic_support_and_citations() -> None:
    with pytest.raises(ValidationError, match="determinate result requires verified support"):
        ComplianceResult(
            claim_id="a" * 64,
            verdict="violating",
            verification_status="semantic_failed",
            citations=(),
            reason_code="unsupported",
        )
```

Also assert: normalized claims yield stable SHA-256 IDs and duplicate normalized claims are rejected; Evidence IDs are unique, full SHA-256, and at most four per candidate; a batch has 1–3 items; semantic per-Evidence IDs are unique and at most four; `supports_verdict=False` requires a non-`supported` reason; final `insufficient_evidence` contains no citations.

- [ ] **Step 2: Run the contract RED and observe the missing module**

Run:

```bash
.venv/bin/python -m pytest tests/unit/contracts/test_verdict.py -q
```

Expected: collection fails with `ModuleNotFoundError: specpilot.contracts.verdict`.

- [ ] **Step 3: Implement the minimal closed contracts**

Create `src/specpilot/contracts/verdict.py` with these exact public shapes:

```python
class ComplianceVerdict(StrEnum):
    COMPLIANT = "compliant"
    VIOLATING = "violating"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    DETERMINISTIC_FAILED = "deterministic_failed"
    SEMANTIC_FAILED = "semantic_failed"
    INSUFFICIENT = "insufficient"


class SemanticReason(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONDITION_MISMATCH = "condition_mismatch"
    EXCEPTION_MISSING = "exception_missing"
    POLARITY_MISMATCH = "polarity_mismatch"


class ComplianceCandidate(_FrozenModel):
    claim: ShortText
    proposed_verdict: ComplianceVerdict
    evidence_ids: Annotated[tuple[Sha256, ...], Field(max_length=4)] = ()
    rationale: ShortText


class ComplianceBatch(_FrozenModel):
    candidates: Annotated[tuple[ComplianceCandidate, ...], Field(min_length=1, max_length=3)]


class SemanticEvidenceDecision(_FrozenModel):
    evidence_id: Sha256
    supports: bool


class SemanticDecision(_FrozenModel):
    supports_verdict: bool
    evidence: Annotated[tuple[SemanticEvidenceDecision, ...], Field(min_length=1, max_length=4)]
    reason: SemanticReason
    rationale: ShortText


class ComplianceResult(_FrozenModel):
    claim_id: Sha256
    verdict: ComplianceVerdict
    verification_status: VerificationStatus
    citations: tuple[Citation, ...] = ()
    reason_code: Identifier | None = None
```

Use `extra="forbid"`, frozen models, after-model validators, and explicit duplicate detection. `ComplianceCandidate` deliberately has no model-supplied claim ID. Add `normalized_claim_id(claim: str) -> str`, which strips the already validated claim, UTF-8 encodes it, and returns SHA-256; the Compliance parser constructs a separate local `IdentifiedCandidate(claim_id: Sha256, candidate: ComplianceCandidate)` from that value. Do not store a claim or rationale on `ComplianceResult` because this is the durable/public metadata shape.

- [ ] **Step 4: Add different response contracts to the two L2 payloads**

In `providers/http.py`, stop using `REPLY_INSTRUCTIONS` for every non-judge payload. Define:

```python
COMPLIANCE_REPLY_INSTRUCTIONS = json.dumps(
    {"instruction": "Split into one to three atomic candidates and return JSON only.",
     "response_schema": ComplianceBatch.model_json_schema()},
    ensure_ascii=False, separators=(",", ":"), sort_keys=True,
)
SEMANTIC_REPLY_INSTRUCTIONS = json.dumps(
    {"instruction": "Judge only whether the excerpts support the proposed verdict; return JSON only.",
     "response_schema": SemanticDecision.model_json_schema()},
    ensure_ascii=False, separators=(",", ":"), sort_keys=True,
)
```

Make `_system_prompt` return the Compliance instruction for `L2DesignPayload`, the semantic instruction for `L2AtomicClaimPayload`, and the existing answer instruction for `L1OnlinePayload`. Keep attribution and full Evidence IDs unchanged.

Update `FakeProvider._deterministic_content` so `L2DesignPayload` returns one `ComplianceBatch` candidate referencing the first excerpt and `L2AtomicClaimPayload` returns a `SemanticDecision` whose Evidence IDs exactly match the payload. Do not make fixture replies cite an identifier absent from rendered bytes.

- [ ] **Step 5: Prove the rendered-wire join**

Add provider tests that render each L2 payload, scrape every `Evidence <sha256>:` label from the rendered user message, feed those exact IDs into the corresponding fixture reply parser, and assert the parsed object. Add a negative test proving a fake reply that cites an undisclosed hash is rejected later by the deterministic verifier rather than silently rewritten.

Run:

```bash
.venv/bin/python -m pytest tests/unit/contracts/test_verdict.py tests/unit/providers/test_http_adapter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the L2 wire contracts**

```bash
git add src/specpilot/contracts/verdict.py src/specpilot/providers/http.py src/specpilot/providers/fake.py tests/unit/contracts/test_verdict.py tests/unit/providers/test_http_adapter.py
git commit -m "feat: define closed L2 candidate and support contracts" -m "Compliance output is an untrusted candidate, while semantic support is a separately prompted decision. Distinct schemas and rendered-wire tests keep either model stage from inheriting the L1 citation contract or citing handles it was never shown."
```

---

### Task 2: Frozen-corpus deterministic Verifier

**Files:**
- Create: `src/specpilot/verifier/__init__.py`
- Create: `src/specpilot/verifier/deterministic.py`
- Modify: `src/specpilot/answer/evidence.py`
- Modify: `src/specpilot/retrieval/local.py`
- Test: `tests/unit/verifier/test_deterministic.py`

**Interfaces:**
- Consumes: `ComplianceCandidate`, `Evidence`, `IndexUnit`, `LocalCorpus.get_clause`, run corpus manifest ID, and allowed document IDs.
- Produces: `DeterministicFault`, `DeterministicCheck`, `DeterministicResult`, and `verify_candidate(candidate, disclosed, corpus, *, corpus_manifest_id, allowed_document_ids)`.

- [ ] **Step 1: Write one RED per deterministic fault family against real `IndexUnit` objects**

Create a small fixture `IndexUnit` with exact text and build `Evidence` from it. Tests must mutate one boundary at a time and assert these stable faults:

```python
class DeterministicFault(StrEnum):
    NOT_DISCLOSED = "not_disclosed"
    AMBIGUOUS_EVIDENCE = "ambiguous_evidence"
    CORPUS_MANIFEST_MISMATCH = "corpus_manifest_mismatch"
    DOCUMENT_SCOPE_MISMATCH = "document_scope_mismatch"
    CLAUSE_NOT_FOUND = "clause_not_found"
    DOCUMENT_ID_MISMATCH = "document_id_mismatch"
    DOCUMENT_VERSION_MISMATCH = "document_version_mismatch"
    SECTION_MISMATCH = "section_mismatch"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    QUOTE_HASH_MISMATCH = "quote_hash_mismatch"
    SPAN_MISMATCH = "span_mismatch"
    NO_VERIFIED_EVIDENCE = "no_verified_evidence"
```

The happy-path assertion is:

```python
result = verify_candidate(
    candidate,
    (evidence,),
    corpus,
    corpus_manifest_id="c" * 64,
    allowed_document_ids=frozenset({"RFC9110"}),
)
assert result.passed
assert result.citations == (
    Citation(
        clause_id=unit.unit_id,
        corpus_manifest_id="c" * 64,
        document_id=unit.document_id,
        document_version=unit.document_version,
        section_number=unit.section_number,
        content_hash=hashlib.sha256(unit.text.encode()).hexdigest(),
    ),
)
```

The quote-hash and span tests must construct corrupted local-only `Evidence` through a test helper rather than weakening `EvidenceExcerpt` validation.

- [ ] **Step 2: Run the Verifier RED**

```bash
.venv/bin/python -m pytest tests/unit/verifier/test_deterministic.py -q
```

Expected: collection fails because `specpilot.verifier.deterministic` does not exist.

- [ ] **Step 3: Preserve enough local disclosure identity to verify exact bytes and spans**

Extend `DisclosedClause`/`Evidence` local-only data so the deterministic verifier receives `quote_hash` and `NormalizedExcerptSpan` in addition to the existing identities. Keep these fields out of any general JSON serializer. `build_evidence_from_unit` must calculate both hashes from `unit.text` and set the whole-unit span from `unit.ordinal` and the same token boundary used in `EvidenceExcerpt`.

Add `LocalCorpus.resolve(unit_id: str) -> IndexUnit | None`; it returns `None` on absence and never performs fuzzy or cross-document lookup. Keep `get_clause` behavior for existing callers.

- [ ] **Step 4: Implement the pure verifier**

Use these immutable outputs:

```python
@dataclass(frozen=True, slots=True)
class DeterministicCheck:
    evidence_id: str | None
    fault: DeterministicFault | None


@dataclass(frozen=True, slots=True)
class DeterministicResult:
    checks: tuple[DeterministicCheck, ...]
    citations: tuple[Citation, ...]

    @property
    def passed(self) -> bool:
        return bool(self.citations) and all(check.fault is None for check in self.checks)
```

Index disclosed Evidence by content-hash handle while detecting duplicates instead of overwriting them in a dict. For each candidate Evidence ID, run checks in the spec order. Recompute hashes from `IndexUnit.text`; compare clause, document, version, section, and span; require the document in `allowed_document_ids`. Accumulate bounded checks but return zero citations if any fault occurs, preserving all-or-nothing publication.

- [ ] **Step 5: Run focused and L1 regression tests**

```bash
.venv/bin/python -m pytest tests/unit/verifier/test_deterministic.py tests/unit/answer/test_verify.py tests/unit/answer/test_reply_and_evidence.py -q
```

Expected: PASS; existing L1 citation semantics remain unchanged.

- [ ] **Step 6: Commit deterministic verification**

```bash
git add src/specpilot/verifier src/specpilot/answer/evidence.py src/specpilot/retrieval/local.py tests/unit/verifier/test_deterministic.py
git commit -m "feat: verify L2 evidence against the frozen corpus" -m "A disclosed handle is not sufficient to publish a compliance verdict. Re-resolve the exact frozen unit and bind manifest, document, version, locator, bytes, span, and request scope before any semantic provider call."
```

---

### Task 3: Ledger-bound Compliance and semantic agents

**Files:**
- Create: `src/specpilot/agents/compliance.py`
- Create: `src/specpilot/verifier/semantic.py`
- Modify: `src/specpilot/contracts/egress.py`
- Modify: `src/specpilot/egress/enforcer.py`
- Test: `tests/unit/agents/test_compliance.py`
- Test: `tests/unit/verifier/test_semantic.py`
- Test: `tests/integration/agents/test_l2_ledger_flow.py`

**Interfaces:**
- Consumes: Task 1 contracts, Task 2 `DeterministicResult`, `PolicyBoundTransport`, source manifest, Evidence, and logical idempotency key.
- Produces: `ComplianceContext`, `ComplianceOutcome`, `ComplianceAgent.evaluate`; `SemanticContext`, `SemanticOutcome`, `SemanticVerifier.verify`.

- [ ] **Step 1: Write REDs proving stage, payload, parsing, and no-send boundaries**

For Compliance, assert the captured reservation request has `task_level=L2`, `stage=compliance`, `payload.kind=l2_design`, a maximum of 12 L2 excerpts, and the initial-generation idempotency key `f"{run_id}-compliance-initial-g0"`. Assert malformed JSON becomes `InvalidComplianceReply` containing only reservation/replay/request-size metadata.

For semantic verification, assert one call per determinate claim with `stage=verifier`, `payload.kind=l2_atomic_claim`, only Evidence IDs present in `DeterministicResult.citations`, and a key carrying the explicit initial/recovery label plus reconstruction generation. Assert the method raises before transport when deterministic verification failed.

Integration RED:

```python
assert compliance_reservation.stage == "compliance"
assert verifier_reservation.stage == "verifier"
assert compliance_reservation.evaluation_root_id == verifier_reservation.evaluation_root_id
assert compliance_reservation.run_id == verifier_reservation.run_id
assert len(fake_provider.calls) == 2
```

- [ ] **Step 2: Run REDs and observe missing agents**

```bash
.venv/bin/python -m pytest tests/unit/agents/test_compliance.py tests/unit/verifier/test_semantic.py -q
```

Expected: missing-module failures.

- [ ] **Step 3: Implement Compliance send/parse**

Define:

```python
@dataclass(frozen=True, slots=True)
class ComplianceContext:
    source_manifest: SourceManifest | RfcSourceManifest
    corpus_manifest_id: str
    evaluation_root_id: str
    run_id: str
    model_id: str
    idempotency_key: str
    reconstruction_generation: int


@dataclass(frozen=True, slots=True)
class ComplianceOutcome:
    batch: ComplianceBatch
    reservation_id: str
    replayed: bool
    request_size: RequestSize
```

`ComplianceAgent.evaluate(description, evidence, context)` builds `L2DesignPayload`, sends once per supplied reconstruction generation, parses `ComplianceBatch.model_validate_json`, computes each server-owned claim ID, and returns identified candidates. It must cap the batch's union of Evidence IDs at 12 and keep each candidate at four. An undisclosed ID stays in the candidate only long enough for Task 2 to emit `not_disclosed`; do not substitute a valid ID.

- [ ] **Step 4: Implement semantic send/parse**

Define matching `SemanticContext` and `SemanticOutcome`, including `reconstruction_generation`. `SemanticVerifier.verify(candidate, evidence, deterministic, context)` refuses locally unless `deterministic.passed`, builds one `L2AtomicClaimPayload`, sends once for that generation, parses `SemanticDecision`, and rejects a response whose Evidence-ID set differs from the payload's set. Keep its prompt and parser separate from Compliance.

Update enforcer tests only if the existing `L2DesignPayload`/`L2AtomicClaimPayload` stage mapping is incomplete. Do not rename policy fields or change cap values.

- [ ] **Step 5: Run unit and fresh-DB ledger joins**

```bash
.venv/bin/python -m pytest tests/unit/agents/test_compliance.py tests/unit/verifier/test_semantic.py tests/unit/egress -q
SPECPILOT_TEST_DSN=postgresql:///specpilot_w4_scratch .venv/bin/python -m pytest tests/integration/agents/test_l2_ledger_flow.py -q
```

Expected: PASS; the integration database has already been created explicitly for the execution session.

- [ ] **Step 6: Commit model stages**

```bash
git add src/specpilot/agents/compliance.py src/specpilot/verifier/semantic.py src/specpilot/contracts/egress.py src/specpilot/egress/enforcer.py tests/unit/agents/test_compliance.py tests/unit/verifier/test_semantic.py tests/integration/agents/test_l2_ledger_flow.py
git commit -m "feat: meter Compliance and semantic verification separately" -m "The Compliance model proposes candidates but cannot attest to its own output. Send the candidate and the independent semantic support judgement through distinct egress stages, schemas, reservations, and parsers under one evaluation root."
```

---

### Task 4: Eight-call L2 budget and one directed recovery

**Files:**
- Modify: `src/specpilot/agents/contracts.py`
- Modify: `src/specpilot/agents/planner.py`
- Modify: `src/specpilot/agents/evidence.py`
- Create: `src/specpilot/verifier/recovery.py`
- Test: `tests/unit/agents/test_tool_plan.py`
- Test: `tests/unit/agents/test_evidence_agent.py`
- Test: `tests/unit/verifier/test_recovery.py`

**Interfaces:**
- Consumes: deterministic/semantic closed fault codes, current Evidence, MCP client, `LocalCorpus`, allowed scope, and already used tool attempts.
- Produces: `validate_tool_plan(plan, max_call_cost)`, `EvidenceAgent.collect(..., attempt_budget, attempts_used)`, `RecoveryKind`, `RecoveryRequest`, `RecoveryOutcome`, `select_recovery`, `execute_recovery`.

- [ ] **Step 1: Write REDs separating L1 six calls from L2 eight calls**

Change tests to require:

```python
assert validate_tool_plan(six_call_plan, max_call_cost=6) == six_call_plan
with pytest.raises(ValueError, match="six calls"):
    validate_tool_plan(eight_call_plan, max_call_cost=6)
assert validate_tool_plan(eight_call_plan, max_call_cost=8) == eight_call_plan
```

Evidence tests must start with `attempts_used=6`, permit exactly two more L2 attempts, return `attempts_used=8`, and prove a timeout retry consumes an attempt. L1 callers continue passing six.

- [ ] **Step 2: Write recovery selection/execution REDs**

Assert the closed mapping:

```python
assert select_recovery((DeterministicFault.NOT_DISCLOSED,), source_clause_id=None).kind is RecoveryKind.SCOPED_SEARCH
assert select_recovery((DeterministicFault.CONTENT_HASH_MISMATCH,), source_clause_id=unit_id).kind is RecoveryKind.GET_CLAUSE
assert select_recovery((DeterministicFault.DOCUMENT_SCOPE_MISMATCH,), source_clause_id=unit_id).kind is RecoveryKind.SCOPED_SEARCH
assert select_recovery((SemanticReason.EXCEPTION_MISSING,), source_clause_id=unit_id).kind is RecoveryKind.EXPAND_REFERENCES
```

Also prove: `recovery_attempted=True` returns no action; no remaining attempts returns no action; recovery arguments always carry the run corpus manifest and allowed document IDs; an invented clause never reaches `get_clause`; the outcome increments attempts and never clears earlier Evidence or call summaries.

- [ ] **Step 3: Run budget and recovery REDs**

```bash
.venv/bin/python -m pytest tests/unit/agents/test_tool_plan.py tests/unit/agents/test_evidence_agent.py tests/unit/verifier/test_recovery.py -q
```

Expected: failures show the current six-call constants and missing recovery module.

- [ ] **Step 4: Parameterize planning and evidence budgets without weakening L1**

Remove `_MAX_ATTEMPTS = 6` as hidden global policy. Use:

```python
def validate_tool_plan(plan: ToolPlan | Mapping[str, object], *, max_call_cost: Literal[6, 8]) -> ToolPlan: ...

async def collect(
    self,
    plan: ToolPlan,
    corpus_manifest_id: str,
    *,
    attempt_budget: Literal[6, 8] = 6,
    attempts_used: int = 0,
) -> EvidenceResult: ...
```

Add `attempts_used: int` to `EvidenceResult`. `PlannerContext` gains `task_level: TaskLevel` and `reconstruction_generation: int`; it chooses an L1 six-call or L2 eight-call tool catalog/prompt value and a generation-aware idempotency key. Keep maximum four plan steps.

- [ ] **Step 5: Implement one-tool recovery**

`RecoveryRequest` contains only `kind`, `claim_id`, `reason_code`, optional verified `source_clause_id`, corpus manifest, allowed document IDs, and remaining attempts. The claim text is an ephemeral argument accepted separately by `execute_recovery` and is never part of `RecoveryRequest.model_dump` or trace metadata.

`execute_recovery` performs exactly one logical recovery action through the existing client, decodes it with the same production result validators as EvidenceAgent, builds Evidence only from `LocalCorpus`, and returns:

```python
@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    evidence: tuple[Evidence, ...]
    call: ToolCallSummary
    attempts_used: int
```

For scoped search, `search_clauses` returns metadata rather than text, so the one logical recovery action may contain a search followed by `get_clause`; both are charged as separate MCP attempts and both summaries are recorded while `recovery_attempted` remains one boolean. When a verified clause ID exists, direct `get_clause` or `expand_references` plus `get_clause` is preferred. The total remains capped at eight.

- [ ] **Step 6: Verify and commit recovery**

```bash
.venv/bin/python -m pytest tests/unit/agents/test_tool_plan.py tests/unit/agents/test_evidence_agent.py tests/unit/verifier/test_recovery.py -q
```

Expected: PASS.

```bash
git add src/specpilot/agents/contracts.py src/specpilot/agents/planner.py src/specpilot/agents/evidence.py src/specpilot/verifier/recovery.py tests/unit/agents/test_tool_plan.py tests/unit/agents/test_evidence_agent.py tests/unit/verifier/test_recovery.py
git commit -m "feat: bound one directed L2 retrieval recovery" -m "L2 receives eight total MCP attempts so a real evidence chain can recover once, while L1 remains capped at six. Recovery is selected from closed fault codes, consumes the existing budget, and cannot create a second recovery allowance."
```

---

### Task 5: Sanitized checkpoint and attempt persistence

**Files:**
- Create: `src/specpilot/checkpoints/__init__.py`
- Create: `src/specpilot/checkpoints/contracts.py`
- Create: `src/specpilot/checkpoints/postgres.py`
- Create: `migrations/006_w4_checkpoint_resume.sql`
- Modify: `src/specpilot/runs/contracts.py`
- Modify: `src/specpilot/runs/postgres.py`
- Modify: `tests/conftest.py` only if the filename-ordered migration fixture needs a new assertion, not a new hard-coded filename.
- Test: `tests/unit/checkpoints/test_contracts.py`
- Test: `tests/integration/checkpoints/test_postgres_store.py`
- Test: `tests/integration/runs/test_run_leases.py`

**Interfaces:**
- Consumes: `RunRecord`, `RunEvent`, UUID run ID, hashes/bindings, Evidence identities, reservation IDs, tool usage, and recovery state.
- Produces: `CheckpointStage`, `EvidenceCheckpointRef`, `RunCheckpoint`, `ResumeDisposition`, `PostgresCheckpointStore.write/read/begin_resume/compact/delete_expired`; attempt-aware `PostgresRunStore.resume_claim`.

- [ ] **Step 1: Write checkpoint contract REDs including a sentinel prose scan**

Create legal checkpoints for every stage and assert the transition graph:

```python
LEGAL_TRANSITIONS = {
    CheckpointStage.PLANNED: {CheckpointStage.EVIDENCE_COLLECTED},
    CheckpointStage.EVIDENCE_COLLECTED: {CheckpointStage.CANDIDATE_BUILT},
    CheckpointStage.CANDIDATE_BUILT: {CheckpointStage.DETERMINISTIC_VERIFIED, CheckpointStage.RECOVERY_COMPLETED},
    CheckpointStage.DETERMINISTIC_VERIFIED: {CheckpointStage.SEMANTIC_VERIFIED, CheckpointStage.RECOVERY_COMPLETED},
    CheckpointStage.RECOVERY_COMPLETED: {CheckpointStage.DETERMINISTIC_VERIFIED},
    CheckpointStage.SEMANTIC_VERIFIED: {CheckpointStage.COMPLETED},
    CheckpointStage.COMPLETED: set(),
}
```

Use a sentinel `PRIVATE-QUESTION-SENTINEL` and recursively inspect `model_dump(mode="json")`; assert it contains none of question, claim, rationale, query, excerpt, provider response, path, or exception fields. Assert `recovery_completed` requires `recovery_attempted=True`, cannot occur twice, attempts used is `0..8`, and completed claim IDs are opaque identifiers.

- [ ] **Step 2: Write fresh-DB concurrency and integrity REDs**

Integration tests must prove:

- compare-and-set checkpoint version: two writers starting from version 2 yield exactly one version 3;
- checkpoint and `checkpoint_summary` event commit in one transaction;
- invalid raw JSON/extra keys fail database constraints;
- same resume idempotency key returns the same attempt;
- two different concurrent keys yield one `ACQUIRED` and one `LEASED`;
- owner, query hash, binding, non-interrupted state, or missing checkpoint mismatch returns a closed disposition without mutating the row;
- `compact` removes stage detail after completion but preserves sanitized run/result metadata;
- `delete_expired(before)` deletes only nonterminal checkpoint state last accessed before the bound.

- [ ] **Step 3: Run contract REDs before writing SQL**

```bash
.venv/bin/python -m pytest tests/unit/checkpoints/test_contracts.py -q
```

Expected: missing checkpoint package.

- [ ] **Step 4: Implement checkpoint contracts**

Use a frozen `RunCheckpoint` with exact fields:

```python
class RunCheckpoint(_FrozenModel):
    schema_version: Literal["run-checkpoint/v1"] = "run-checkpoint/v1"
    run_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    checkpoint_version: Annotated[int, Field(ge=1)]
    stage: CheckpointStage
    task_level: Literal["L2"]
    query_hash: Sha256
    evaluation_root_id: TraceIdentifier
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256
    policy_hash: Sha256
    configuration_hash: Sha256
    compliance_prompt_hash: Sha256
    verifier_prompt_hash: Sha256
    provider_id: TraceIdentifier
    model_id: TraceIdentifier
    plan_id: TraceIdentifier | None
    plan_hash: Sha256 | None
    evidence: Annotated[tuple[EvidenceCheckpointRef, ...], Field(max_length=12)]
    tool_attempts_used: Annotated[int, Field(ge=0, le=8)]
    reservation_ids: Annotated[tuple[UUID, ...], Field(max_length=16)]
    reconstruction_generations: Annotated[tuple[StageGeneration, ...], Field(max_length=8)]
    recovery_attempted: bool
    recovery_reason: TerminalReason | None
    completed_claim_ids: Annotated[tuple[Sha256, ...], Field(max_length=3)]
    completed_results: Annotated[tuple[ComplianceResult, ...], Field(max_length=3)]
    last_accessed_at: datetime
```

`EvidenceCheckpointRef` contains evidence/content/quote hashes, clause ID, document ID/version, section number, and span coordinates only. `StageGeneration` contains logical stage (including planning), optional opaque claim ID, recovery flag, and a nonnegative generation. `completed_results` contains only Task 1's prose-free `ComplianceResult`; its IDs must equal `completed_claim_ids` in the same order. Store a `plan_hash`, not the typed plan, because tool arguments contain query prose.

- [ ] **Step 5: Write migration 006 with strict ownership and CAS support**

The migration creates:

```sql
CREATE TABLE specpilot_run_attempt (
    run_id uuid NOT NULL REFERENCES specpilot_run(run_id),
    attempt integer NOT NULL CHECK (attempt >= 1),
    resume_key_hash char(64),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    end_reason varchar(64),
    PRIMARY KEY (run_id, attempt),
    UNIQUE (run_id, resume_key_hash)
);

CREATE TABLE specpilot_run_checkpoint (
    run_id uuid PRIMARY KEY REFERENCES specpilot_run(run_id),
    checkpoint_version integer NOT NULL CHECK (checkpoint_version >= 1),
    stage varchar(32) NOT NULL CHECK (stage IN (
        'planned','evidence_collected','candidate_built',
        'deterministic_verified','recovery_completed',
        'semantic_verified','completed'
    )),
    payload jsonb NOT NULL,
    last_accessed_at timestamptz NOT NULL
);
```

In the same migration replace migration 005's `CHECK (task_level = 'L1')` with `CHECK (task_level IN ('L1', 'L2'))`; add a migration test proving another value is rejected. Add immutable SQL validators following migration 005's exact-object pattern. Permit only the checkpoint keys above and recursively exact-check every Evidence reference. Extend the event-kind constraint with `checkpoint_summary`, `compliance_summary`, `semantic_summary`, `recovery_summary`, and `resume_summary`; define a strict Pydantic and SQL shape for each.

Do not rewrite existing runs. Seed attempt 1 only when the first W4 checkpoint is written, so pre-W4 interrupted rows remain non-resumable.

- [ ] **Step 6: Implement transactional store and attempt-aware resume claim**

`PostgresCheckpointStore.write(previous_version, checkpoint, event)` locks the run row, validates bindings against `specpilot_run`, checks the current checkpoint version, inserts/updates the checkpoint and event atomically, and returns the allocated checkpoint/event. `begin_resume` hashes the raw resume key before storage, checks ownership/query/bindings/checkpoint/expired lease in one transaction, creates or replays the attempt, and acquires a new lease only for one winner.

Return a closed enum rather than exceptions for ordinary resume denials:

```python
class ResumeDisposition(StrEnum):
    ACQUIRED = "acquired"
    REPLAY = "replay"
    NOT_FOUND = "not_found"
    NOT_OWNER = "not_owner"
    NOT_INTERRUPTED = "not_interrupted"
    QUERY_MISMATCH = "query_mismatch"
    BINDING_MISMATCH = "binding_mismatch"
    CHECKPOINT_MISSING = "checkpoint_missing"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    LEASED = "leased"
```

Database unavailability/integrity remains an exception and becomes API 503; it must never be mistaken for a domain denial.

- [ ] **Step 7: Run fresh migration and store tests**

```bash
SPECPILOT_TEST_DSN=postgresql:///specpilot_w4_scratch .venv/bin/python -m pytest tests/integration/checkpoints/test_postgres_store.py tests/integration/runs/test_run_leases.py -q
```

Expected: PASS on a database created after migration 006 exists.

- [ ] **Step 8: Commit persistence**

```bash
git add src/specpilot/checkpoints migrations/006_w4_checkpoint_resume.sql src/specpilot/runs/contracts.py src/specpilot/runs/postgres.py tests/conftest.py tests/unit/checkpoints/test_contracts.py tests/integration/checkpoints/test_postgres_store.py tests/integration/runs/test_run_leases.py
git commit -m "feat: persist sanitized W4 checkpoints and attempts" -m "Resume needs durable stage and budget state but not durable question or excerpt prose. A strict checkpoint envelope, atomic stage event, hashed resume key, and compare-and-set attempt lease make process recovery auditable without creating a second budget."
```

---

### Task 6: L2 attempt orchestration and full-gate recovery loop

**Files:**
- Create: `src/specpilot/runtime/l2.py`
- Modify: `src/specpilot/runtime/worker.py`
- Modify: `src/specpilot/runs/contracts.py`
- Modify: `src/specpilot/runs/outcomes.py`
- Test: `tests/unit/runtime/test_l2.py`
- Test: `tests/unit/runtime/test_worker.py`

**Interfaces:**
- Consumes: Planner/Evidence/Compliance/deterministic/semantic/recovery/checkpoint interfaces from Tasks 1–5.
- Produces: `L2RunContext`, `L2Outcome`, `run_l2_attempt`; task-level worker dispatch and sanitized L2 trace projection.

- [ ] **Step 1: Write orchestration REDs that cross every gate**

Use exact-shaped fakes but real contracts. Required tests:

1. three candidates pass both gates and produce three determinate results;
2. proposed insufficient skips semantic stage and remains insufficient;
3. deterministic failure calls recovery once, rebuilds Evidence, reruns the entire deterministic verifier, then runs semantic;
4. semantic failure calls recovery once, reruns deterministic then semantic;
5. failure after recovery becomes insufficient and never calls recovery twice;
6. total `attempts_used` never exceeds eight;
7. policy denial becomes `egress_blocked`, provider failure becomes `failed`, malformed Compliance reply becomes `refused/invalid_compliance_reply`;
8. checkpoint writes occur after `planned`, `evidence_collected`, `candidate_built`, `deterministic_verified`, optional `recovery_completed`, `semantic_verified`, and `completed` in legal order;
9. loss of lease after any awaited operation stops before the next outward operation.

The recovery-success assertion must include:

```python
assert deterministic.calls == 2
assert semantic.calls == 1
assert recovery.calls == 1
assert outcome.recovery_attempted
assert outcome.results[0].verification_status is VerificationStatus.VERIFIED
```

- [ ] **Step 2: Run L2 REDs**

```bash
.venv/bin/python -m pytest tests/unit/runtime/test_l2.py -q
```

Expected: missing `runtime.l2`.

- [ ] **Step 3: Implement one-attempt L2 state machine**

`run_l2_attempt(context)` is an async function/class with explicit injected protocols and no database/provider construction. It must:

- reconstruct Evidence and completed results locally from checkpoint when possible; from `planned`, resend planning at the next reconstruction generation; from `evidence_collected`, send Compliance; from `candidate_built`, `deterministic_verified`, or `recovery_completed`, resend Compliance at the next generation and require completed claim IDs to match; from `semantic_verified`, complete from durable prose-free results; otherwise plan as L2 generation zero;
- write a checkpoint only after the corresponding result is locally validated;
- select up to 12 Evidence for the L2 run without changing L1's five, while each candidate/semantic call carries at most four;
- run Compliance once for the initial evidence set;
- process candidates in order, sharing one mutable-in-function recovery flag and attempt count;
- convert proposed insufficient directly to an insufficient result;
- run deterministic checks before semantic;
- recover at most once across the batch, then rebuild the affected candidate Evidence set and rerun all checks;
- produce a `L2Outcome(results, reservation_ids, tool_attempts_used, recovery_attempted, provider_error, parse_fault)` with no claim/rationale fields.

Generation-aware idempotency keys come from a helper tested directly:

```python
logical_stage_key(run_id, "compliance", None, False, 0) == f"{run_id}-compliance-initial-g0"
claim_id = "a" * 64
logical_stage_key(run_id, "verifier", claim_id, True, 1) == f"{run_id}-verifier-{claim_id}-recovery-g1"
```

- [ ] **Step 4: Dispatch L1 and L2 without merging their invariants**

Add `task_level` to `RunJob`. Keep the existing L1 body in a focused `_run_l1` method and call the injected L2 runner only for L2. Extend `AgentName` with `COMPLIANCE` and retain `VERIFIER`. Add sanitized summary events containing counts, opaque IDs, pass/fault codes, recovery kind, and remaining budget only.

Do not store terminal claim prose in `RunView`; only `ComplianceResult` metadata is eligible.

- [ ] **Step 5: Run runtime regression tests**

```bash
.venv/bin/python -m pytest tests/unit/runtime/test_l2.py tests/unit/runtime/test_worker.py tests/unit/runs -q
```

Expected: PASS for L1 and L2.

- [ ] **Step 6: Commit orchestration**

```bash
git add src/specpilot/runtime/l2.py src/specpilot/runtime/worker.py src/specpilot/runs/contracts.py src/specpilot/runs/outcomes.py tests/unit/runtime/test_l2.py tests/unit/runtime/test_worker.py
git commit -m "feat: run L2 candidates through both verifier layers" -m "The worker now treats Compliance output as a candidate, spends one shared recovery allowance, and reruns the complete deterministic and semantic gates after recovery. L1 remains a separate path with its existing limits and terminal semantics."
```

---

### Task 7: Owner/query-bound client resume API

**Files:**
- Modify: `src/specpilot/api/contracts.py`
- Modify: `src/specpilot/api/dependencies.py`
- Modify: `src/specpilot/api/app.py`
- Modify: `src/specpilot/api/runtime.py`
- Modify: `src/specpilot/runs/contracts.py`
- Test: `tests/unit/api/test_app.py`
- Test: `tests/unit/api/test_api_runtime.py`
- Test: `tests/integration/api/test_l2_resume.py`

**Interfaces:**
- Consumes: checkpoint store `begin_resume/read`, attempt-aware job factory, owner session, normalized question hash, and worker delivery permit.
- Produces: `ChatRequest.task_level: Literal["L1", "L2"]`, `ResumeRequest`, `ResumeAccepted`, `POST /runs/{run_id}/resume`.

- [ ] **Step 1: Write API REDs for L2 create and every pre-delivery resume denial**

Add request contracts:

```python
class ResumeRequest(_ClosedModel):
    question: Question
    resume_key: ApiIdentifier


class ResumeAccepted(_ClosedModel):
    run_id: UUID
    attempt: Annotated[int, Field(ge=2)]
    status: Literal["queued"] = "queued"
```

Tests must assert:

- `POST /chat` accepts L2 and persists `task_level=L2`;
- L1 remains the default/accepted compatibility shape explicitly chosen by the existing client;
- wrong owner returns the same 404 as an unknown run, preventing enumeration;
- wrong question, non-interrupted run, missing/corrupt checkpoint, and binding drift return stable 409/422 codes and call neither `worker.reserve` nor MCP/provider;
- same resume key returns the same attempt without delivering twice;
- a different concurrent key receives `409 run_already_leased`;
- store failure returns 503 with no raw database detail;
- queue reservation is acquired only after all resume validation, and cancelled if delivery fails.

- [ ] **Step 2: Run API REDs**

```bash
.venv/bin/python -m pytest tests/unit/api/test_app.py tests/unit/api/test_api_runtime.py -q
```

Expected: validation/route failures because L2 and resume contracts are absent.

- [ ] **Step 3: Extend runtime bindings without passing raw transports through requests**

Change `JobFactory` to:

```python
JobFactory = Callable[[UUID, str, ChatRequest, RunCheckpoint | None], RunJob]
```

Add `checkpoint_store: ApiCheckpointStore` to `ApiRuntime`. The production `build_job` chooses L1/L2 planner context from `request.task_level`, sets the same evaluation root from a new run or checkpoint, and injects the already constructed Compliance/Semantic/deterministic/recovery services. Request data cannot replace the transport.

Persist the `evaluation_root_id` in the initial planned checkpoint; on resume ignore any client-supplied root because `ResumeRequest` has no such field.

- [ ] **Step 4: Implement resume route in validation-before-capacity order**

The route algorithm is:

```python
query_hash = hashlib.sha256(request.question.encode("utf-8")).hexdigest()
decision = await runtime.checkpoint_store.begin_resume(
    run_id=run_id,
    session_id=session.session_id,
    query_hash=query_hash,
    resume_key=request.resume_key,
    binding=runtime.binding.checkpoint_binding,
)
```

Map `NOT_FOUND` and `NOT_OWNER` to 404, query/binding/checkpoint/non-interrupted denials to closed 409/422 responses, `LEASED` to 409, and `REPLAY` to the existing `ResumeAccepted`. Only `ACQUIRED` reserves worker capacity and delivers a job reconstructed from the returned checkpoint. On delivery failure, release the attempt lease with a sanitized interruption event; do not delete checkpoint or ledger rows.

- [ ] **Step 5: Cross the real API/store/worker process-loss join**

In `tests/integration/api/test_l2_resume.py`, run an L2 fixture worker until a test hook stops it immediately after each nonterminal checkpoint. Expire/reconcile the lease, restart the runtime, resubmit the same question and session, and assert the same run ID/root, monotonically increasing attempt, stable reservation keys, inherited tool count/recovery flag, and terminal result. Use a sentinel question/excerpt and query PostgreSQL JSON/text columns to prove it is absent.

- [ ] **Step 6: Run focused API integration tests**

```bash
SPECPILOT_TEST_DSN=postgresql:///specpilot_w4_scratch SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 .venv/bin/python -m pytest tests/integration/api/test_l2_resume.py tests/integration/api/test_l1_end_to_end.py tests/integration/api/test_run_ownership.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit resume API**

```bash
git add src/specpilot/api/contracts.py src/specpilot/api/dependencies.py src/specpilot/api/app.py src/specpilot/api/runtime.py src/specpilot/runs/contracts.py tests/unit/api/test_app.py tests/unit/api/test_api_runtime.py tests/integration/api/test_l2_resume.py
git commit -m "feat: resume interrupted runs from owner-supplied questions" -m "The server still stores only a query hash. An owning client may resubmit the same question after process loss; transactional checkpoint, binding, ledger, idempotency, and lease checks all pass before a job is delivered or any outward call can occur."
```

---

### Task 8: Complete fixture path, fresh-service verification, and measured W4 status

**Files:**
- Modify: `tests/smoke/test_fixture_pipeline.py`
- Create: `tests/integration/api/test_l2_end_to_end.py`
- Modify: `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`
- Create: `docs/reports/w4-compliance-verifier-recovery.md`
- Modify: `SpecPilot_项目方案.md` only to add a dated `[已完成]/[已实测]` annotation supported by the final commands; do not rewrite the source plan.

**Interfaces:**
- Consumes: complete W4 path and all verification targets.
- Produces: deterministic offline scenarios, zero-skip service evidence, and an exact measured status report without quality claims.

- [ ] **Step 1: Write the four fixture E2E REDs before any report text**

The new integration file must drive `POST /chat`, owner-scoped reads, and (for the process-loss case) `POST /runs/{run_id}/resume` through:

1. L2 happy path with one verified determinate claim;
2. deterministic mismatch blocked before semantic send, recovery succeeds, then full gate passes;
3. semantic distractor rejected, one recovery occurs, second semantic failure yields `insufficient_evidence`;
4. checkpoint resume continues the same root/budgets; a locally reconstructible stage is not repeated, while a lost model result is resent under the next generation and adds transmitted usage.

Assert event order, exact egress stages, reservation count, recovery count, terminal state, and absence of sentinel prose. Do not calculate accuracy, recall, latency claims, or any quality metric from fixture output.

- [ ] **Step 2: Run the fixture REDs and fix only production joins they expose**

```bash
SPECPILOT_TEST_DSN=postgresql:///specpilot_w4_scratch SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 .venv/bin/python -m pytest tests/integration/api/test_l2_end_to_end.py tests/smoke/test_fixture_pipeline.py -q
```

Expected before final wiring: failures identify missing real joins, not test typos. Apply minimal fixes in the owning files, rerunning each failing test until GREEN.

- [ ] **Step 3: Run `make check` fresh**

```bash
make check
```

Expected: ruff clean, mypy strict clean, unit and CLI suites report zero failures.

- [ ] **Step 4: Exercise every migration and every service-dependent test on a fresh database**

First verify Qdrant is reachable and names the frozen collection:

```bash
curl --fail --silent http://localhost:6333/collections/specpilot_ff4841e2d846388014efa06870fbbdb7
```

Create a fresh database, run the whole suite, and drop it even after failure. Use a shell trap rather than a semicolon chain that could skip cleanup:

```bash
createdb specpilot_w4_scratch
SPECPILOT_TEST_DSN=postgresql:///specpilot_w4_scratch \
SPECPILOT_TEST_QDRANT_URL=http://localhost:6333 \
  .venv/bin/python -m pytest -q
dropdb specpilot_w4_scratch
```

Expected: exit 0 and `0 skipped`. If the database already exists, stop and choose a new explicit scratch name; do not reuse a hand-migrated database.

- [ ] **Step 5: Run leakage and diff checks**

```bash
git diff --check
git status --short
rg -n "PRIVATE-QUESTION-SENTINEL|PRIVATE-CLAUSE-SENTINEL" migrations src docs tests --glob '!tests/**'
```

Expected: no whitespace errors; only intentional files modified; no sentinel outside tests.

- [ ] **Step 6: Write the report from command output, not memory**

`docs/reports/w4-compliance-verifier-recovery.md` must record:

- commit tested and exact commands;
- pass/fail/skip totals copied from the fresh output;
- which fixture paths were exercised;
- that semantic support is a model judgement and fixture tests are engineering evidence, not quality evidence;
- that live provider calls, L2 dev calibration/numbers, SSE, full demo profiles, and locked evaluation remain open;
- process recovery semantics: owner resubmission, same hash/root/budget, no durable prose.

Update the W4 roadmap row and add a dated annotation to the project plan only for facts the commands proved. Do not state W4 as wholly complete if dev evaluation/calibration remains open; label this engineering package complete and list the remaining evaluation deliverables.

- [ ] **Step 7: Re-run documentation-sensitive checks and commit**

```bash
make check
git diff --check
```

Expected: exit 0.

```bash
git add tests/smoke/test_fixture_pipeline.py tests/integration/api/test_l2_end_to_end.py docs/roadmaps/2026-08-06-specpilot-master-roadmap.md docs/reports/w4-compliance-verifier-recovery.md SpecPilot_项目方案.md
git commit -m "docs: record the verified W4 engineering package" -m "Fresh PostgreSQL and frozen-Qdrant evidence now covers Compliance, deterministic and semantic gates, one directed recovery, and owner-assisted process resume. The report separates this engineering evidence from still-open dev quality calibration and live-provider acceptance."
```

## Plan Self-Review Checklist

- [ ] Every approved spec section maps to Tasks 1–8.
- [ ] No task persists question, claim, rationale, tool query, excerpt, or provider-response prose.
- [ ] L1 remains capped at six; L2 plan plus recovery is capped at eight.
- [ ] Directed retrieval recovery is run-scoped and single-use; process resume is not artificially single-use.
- [ ] Compliance and semantic stages have different prompts, schemas, stage IDs, and reservations.
- [ ] A recovered candidate passes the complete deterministic and semantic gates again.
- [ ] Resume validates owner, query hash, binding, checkpoint, ledger state, idempotency, and lease before delivery.
- [ ] Provider keys contain explicit reconstruction generation, not process attempt number; every resend adds transmitted usage under unchanged caps.
- [ ] Migration tests use a fresh database and the final whole-suite run requires zero skips.
- [ ] Report language distinguishes deterministic engineering evidence from semantic model quality.
