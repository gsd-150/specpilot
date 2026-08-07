# Task 8 Step 4 Scope 1 Assessments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the executable Scope 1 prerequisite by freezing two Release
18 origins in default-deny source-manifest/v1 records, implementing verifiable
source-bound assessment envelopes, capturing route evidence, and materializing
two conditionally complete DeepSeek assessments plus two unsigned ChatAnywhere
drafts without creating an authorized successor or beginning Scope 2.

**Architecture:** Existing source-manifest/v1 remains byte-for-byte compatible
and identifies each immutable official origin even when OOXML inspection
refuses the DOCX. A new compliance contract binds an assessment to one stored
initial manifest, one exact provider route, one exact model slug, and one
content-addressed evidence index. Restricted evidence and assessment files stay
Git-ignored; the repository receives only implementation, tests, aligned design
text, and a sanitized status record.

**Tech Stack:** Python 3.12+, Pydantic v2, existing secure filesystem helpers,
canonical JSON/SHA-256, pytest, Ruff, mypy, curl, and the authenticated browser
session used only for the DeepSeek personal-account setting.

## Global Constraints

- Scope 1 only. Do not implement an OOXML sanitizer, OPC analyzer, transformer,
  derivation record, source-manifest/v2 creator/reader, or derivative CLI.
- Scope 2 remains blocked until the go/no-go record prices Route D against
  Route C and records both cheaper alternatives as tried and failed.
- Main route is exactly `deepseek` /
  `online-main-deepseek-v4-flash-api` / `online_main`; model slug is exactly
  `deepseek-v4-flash`.
- Judge route is exactly `chatanywhere` / `offline-judge-glm-5-2-api` /
  `offline_judge`; model slug is exactly `glm-5.2`. This user decision is
  resolved and is not an open question.
- Preserve all valid source-manifest/v1 canonical bytes, IDs, authorization
  behavior, and public method signatures. Rejecting previously normalized
  whitespace-only variants is an intentional validation hardening.
- Every initial manifest remains default-deny. Create no successor, invoke no
  real provider transport, and send no 3GPP content or excerpt.
- The two ChatAnywhere envelopes always omit `author_conclusion`.
- Add `author_conclusion` to both DeepSeek envelopes together only when the
  DeepSeek evidence index hash-binds every API-governing document the route
  requires (`deepseek-api-docs`, `deepseek-privacy`, `deepseek-terms`), each
  matching a supplied `ProviderPolicyEvidence` record by document hash, URL, and
  capture time, and none captured after the conclusion's `authored_at`. Any
  missing, unbound, or post-dating document leaves both unsigned.
- `deepseek-account-setting` is optional context, not a gate. The chat product's
  data-use switch governs a different surface than the API route being
  authorized, so `observed`, `blocked`, and `not_captured` all leave completion
  unchanged. A conclusion that does lean on that setting must record the surface
  mismatch in `uncertainty`.
- The copied DeepSeek conclusion has exact fields:

```json
{
  "authorized": true,
  "authorization_statement": "基于截至2026-08-07记录的3GPP/ETSI官方条款、适用于本账户的服务商数据政策，以及default-v1强制出站限制，我授权仅将该冻结源文档的白名单字段和受限片段通过DeepSeek官方API路由用于SpecPilot在线主链处理。",
  "author_id": "chunxue",
  "provider_id": "deepseek",
  "endpoint_purpose": "online-main-deepseek-v4-flash-api",
  "authored_at": "2026-08-07T14:44:00Z",
  "expires_at": "2026-09-06T14:44:00Z"
}
```

- The authorization statement is exactly 268 UTF-8 bytes with SHA-256
  `b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215`.
  Reject leading/trailing whitespace or any altered byte.
- A tool may validate or mechanically copy that conclusion after the gate; it
  may not write, infer, paraphrase, or upgrade the author's conclusion.
- `download_url`, `downloaded_at`, every assessment summary, and every
  `uncertainty` entry must be captured facts or exact text the author confirms.
  Do not attribute assistant-generated policy prose to `author_id=chunxue`.
- The source-bound envelope contains an explicit `evidence_index_id`; the
  evidence index includes `model_slug` in its canonical bytes. Never infer
  source, route, model, or index identity from filenames or prose summaries.
  This fifth envelope field is the recorded binding choice that makes the
  design's hash-bound evidence requirement mechanically enforceable; Task 0
  must add it to both specifications before implementation begins.
- The stable unsupported-version refusal code is exactly
  `unsupported_manifest_version`, with exit 2, empty stdout, and exactly that
  line on stderr.
- Keep original ZIP/DOCX bytes immutable. A refused original remains quarantine
  evidence and is never moved or copied into an accepted `data/real` location.
- Restricted directories are `0700`; restricted data files are `0600`; reject
  symlinks and publish with no-replace semantics.
- Preserve the user-owned untracked `SpecPilot_项目方案.md`. Update only the
  confirmed `glm-5.2`/ChatAnywhere wording and ADR 0001 ingestion wording; never
  stage or commit it.
- Do not create or modify `artifacts/public/w0-verification.json` or
  `docs/reports/w0-foundation-report.md`. Task 10 remains incomplete and the
  only permitted eligibility is `extend`.

## Verified Starting Point

- Branch: `feat/w0-foundation`.
- Revised design/runbook commit: `705e9c5`.
- Existing restricted source: TS 38.300 archive SHA-256
  `cd99c7a86796046d906c73e52f375a4bcda1bec8003ca6f158adb2bb669f8145`
  and quarantined DOCX SHA-256
  `c287873582310c0f609eab0ff2ee33634c4a2a1d10dd6ce6e74d7e96e2a83819`.
- TS 38.321, source manifests, assessment envelopes, and dated compliance
  evidence are absent.
- Baseline verification on 2026-08-07: 248 tests passed, 24 database-dependent
  tests skipped; Ruff and mypy passed.
- Task 10 final JSON/report are absent.

## File Structure

### Repository files

- Modify: `docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md`
- Modify: `docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md`
- Create: `src/specpilot/contracts/compliance.py`
- Create: `src/specpilot/compliance/__init__.py`
- Create: `src/specpilot/compliance/validation.py`
- Create: `tests/unit/compliance/__init__.py`
- Create: `tests/unit/compliance/test_assessment_binding.py`
- Modify: `tests/unit/manifests/test_source_manifest.py`
- Modify: `src/specpilot/manifests/store.py`
- Modify: `src/specpilot/cli.py`
- Modify: `tests/unit/manifests/test_manifest_store.py`
- Modify: `tests/cli/test_manifest_commands.py`
- Create: `docs/compliance/2026-08-07-task8-step4-evidence-status.md`

### Restricted Git-ignored files

- Preserve: `artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip`
- Preserve: `data/quarantine/3gpp/38.300/38300-ia0.docx`
- Create: `artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip`
- Create either accepted or quarantined original:
  `data/real/3gpp/38.321/18.10.0/38321-ia0.docx` or
  `data/quarantine/3gpp/38.321/38321-ia0.docx`
- Create: `manifests/local/task8-step4/source/*.json`
- Create: `artifacts/restricted/compliance/2026-08-07/`
- Create: `manifests/local/task8-step4/assessment-drafts/*.json`

---

## Plan Execution Preflight

Before Task 0, commit this reviewed plan and the superseded marker as one
documentation-only commit. Assert that no other path is staged and that the
user-owned proposal remains untracked:

```bash
git add -- \
  docs/superpowers/plans/2026-08-07-task8-step4-evidence-draft.md \
  docs/superpowers/plans/2026-08-07-task8-step4-scope1-assessments.md
git diff --cached --check
test "$(git diff --cached --name-only | wc -l | tr -d ' ')" = 2
git commit -m "docs: plan task8 scope1 assessments"
test -z "$(git diff --cached --name-only)"
test "$(git status --short -- 'SpecPilot_项目方案.md' | cut -c1-2)" = '??'
```

At the beginning of every task, record `BASE=$(git rev-parse HEAD)` for its
review package and confirm the index is empty. Never include the untracked
proposal or restricted files in a commit.

---

### Task 0: Reconcile the written Scope 1 contract

**Files:**
- Modify: `docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md`
- Modify: `docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md`

**Interfaces:**
- Consumes: the user's already-recorded `glm-5.2` decision and exact DeepSeek
  conclusion.
- Produces: one non-contradictory specification for Tasks 1–6.

- [x] **Step 1: Run assertions that expose the stale text**

```bash
rg -n 'Open item the author must resolve|gpt-5\.6-luna|不得填写.*author_conclusion' \
  docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md \
  docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md
```

Expected: stale model/conclusion language is found.

- [x] **Step 2: Apply the exact reconciliation**

Use `apply_patch` to update these exact sections in both specifications:

- Goal/current boundary: Scope 1 now produces source-bound envelopes, not four
  bare unsigned three-section files.
- Route tables: `glm-5.2` over ChatAnywhere is resolved; remove every open-item
  or old `gpt-5.6-luna` reference.
- Envelope schema: add `evidence_index_id`; add `model_slug` to the canonical
  evidence-index shape.
- Completion matrix: fully hash-bound, non-post-dating API-governing documents
  permit exactly two complete DeepSeek assessments; any missing or unbound
  required document leaves those two unsigned; the two ChatAnywhere assessments
  are always unsigned.
- Author boundary: tools may mechanically copy only the exact confirmed
  conclusion after all gates, while source/policy prose still comes from the
  author.
- Source identity: resolution of the stored canonical initial-v1 manifest
  verifies the origin hashes already bound into its ID; do not duplicate origin
  fields in the envelope.
- Scope 2: retain the route-decision block unchanged.

- [x] **Step 3: Verify and commit only the reconciled specifications**

```bash
test "$(rg -c 'glm-5\.2' docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md)" -ge 2
test "$(rg -c 'evidence_index_id' docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md)" -ge 1
test "$(rg -c 'evidence_index_id' docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md)" -ge 1
test "$(rg -c 'status=observed' docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md)" -ge 1
! rg -n 'Open item the author must resolve|gpt-5\.6-luna' \
  docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md \
  docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md
! rg -n '本工作只准备证据和前三部分|完整 assessment 刻意保持未签署|作者本人复核证据并填写完整' \
  docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md
git diff --check
git add -- \
  docs/superpowers/specs/2026-08-07-safe-ooxml-derivative-deepseek-assessment-design.md \
  docs/superpowers/specs/2026-08-07-task8-step4-assessment-design.md
git diff --cached --check
git commit -m "docs: reconcile task8 scope1 assessment contract"
```

---

### Task 1: Implement source-bound assessment and evidence contracts with TDD

**Files:**
- Create: `src/specpilot/contracts/compliance.py`
- Create: `src/specpilot/compliance/__init__.py`
- Create: `src/specpilot/compliance/validation.py`
- Modify: `src/specpilot/contracts/manifests.py`
- Create: `tests/unit/compliance/__init__.py`
- Create: `tests/unit/compliance/test_assessment_binding.py`
- Modify: `tests/unit/manifests/test_source_manifest.py`
- Read only: `src/specpilot/manifests/canonical.py`

**Interfaces:**
- Produces: `ComplianceAssessmentDraft`, `ComplianceEvidenceIndex`,
  `EvidenceIndexEntry`, `ProviderPolicyEvidence`,
  `DeepSeekAccountObservation`, `DeepSeekAccountNotCaptured`,
  `SourceBoundAssessment`, `AssessmentBindingError`, exact Task 8
  route/conclusion constants, `TASK8_REQUIRED_POLICY_EVIDENCE_KINDS`, and
  `validate_task8_source_bound_assessment(...) -> SourceManifest`.
- Preserves: existing `ComplianceAssessment`, source-manifest/v1, and canonical
  manifest bytes without modification.

- [x] **Step 1: Write failing contract and swap-refusal tests**

The test helpers use two stored initial manifests, two routes, and two evidence
indexes. Include these representative assertions and parameterize the same
pattern for source, route, use, model, and evidence-index swaps:

```python
def test_unsigned_chatanywhere_envelope_is_bound_but_not_complete(
    stored_sources,
    chatanywhere_index,
) -> None:
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=CHATANYWHERE_ROUTE,
        model_slug="glm-5.2",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=None,
    )
    resolved = validate_task8_source_bound_assessment(
        envelope,
        manifest_store=stored_sources.store,
        evidence_index=chatanywhere_index,
    )
    assert resolved.manifest_id == envelope.source_manifest_id
    with pytest.raises(ValidationError) as error:
        ComplianceAssessment.model_validate(envelope.assessment.model_dump())
    assert {item["loc"] for item in error.value.errors()} == {
        ("author_conclusion",)
    }


def test_envelope_refuses_a_model_not_bound_by_its_index(
    stored_sources,
    chatanywhere_index,
) -> None:
    envelope = build_envelope(
        source_manifest_id=stored_sources["38.300"].manifest_id,
        route_binding=CHATANYWHERE_ROUTE,
        model_slug="different-model",
        evidence_index_id=canonical_sha256(chatanywhere_index),
        author_conclusion=None,
    )
    with pytest.raises(AssessmentBindingError, match="model"):
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=stored_sources.store,
            evidence_index=chatanywhere_index,
        )
```

Also require tests for a complete DeepSeek envelope with the exact route and
conclusion; provider/endpoint mismatch; unstored or successor source; index ID,
route, use, and model mismatch; extra fields; unsafe URLs; invalid timestamps;
non-strict booleans; duplicate or unsorted evidence kinds; and a canonical
index ID change when `model_slug` changes. A DeepSeek conclusion missing any
required API-governing document, supplying one whose hash, URL, or capture time
disagrees with its index entry, supplying a kind twice, or resting on a document
captured after `authored_at` must fail. Leading/trailing whitespace in an
authorization statement must fail before Pydantic can normalize it. Preserve a
golden v1 canonical-byte/manifest-ID test.

Parameterize one negative test over every conclusion field: `authorized=false`,
changed statement bytes, different `author_id`, provider, endpoint, authored
time, or expiry must each fail. Any present conclusion on the ChatAnywhere
route must fail even when it matches that route.

- [x] **Step 2: Run the new tests and prove red**

```bash
.venv/bin/python -m pytest tests/unit/compliance/test_assessment_binding.py -q
```

Expected: collection or import fails because the new contracts do not exist.

- [x] **Step 3: Implement the minimal frozen contracts**

Implement these exact field shapes in `contracts/compliance.py`:

```python
class ComplianceAssessmentDraft(FrozenComplianceModel):
    source_terms: SourceTermsAssessment
    provider_policy: ProviderPolicyAssessment
    outbound_limit: OutboundLimitAssessment
    author_conclusion: AuthorizationConclusion | None = None


class EvidenceIndexEntry(FrozenComplianceModel):
    kind: Identifier
    url: HttpsUrl
    captured_at: datetime
    sha256: Sha256
    summary: Summary
    scope: Summary


class ComplianceEvidenceIndex(FrozenComplianceModel):
    schema_version: Literal["compliance-evidence-index/v1"]
    route: ProviderRouteBinding
    model_slug: Identifier
    entries: Annotated[tuple[EvidenceIndexEntry, ...], Field(min_length=1)]


class ProviderPolicyEvidence(FrozenComplianceModel):
    kind: Identifier
    url: HttpsUrl
    captured_at: datetime
    document_sha256: Sha256


class DeepSeekAccountObservation(FrozenComplianceModel):
    status: Literal["observed"]
    setting_state: Literal["enabled", "disabled"]
    captured_at: datetime
    url: HttpsUrl
    screenshot_sha256: Sha256


class DeepSeekAccountNotCaptured(FrozenComplianceModel):
    status: Literal["not_captured"]
    reason: Identifier
    captured_at: datetime
    url: HttpsUrl


class SourceBoundAssessment(FrozenComplianceModel):
    schema_version: Literal["source-bound-assessment/v1"]
    source_manifest_id: Sha256
    route_binding: ProviderRouteBinding
    model_slug: Identifier
    evidence_index_id: Sha256
    assessment: ComplianceAssessmentDraft
```

Use existing RFC3339/HTTPS semantics, require evidence entries to have unique
`kind` values in lexicographic order, and keep `extra="forbid"`, strict input
handling, deep immutability, and canonical JSON behavior. Add a mode-before
validator to `AuthorizationConclusion.authorization_statement` that rejects a
non-string or any value unequal to its own `.strip()`; valid existing manifest
bytes and IDs must remain unchanged.

- [x] **Step 4: Implement mechanical binding validation**

In `compliance/validation.py`, define the two exact Task 8 route/model pairs and
the exact seven-field `TASK8_DEEPSEEK_CONCLUSION` from Global Constraints, then
implement:

```python
def validate_task8_source_bound_assessment(
    envelope: SourceBoundAssessment,
    *,
    manifest_store: ManifestStore,
    evidence_index: ComplianceEvidenceIndex,
    policy_evidence: tuple[ProviderPolicyEvidence, ...] = (),
) -> SourceManifest:
    try:
        manifest = manifest_store.read_source(envelope.source_manifest_id)
    except (OSError, RuntimeError, ValueError) as error:
        raise AssessmentBindingError("source binding is invalid") from error
    if (
        manifest.predecessor_manifest_id is not None
        or manifest.cloud_egress_authorized
        or manifest.compliance_assessment is not None
        or manifest.provider_route_binding is not None
    ):
        raise AssessmentBindingError("source state is invalid")
    if canonical_sha256(evidence_index) != envelope.evidence_index_id:
        raise AssessmentBindingError("evidence index binding is invalid")
    if evidence_index.route != envelope.route_binding:
        raise AssessmentBindingError("route binding is invalid")
    if evidence_index.model_slug != envelope.model_slug:
        raise AssessmentBindingError("model binding is invalid")
    expected_model = TASK8_MODELS_BY_ROUTE.get(envelope.route_binding)
    if expected_model is None or expected_model != envelope.model_slug:
        raise AssessmentBindingError("task8 route is invalid")
    conclusion = envelope.assessment.author_conclusion
    if conclusion is not None:
        if envelope.route_binding != TASK8_DEEPSEEK_ROUTE:
            raise AssessmentBindingError("conclusion route is invalid")
        if conclusion != TASK8_DEEPSEEK_CONCLUSION:
            raise AssessmentBindingError("conclusion content is invalid")
        statement = conclusion.authorization_statement.encode("utf-8")
        if len(statement) != 268 or hashlib.sha256(statement).hexdigest() != (
            "b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215"
        ):
            raise AssessmentBindingError("conclusion content is invalid")
        required = TASK8_REQUIRED_POLICY_EVIDENCE_KINDS.get(envelope.route_binding)
        if required is None:
            raise AssessmentBindingError("policy evidence is invalid")
        supplied = {record.kind: record for record in policy_evidence}
        if len(supplied) != len(policy_evidence):
            raise AssessmentBindingError("policy evidence is invalid")
        entries = {entry.kind: entry for entry in evidence_index.entries}
        for kind in sorted(required):
            entry, record = entries.get(kind), supplied.get(kind)
            if entry is None or record is None:
                raise AssessmentBindingError("policy evidence is invalid")
            if (
                entry.sha256 != record.document_sha256
                or str(entry.url) != str(record.url)
                or entry.captured_at != record.captured_at
            ):
                raise AssessmentBindingError("policy evidence is invalid")
            if entry.captured_at > conclusion.authored_at:
                raise AssessmentBindingError(
                    "policy evidence postdates the conclusion"
                )
        ComplianceAssessment.model_validate(envelope.assessment.model_dump())
    return manifest
```

The function resolves `source_manifest_id`; requires an initial default-deny v1
manifest; recomputes and compares the evidence index ID; compares the complete
route and exact model; rejects every conclusion outside the exact DeepSeek
route; requires a DeepSeek conclusion to equal all seven confirmed fields and to
hash-bind every API-governing document that route requires, none of them
captured after `authored_at`; validates it as a complete `ComplianceAssessment`;
returns the resolved manifest; and never writes files or authorizes egress.
Normalize failures to content-free `AssessmentBindingError` messages.

- [x] **Step 5: Run focused and regression tests**

```bash
.venv/bin/python -m pytest tests/unit/compliance/test_assessment_binding.py -q
.venv/bin/python -m pytest tests/unit/manifests -q
.venv/bin/ruff check src/specpilot/contracts/compliance.py \
  src/specpilot/compliance tests/unit/compliance
.venv/bin/mypy src
```

Expected: all commands exit 0; existing v1 tests remain green.

- [x] **Step 6: Commit the contract slice**

```bash
git add -- src/specpilot/contracts/compliance.py \
  src/specpilot/contracts/manifests.py src/specpilot/compliance \
  tests/unit/compliance tests/unit/manifests/test_source_manifest.py
git diff --cached --check
git commit -m "feat: bind compliance assessments to sources and models"
```

---

### Task 2: Return a stable unsupported-manifest-version refusal

**Files:**
- Modify: `src/specpilot/manifests/store.py`
- Modify: `src/specpilot/cli.py`
- Modify: `tests/unit/manifests/test_manifest_store.py`
- Modify: `tests/cli/test_manifest_commands.py`

**Interfaces:**
- Produces: `UnsupportedManifestVersionError` from `read_source` when a secure,
  readable JSON object declares a schema other than `source-manifest/v1`.
- Produces: CLI refusal `unsupported_manifest_version` without adding a v2
  reader or successor path.

- [x] **Step 1: Write failing store and CLI tests**

Add a mode-`0600` regular file named by a lowercase SHA-256 whose JSON declares
`schema_version="source-manifest/v2"`. Assert:

```python
with pytest.raises(UnsupportedManifestVersionError):
    store.read_source(v2_id)
```

At the CLI boundary assert exact bytes:

```python
assert code == 2
assert out == ""
assert err == "unsupported_manifest_version\n"
```

Tighten the missing-predecessor test to exactly `manifest_not_found\n`. Add
malformed JSON and missing-file cases proving they are not misclassified.

- [x] **Step 2: Run the focused tests and prove red**

```bash
.venv/bin/python -m pytest \
  tests/unit/manifests/test_manifest_store.py \
  tests/cli/test_manifest_commands.py -q
```

Expected: the new version-specific assertions fail against the current broad
exception mapping.

- [x] **Step 3: Implement one narrow exception path**

Define `UnsupportedManifestVersionError(ValueError)` in `store.py`. After the
secure bounded read and before v1 Pydantic decoding, parse JSON only far enough
to inspect a string top-level `schema_version`. Raise the new exception when it
is present and differs from `source-manifest/v1`; otherwise retain current v1
canonical validation. In `_manifest_authorize`, catch this exception before the
broad read failure and return `_refuse("unsupported_manifest_version")`.

- [x] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest \
  tests/unit/manifests/test_manifest_store.py \
  tests/cli/test_manifest_commands.py -q
.venv/bin/ruff check src/specpilot/manifests/store.py src/specpilot/cli.py \
  tests/unit/manifests/test_manifest_store.py tests/cli/test_manifest_commands.py
.venv/bin/mypy src
git add -- src/specpilot/manifests/store.py src/specpilot/cli.py \
  tests/unit/manifests/test_manifest_store.py tests/cli/test_manifest_commands.py
git diff --cached --check
git commit -m "fix: distinguish unsupported manifest versions"
```

---

### Task 3: Freeze both origins and create initial v1 manifests

**Files:**
- Preserve/create: restricted source files listed in File Structure
- Create locally: `manifests/local/task8-step4/source/*.json`
- Create/update: `docs/compliance/2026-08-07-task8-step4-evidence-status.md`

**Interfaces:**
- Consumes: author-confirmed official URLs/download times, source bytes,
  `archive inspect`, and `source-manifest create`.
- Produces: exactly two initial default-deny manifest IDs. A DOCX refusal is
  recorded but does not block manifest creation.

- [x] **Step 1: Reconcile instead of overwriting existing state**

```bash
set -euo pipefail
umask 077
source_dir=artifacts/restricted/sources/3gpp/38.321/18.10.0
archive="$source_dir/38321-ia0.zip"
inspection="$source_dir/inspection.json"
quarantine_dir=data/quarantine/3gpp/38.321
quarantined=data/quarantine/3gpp/38.321/38321-ia0.docx
accepted=data/real/3gpp/38.321/18.10.0/38321-ia0.docx
record="$source_dir/source-capture.json"
test "$(shasum -a 256 artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip | awk '{print $1}')" = cd99c7a86796046d906c73e52f375a4bcda1bec8003ca6f158adb2bb669f8145
test "$(shasum -a 256 data/quarantine/3gpp/38.300/38300-ia0.docx | awk '{print $1}')" = c287873582310c0f609eab0ff2ee33634c4a2a1d10dd6ce6e74d7e96e2a83819
test -d "$source_dir"
test ! -L "$source_dir"
test "$(/usr/bin/stat -f '%Lp' "$source_dir")" = 700
test "$(/usr/bin/stat -f '%Su' "$source_dir")" = "$(id -un)"
test -f "$archive"
test ! -L "$archive"
test "$(/usr/bin/stat -f '%Lp' "$archive")" = 600
test "$(/usr/bin/stat -f '%Su' "$archive")" = "$(id -un)"
test "$(shasum -a 256 "$archive" | awk '{print $1}')" = fc77636b28c57293688e854a3585fcf6056da77d5570d51835d8772eedbe9446
test "$(wc -c < "$archive" | tr -d ' ')" = 3015837
test -d "$quarantine_dir"
test ! -L "$quarantine_dir"
test "$(/usr/bin/stat -f '%Lp' "$quarantine_dir")" = 700
test "$(/usr/bin/stat -f '%Su' "$quarantine_dir")" = "$(id -un)"
test -f "$quarantined"
test ! -L "$quarantined"
test "$(/usr/bin/stat -f '%Lp' "$quarantined")" = 600
test "$(/usr/bin/stat -f '%Su' "$quarantined")" = "$(id -un)"
test "$(shasum -a 256 "$quarantined" | awk '{print $1}')" = 6c98d03d5c3936c251edaa4148e3536bff80addc918c8a5416624aeda82ab9ff
test "$(wc -c < "$quarantined" | tr -d ' ')" = 3857056
test -f "$inspection"
test ! -L "$inspection"
test "$(/usr/bin/stat -f '%Lp' "$inspection")" = 600
test "$(/usr/bin/stat -f '%Su' "$inspection")" = "$(id -un)"
.venv/bin/python - "$inspection" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "inspection_code": "embedded_active_content",
    "docx_sha256": "6c98d03d5c3936c251edaa4148e3536bff80addc918c8a5416624aeda82ab9ff",
}
if value != expected:
    raise SystemExit("unexpected 38.321 inspection record")
PY
test ! -e "$accepted"
test ! -L "$accepted"
test ! -e "$record"
test ! -L "$record"
test ! -e artifacts/public/w0-verification.json
test ! -e docs/reports/w0-foundation-report.md
test -z "$(git diff --cached --name-only)"
```

Expected: both retained origins pass their exact hash/size, mode, ownership,
regular-file/non-symlink, and inspection checks; the 38.321 accepted DOCX and
capture record are absent; Task 10 outputs are absent; and the index is empty.
Never move the quarantined DOCX into `data/real`.

- [x] **Step 2: Download TS 38.321 with private no-replace publication**

Use this exact destination and capture-record shape:

```text
archive: artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip
record:  artifacts/restricted/sources/3gpp/38.321/18.10.0/source-capture.json
record fields: download_url, downloaded_at, archive_sha256, byte_count
```

Run one fail-fast shell with `umask 077`:

```bash
set -euo pipefail
umask 077
source_dir=artifacts/restricted/sources/3gpp/38.321/18.10.0
archive="$source_dir/38321-ia0.zip"
record="$source_dir/source-capture.json"
url=https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip
mkdir -p "$source_dir"
chmod 700 "$source_dir"
test ! -e "$archive"
test ! -L "$archive"
test ! -e "$record"
test ! -L "$record"
tmp="$(mktemp "$source_dir/.38321-ia0.zip.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT HUP INT TERM
curl --fail --location --silent --show-error --compressed \
  --remove-on-error --user-agent 'SpecPilot-W0-evidence/1.0' \
  --output "$tmp" "$url"
chmod 600 "$tmp"
downloaded_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
archive_sha="$(shasum -a 256 "$tmp" | awk '{print $1}')"
byte_count="$(wc -c < "$tmp" | tr -d ' ')"
ln "$tmp" "$archive"
rm -f -- "$tmp"
trap - EXIT HUP INT TERM
printf 'download_url=%s\ndownloaded_at=%s\narchive_sha256=%s\nbyte_count=%s\n' \
  "$url" "$downloaded_at" "$archive_sha" "$byte_count"
```

Use `apply_patch` to write `source-capture.json` from those four exact values,
as printed by the same shell, then `chmod 600` it. Do not reconstruct or rerun
the time. If a final path appears before publication, stop; securely reread and
compare it before deciding whether the attempt is an exact replay.

**Observed interrupted-state recovery branch:** Select this branch, and do not
run the fresh-publication shell above, only after Step 1 proves the retained
38.321 archive, quarantined DOCX, and `inspection.json` are exactly the
interrupted state described there and `source-capture.json` is absent. The
author explicitly approves deletion only of the validated replay temporary file
below; do not publish to, link over, move over, truncate, unlink, or otherwise
change either retained file.

Run this exact fail-fast replay shell. It uses the same official URL and curl
options. It does not install a deletion trap: a protected hard link preserves
any pre-publication curl bytes even though curl retains `--remove-on-error`.
No replay/download bytes are unlinked before the complete replay's facts and
identity are durably appended to the ignored implementer report with
`apply_patch`.

```bash
set -euo pipefail
umask 077
source_dir=artifacts/restricted/sources/3gpp/38.321/18.10.0
archive="$source_dir/38321-ia0.zip"
record="$source_dir/source-capture.json"
url=https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip
retained_sha=fc77636b28c57293688e854a3585fcf6056da77d5570d51835d8772eedbe9446
retained_bytes=3015837
test -d "$source_dir"
test ! -L "$source_dir"
test "$(/usr/bin/stat -f '%Lp' "$source_dir")" = 700
test "$(/usr/bin/stat -f '%Su' "$source_dir")" = "$(id -un)"
test -f "$archive"
test ! -L "$archive"
test "$(shasum -a 256 "$archive" | awk '{print $1}')" = "$retained_sha"
test "$(wc -c < "$archive" | tr -d ' ')" = "$retained_bytes"
test ! -e "$record"
test ! -L "$record"
download_tmp="$(mktemp "$source_dir/.38321-ia0.replay-download.XXXXXX")"
download_guard="$source_dir/.38321-ia0.replay-guard-$(basename "$download_tmp")"
test -f "$download_tmp"
test ! -L "$download_tmp"
chmod 600 "$download_tmp"
test ! -e "$download_guard"
test ! -L "$download_guard"
ln "$download_tmp" "$download_guard"
test -f "$download_guard"
test ! -L "$download_guard"
chmod 600 "$download_guard"
trap 'printf "replay interrupted; preserve guard for reconciliation: %s\n" "$download_guard" >&2; exit 128' HUP INT TERM
set +e
curl --fail --location --silent --show-error --compressed \
  --remove-on-error --user-agent 'SpecPilot-W0-evidence/1.0' \
  --output "$download_tmp" "$url"
curl_exit=$?
set -e
if test "$curl_exit" -ne 0; then
  partial_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  partial_sha="$(shasum -a 256 "$download_guard" | awk '{print $1}')"
  partial_bytes="$(wc -c < "$download_guard" | tr -d ' ')"
  partial_tag="$(printf '%s' "$partial_at" | tr -d ':-')"
  partial="$source_dir/.38321-ia0.replay-partial-${partial_tag}-${partial_sha}.zip"
  test ! -e "$partial"
  test ! -L "$partial"
  ln "$download_guard" "$partial"
  test -f "$partial"
  test ! -L "$partial"
  chmod 600 "$partial"
  partial_real="$(realpath "$partial")"
  partial_identity="$(/usr/bin/stat -f '%d:%i' "$partial")"
  printf '%s\n' \
    BEGIN_TASK3_REPLAY_PARTIAL_V1 \
    "download_url=$url" \
    "captured_at=$partial_at" \
    "partial_sha256=$partial_sha" \
    "partial_byte_count=$partial_bytes" \
    "partial_path=$partial_real" \
    "partial_device_inode=$partial_identity" \
    END_TASK3_REPLAY_PARTIAL_V1
  exit "$curl_exit"
fi
downloaded_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
replay_sha="$(shasum -a 256 "$download_tmp" | awk '{print $1}')"
replay_bytes="$(wc -c < "$download_tmp" | tr -d ' ')"
timestamp_tag="$(printf '%s' "$downloaded_at" | tr -d ':-')"
replay="$source_dir/.38321-ia0.replay-${timestamp_tag}-${replay_sha}.zip"
test ! -e "$replay"
test ! -L "$replay"
ln "$download_tmp" "$replay"
replay_real="$(realpath "$replay")"
replay_identity="$(/usr/bin/stat -f '%d:%i' "$replay")"
retained_identity="$(/usr/bin/stat -f '%d:%i' "$archive")"
test "$replay_sha" = "$retained_sha"
test "$replay_bytes" = "$retained_bytes"
test -f "$replay"
test ! -L "$replay"
chmod 600 "$replay"
test "$(dirname "$replay_real")" = "$(realpath "$source_dir")"
test "$replay_identity" != "$retained_identity"
printf '%s\n' \
  BEGIN_TASK3_REPLAY_RECOVERY_V1 \
  'state=complete' \
  "download_url=$url" \
  "downloaded_at=$downloaded_at" \
  "archive_sha256=$replay_sha" \
  "byte_count=$replay_bytes" \
  "replay_path=$replay_real" \
  "replay_device_inode=$replay_identity" \
  "retained_archive_device_inode=$retained_identity" \
  'replay_archive_sha256_equals_retained=true' \
  'replay_byte_count_equals_retained=true' \
  END_TASK3_REPLAY_RECOVERY_V1
```

On a curl failure, do not delete `download_guard`, `download_tmp` if curl left
it present, or the timestamp/hash-bearing `partial` path. Use `apply_patch` to
append the one verbatim `BEGIN_TASK3_REPLAY_PARTIAL_V1` block printed above to
the ignored implementer report, then stop for later reconciliation. A signal
before the partial block leaves the guard as private recovery state; do not
infer an unprinted timestamp or delete it. The later reconciliation must read
and validate that exact guard path, then capture a new fact rather than
inventing one.

On a successful replay, before any unlink, use `apply_patch` to append exactly
the one verbatim `BEGIN_TASK3_REPLAY_RECOVERY_V1` block printed above to the
ignored `.superpowers/sdd/2026-08-07-task8-step4-scope1-assessments/task-3-implementer-report.md`.
Do not hand-edit, reformat, duplicate, or combine this machine-readable block;
it is the durable record of the replay path, device/inode identity, capture
facts, and both equality results. Preserve `download_tmp` and `download_guard`
as private recovery state in this attempt; this approved cleanup deletes only
the separately validated replay child.

Then validate and remove only the exact replay child. This removal is covered
by the author's explicit non-destructive-replay approval:

```bash
set -euo pipefail
source_dir=artifacts/restricted/sources/3gpp/38.321/18.10.0
archive="$source_dir/38321-ia0.zip"
inspection="$source_dir/inspection.json"
record="$source_dir/source-capture.json"
quarantined=data/quarantine/3gpp/38.321/38321-ia0.docx
retained_38300_archive=artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip
retained_38300_quarantined=data/quarantine/3gpp/38.300/38300-ia0.docx
report=.superpowers/sdd/2026-08-07-task8-step4-scope1-assessments/task-3-implementer-report.md
url=https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip
retained_sha=fc77636b28c57293688e854a3585fcf6056da77d5570d51835d8772eedbe9446
retained_bytes=3015837
state=
download_url=
downloaded_at=
archive_sha256=
byte_count=
replay_path=
replay_device_inode=
retained_archive_device_inode=
replay_archive_sha256_equals_retained=
replay_byte_count_equals_retained=
report_fields="$(.venv/bin/python - "$report" <<'PY'
import re
import sys
from pathlib import Path

begin = "BEGIN_TASK3_REPLAY_RECOVERY_V1"
end = "END_TASK3_REPLAY_RECOVERY_V1"
keys = (
    "state",
    "download_url",
    "downloaded_at",
    "archive_sha256",
    "byte_count",
    "replay_path",
    "replay_device_inode",
    "retained_archive_device_inode",
    "replay_archive_sha256_equals_retained",
    "replay_byte_count_equals_retained",
)
text = Path(sys.argv[1]).read_text(encoding="utf-8")
if text.count(begin) != 1 or text.count(end) != 1:
    raise SystemExit("expected one complete replay report block")
block = text.split(begin + "\n", 1)[1].split("\n" + end, 1)[0]
lines = block.splitlines()
if len(lines) != len(keys):
    raise SystemExit("unexpected replay report block length")
values = {}
for expected_key, line in zip(keys, lines, strict=True):
    key, separator, value = line.partition("=")
    if separator != "=" or key != expected_key or not value or "\n" in value:
        raise SystemExit("unexpected replay report field")
    values[key] = value
if values["state"] != "complete":
    raise SystemExit("replay report is not complete")
if values["download_url"] != "https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip":
    raise SystemExit("unexpected replay URL")
if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", values["downloaded_at"]):
    raise SystemExit("unexpected replay timestamp")
if not re.fullmatch(r"[0-9a-f]{64}", values["archive_sha256"]):
    raise SystemExit("unexpected replay hash")
if not re.fullmatch(r"[0-9]+", values["byte_count"]):
    raise SystemExit("unexpected replay size")
if not re.fullmatch(r"/[A-Za-z0-9._/-]+", values["replay_path"]):
    raise SystemExit("unexpected replay path")
for key in ("replay_device_inode", "retained_archive_device_inode"):
    if not re.fullmatch(r"[0-9]+:[0-9]+", values[key]):
        raise SystemExit("unexpected replay identity")
if values["replay_archive_sha256_equals_retained"] != "true" or values["replay_byte_count_equals_retained"] != "true":
    raise SystemExit("replay equality is not recorded")
for key in keys:
    print(f"{key}={values[key]}")
PY
)"
while IFS='=' read -r key value; do
  case "$key" in
    state) state="$value" ;;
    download_url) download_url="$value" ;;
    downloaded_at) downloaded_at="$value" ;;
    archive_sha256) archive_sha256="$value" ;;
    byte_count) byte_count="$value" ;;
    replay_path) replay_path="$value" ;;
    replay_device_inode) replay_device_inode="$value" ;;
    retained_archive_device_inode) retained_archive_device_inode="$value" ;;
    replay_archive_sha256_equals_retained) replay_archive_sha256_equals_retained="$value" ;;
    replay_byte_count_equals_retained) replay_byte_count_equals_retained="$value" ;;
    *) exit 1 ;;
  esac
done <<EOF
$report_fields
EOF
test "$state" = complete
test "$download_url" = "$url"
test "$archive_sha256" = "$retained_sha"
test "$byte_count" = "$retained_bytes"
test "$replay_archive_sha256_equals_retained" = true
test "$replay_byte_count_equals_retained" = true
timestamp_tag="$(printf '%s' "$downloaded_at" | tr -d ':-')"
expected_basename=".38321-ia0.replay-${timestamp_tag}-${archive_sha256}.zip"
replay_dir="$(realpath "$source_dir")"
expected_replay="$replay_dir/$expected_basename"
test "$replay_path" = "$expected_replay"
replay_basename="$(basename "$replay_path")"
.venv/bin/python - "$replay_basename" "$downloaded_at" "$archive_sha256" <<'PY'
import re
import sys

basename, downloaded_at, archive_sha256 = sys.argv[1:]
timestamp_tag = downloaded_at.replace("-", "").replace(":", "")
expected = f".38321-ia0.replay-{timestamp_tag}-{archive_sha256}.zip"
if basename != expected or not re.fullmatch(
    r"\.38321-ia0\.replay-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{64}\.zip",
    basename,
):
    raise SystemExit("unexpected replay basename")
PY
test "$(dirname "$replay_path")" = "$replay_dir"
for forbidden in \
  "$(realpath "$archive")" \
  "$(realpath "$inspection")" \
  "$replay_dir/$(basename "$record")" \
  "$(realpath "$quarantined")" \
  "$(realpath "$retained_38300_archive")" \
  "$(realpath "$retained_38300_quarantined")"; do
  test "$replay_path" != "$forbidden"
done
test -f "$replay_path"
test ! -L "$replay_path"
test "$(/usr/bin/stat -f '%Lp' "$replay_path")" = 600
test "$(/usr/bin/stat -f '%Su' "$replay_path")" = "$(id -un)"
test "$(shasum -a 256 "$replay_path" | awk '{print $1}')" = "$retained_sha"
test "$(wc -c < "$replay_path" | tr -d ' ')" = "$retained_bytes"
current_replay_identity="$(/usr/bin/stat -f '%d:%i' "$replay_path")"
current_retained_identity="$(/usr/bin/stat -f '%d:%i' "$archive")"
test "$current_replay_identity" = "$replay_device_inode"
test "$current_retained_identity" = "$retained_archive_device_inode"
replay_inode="${current_replay_identity#*:}"
retained_inode="${current_retained_identity#*:}"
test "$replay_inode" != "$retained_inode"
/bin/rm -- "$replay_path"
test ! -e "$replay_path"
test ! -L "$replay_path"
```

Stop at the author-fact gate after that deletion. Do not create
`source-capture.json`, either manifest, public status, or a Task 3 commit until
the author confirms the newly captured exact TS 38.321 `download_url` and
`downloaded_at` recorded in the ignored implementer report.

- [x] **Step 3: Inspect each source independently**

The 38.321 inspection paths are mutually exclusive. Select the
observed-interrupted/recovery branch only when Step 1 and the reviewed recovery
branch established the existing `inspection.json` and quarantined DOCX. That
branch verifies both artifacts read-only and skips the inspector and every
publication operation. Select the fresh-publication branch only when both
artifacts, the accepted path, and transient stderr are absent. The presence of
only one recovery artifact, any symlink at an absent-state path, or any other
mixed state is an error and must stop before inspection.

Run this discriminator and exactly one branch:

```bash
set -euo pipefail
umask 077
source_dir=artifacts/restricted/sources/3gpp/38.321/18.10.0
archive="$source_dir/38321-ia0.zip"
inspection="$source_dir/inspection.json"
quarantine_dir=data/quarantine/3gpp/38.321
quarantined="$quarantine_dir/38321-ia0.docx"
accepted=data/real/3gpp/38.321/18.10.0/38321-ia0.docx
inspection_stderr="$quarantine_dir/inspection.stderr"

recovery_present=0
if test -e "$inspection" || test -L "$inspection" || \
   test -e "$quarantined" || test -L "$quarantined"; then
  recovery_present=1
fi

if test "$recovery_present" -eq 1; then
  # Observed interrupted/recovery state: read-only verification only.
  test -d "$quarantine_dir"
  test ! -L "$quarantine_dir"
  test "$(/usr/bin/stat -f '%Lp' "$quarantine_dir")" = 700
  test "$(/usr/bin/stat -f '%Su' "$quarantine_dir")" = "$(id -un)"
  test -f "$inspection"
  test ! -L "$inspection"
  test "$(/usr/bin/stat -f '%Lp' "$inspection")" = 600
  test "$(/usr/bin/stat -f '%Su' "$inspection")" = "$(id -un)"
  test "$(shasum -a 256 "$inspection" | awk '{print $1}')" = \
    4c21359dbc68fe0f45fcb89b3d105ce3cad37b6fc956464d28735d3de59a6d9c
  test "$(wc -c < "$inspection" | tr -d ' ')" = 136
  .venv/bin/python - "$inspection" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "inspection_code": "embedded_active_content",
    "docx_sha256": "6c98d03d5c3936c251edaa4148e3536bff80addc918c8a5416624aeda82ab9ff",
}
if value != expected:
    raise SystemExit("unexpected 38.321 recovery inspection record")
PY
  test -f "$quarantined"
  test ! -L "$quarantined"
  test "$(/usr/bin/stat -f '%Lp' "$quarantined")" = 600
  test "$(/usr/bin/stat -f '%Su' "$quarantined")" = "$(id -un)"
  test "$(shasum -a 256 "$quarantined" | awk '{print $1}')" = \
    6c98d03d5c3936c251edaa4148e3536bff80addc918c8a5416624aeda82ab9ff
  test "$(wc -c < "$quarantined" | tr -d ' ')" = 3857056
  test ! -e "$accepted"
  test ! -L "$accepted"
  test ! -e "$inspection_stderr"
  test ! -L "$inspection_stderr"
  printf '%s\n' 'inspection_branch=observed_recovery_read_only'
else
  # Fresh-publication state: inspect once, then complete the observed branch.
  test ! -e "$inspection"
  test ! -L "$inspection"
  test ! -e "$quarantined"
  test ! -L "$quarantined"
  test ! -e "$accepted"
  test ! -L "$accepted"
  test ! -e "$inspection_stderr"
  test ! -L "$inspection_stderr"
  if test -e "$quarantine_dir" || test -L "$quarantine_dir"; then
    test -d "$quarantine_dir"
    test ! -L "$quarantine_dir"
    test "$(/usr/bin/stat -f '%Su' "$quarantine_dir")" = "$(id -un)"
  else
    test ! -L "$quarantine_dir"
    mkdir -p "$quarantine_dir"
  fi
  chmod 700 "$quarantine_dir"
  test -d "$quarantine_dir"
  test ! -L "$quarantine_dir"
  test "$(/usr/bin/stat -f '%Lp' "$quarantine_dir")" = 700
  test "$(/usr/bin/stat -f '%Su' "$quarantine_dir")" = "$(id -un)"
  inspection_attempt_pending=1
  trap 'if test "${inspection_attempt_pending:-0}" = 1; then if test -e "$accepted" || test -L "$accepted"; then printf "%s\n" "TASK3_STEP3_UNVALIDATED_EXTRACTION_RECOVERY_REQUIRED" "preserve accepted=$accepted stderr=$inspection_stderr exactly as found" "do not rerun or delete; record path identities, hashes, and inspection_code=${inspection_code:-not_captured} before reconciliation" >&2; fi; fi' EXIT
  trap 'exit 128' HUP INT TERM
  set +e
  inspection_out="$(.venv/bin/python -m specpilot.cli archive inspect \
    --archive "$archive" \
    --destination data/real/3gpp/38.321/18.10.0 \
    --quarantine "$quarantine_dir" \
    --expect-docx 38321-ia0.docx 2>"$inspection_stderr")"
  inspection_code=$?
  set -e
  if test "$inspection_code" -eq 0; then
    test -n "$inspection_out"
    test ! -s "$inspection_stderr"
    test -f "$accepted"
    test ! -L "$accepted"
    test ! -e "$quarantined"
    test ! -L "$quarantined"
    /bin/rm -- "$inspection_stderr"
    test ! -e "$inspection_stderr"
    test ! -L "$inspection_stderr"
    printf '%s\n' 'inspection_branch=fresh_accepted'
    inspection_attempt_pending=0
    trap - EXIT HUP INT TERM
  elif test "$inspection_code" -eq 2; then
    # From this point, every failure preserves exact state and prints the
    # recovery instruction instead of rerunning or deleting evidence.
    fresh_refusal_pending=1
    trap 'if test "${fresh_refusal_pending:-0}" = 1; then printf "%s\n" "TASK3_STEP3_FRESH_REFUSAL_RECOVERY_REQUIRED" "preserve fresh=$accepted quarantined=$quarantined stderr=$inspection_stderr exactly as found" "do not rerun, move, overwrite, or delete; record path identities and hashes, then resume link-before-unlink reconciliation" >&2; fi' EXIT
    inspection_attempt_pending=0
    trap 'exit 128' HUP INT TERM
    test -z "$inspection_out"
    test "$(wc -l < "$inspection_stderr" | tr -d ' ')" = 1
    refusal_code="$(/bin/cat "$inspection_stderr")"
    test "$refusal_code" = embedded_active_content
    test -f "$accepted"
    test ! -L "$accepted"
    test ! -e "$quarantined"
    test ! -L "$quarantined"
    chmod 600 "$accepted"
    docx_sha="$(shasum -a 256 "$accepted" | awk '{print $1}')"
    test "$docx_sha" = \
      6c98d03d5c3936c251edaa4148e3536bff80addc918c8a5416624aeda82ab9ff
    ln "$accepted" "$quarantined"
    test -f "$quarantined"
    test ! -L "$quarantined"
    test "$(/usr/bin/stat -f '%d:%i' "$accepted")" = \
      "$(/usr/bin/stat -f '%d:%i' "$quarantined")"
    test "$(shasum -a 256 "$accepted" | awk '{print $1}')" = \
      "$(shasum -a 256 "$quarantined" | awk '{print $1}')"
    /bin/rm -- "$accepted"
    test ! -e "$accepted"
    test ! -L "$accepted"
    printf 'inspection_branch=fresh_refused\ninspection_code=%s\ndocx_sha256=%s\n' \
      "$refusal_code" "$docx_sha"
    fresh_refusal_pending=0
    trap - EXIT HUP INT TERM
  else
    exit "$inspection_code"
  fi
fi
```

For a fresh refusal, use `apply_patch` immediately after the successful
link-before-unlink branch to write the printed stable refusal code and DOCX hash
into a new `0600` restricted `inspection.json`; validate its exact content,
owner, regular-file/non-symlink state, then remove only the exact transient
stderr file. If that metadata step stops, preserve the quarantined DOCX and
stderr exactly as found and resume metadata-only reconciliation—never rerun the
inspector. The inspection record contains no document text, relationship
target, or unrestricted path.

After either mutually exclusive 38.321 branch completes, re-run the 38.300
inspector only against a private temporary extraction and assert its known
`embedded_active_content` refusal; do not alter retained files. Use this exact
macOS-safe temporary-root sequence. On macOS, `/var` is a symlink to
`/private/var`; the secure path walker correctly refuses symlinked parent
components, so pass only canonical `tmp_root` children to the inspector. Do not
change product code or weaken `O_NOFOLLOW` behavior.

```bash
set -euo pipefail
umask 077
tmp_root_raw="$(mktemp -d)"
tmp_root="$(realpath "$tmp_root_raw")"
current_tmp_raw="${TMPDIR:?TMPDIR must be set}"
current_tmp="$(realpath "$current_tmp_raw")"
test "$(/usr/bin/stat -f '%d:%i' "$tmp_root_raw")" = \
  "$(/usr/bin/stat -f '%d:%i' "$tmp_root")"
test -d "$tmp_root"
test ! -L "$tmp_root"
test "$(/usr/bin/stat -f '%Lp' "$tmp_root")" = 700
test "$(/usr/bin/stat -f '%Su' "$tmp_root")" = "$(id -un)"
test "$(dirname "$tmp_root")" = "$current_tmp"
set +e
inspection_out="$(.venv/bin/python -m specpilot.cli archive inspect \
  --archive artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip \
  --destination "$tmp_root/extracted" \
  --quarantine "$tmp_root/quarantine" \
  --expect-docx 38300-ia0.docx 2>"$tmp_root/inspection.stderr")"
inspection_exit=$?
set -e
test "$inspection_exit" -eq 2
test -z "$inspection_out"
test "$(wc -l < "$tmp_root/inspection.stderr" | tr -d ' ')" = 1
test "$(/bin/cat "$tmp_root/inspection.stderr")" = embedded_active_content
test -f "$tmp_root/extracted/38300-ia0.docx"
test ! -L "$tmp_root/extracted/38300-ia0.docx"
test "$(shasum -a 256 "$tmp_root/extracted/38300-ia0.docx" | awk '{print $1}')" = \
  c287873582310c0f609eab0ff2ee33634c4a2a1d10dd6ce6e74d7e96e2a83819
test "$(/usr/bin/stat -f '%d:%i' "$tmp_root_raw")" = \
  "$(/usr/bin/stat -f '%d:%i' "$tmp_root")"
test -d "$tmp_root"
test ! -L "$tmp_root"
test "$(/usr/bin/stat -f '%Lp' "$tmp_root")" = 700
test "$(/usr/bin/stat -f '%Su' "$tmp_root")" = "$(id -un)"
test "$(dirname "$tmp_root")" = "$current_tmp"
/bin/rm -rf -- "$tmp_root"
test ! -e "$tmp_root_raw"
test ! -L "$tmp_root_raw"
test ! -e "$tmp_root"
test ! -L "$tmp_root"
```

The sole recursive removal above is the already revalidated exact canonical
`tmp_root`, a direct child of the canonical current temporary directory; both
the raw `/var/...` spelling and canonical `/private/var/...` spelling must be
absent afterward.

- [x] **Step 4: Apply the author-fact gate and create manifests**

Use only author-confirmed `download_url` and `downloaded_at` values. The author
has confirmed these TS 38.300 facts (the timestamp was explicitly identified as
a filesystem-mtime observation before confirmation):

```text
download_url: https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/38300-ia0.zip
downloaded_at: 2026-08-06T20:23:31Z
```

For TS 38.321, `downloaded_at` remains pending: do not substitute the lost
original value or any filesystem mtime/ctime observation. After the recovery
branch captures a new exact value, keep both its printed `download_url` and
`downloaded_at` in the ignored implementer report and stop until the author
confirms those newly captured facts. Do not create either source manifest,
either public status, or a Task 3 commit before that confirmation. Once both
origins have confirmed facts, invoke `source-manifest create` with archive/DOCX
hashes even when inspection refused.

- [x] **Step 5: Verify the two-manifest invariant**

Use `ManifestStore.read_source` and assert exactly two files, document IDs
`3gpp-ts-38.300` and `3gpp-ts-38.321`, version `18.10.0`, live origin hashes,
`predecessor_manifest_id is None`, no assessment/route, and
`cloud_egress_authorized is False`. Verify restricted paths are ignored,
private, regular, and non-symlink.

- [x] **Step 6: Commit a sanitized origin checkpoint**

Create the public status with only URLs, versions, archive/DOCX hashes,
manifest IDs, inspection outcomes, `derivative: absent`, `successor_count: 0`,
`route_eligibility: extend`, and `task10_decision: not_recorded`. Include no
source text or private path.

```bash
git add -- docs/compliance/2026-08-07-task8-step4-evidence-status.md
git diff --cached --check
git commit -m "docs: record task8 frozen origin manifests"
```

---

### Task 4: Capture official and account evidence and build model-bound indexes

**Files:**
- Create restricted: dated snapshot, metadata, index, and account-evidence tree
- Update: `docs/compliance/2026-08-07-task8-step4-evidence-status.md`

**Interfaces:**
- Consumes: the 15 official HTTPS pages enumerated in the superseded evidence
  plan, plus the DeepSeek personal-account setting.
- Produces: two canonical evidence-index IDs with exact route/model bindings.

- [x] **Step 1: Create the dated tree safely**

Require `artifacts/restricted/compliance/2026-08-07` to be absent; an existing
tree is a separate replay/recovery case and must be inspected before continuing.
Create `snapshots`, `capture-metadata`, `evidence-indexes`, `account-evidence`,
and `author-input` with `0700`. Use `apply_patch` to create restricted helper
`capture-page` with this exact interface: three arguments
`capture_name requested_url output_path`; reject an unsafe name, non-HTTPS URL,
an output outside `snapshots/`, any existing/symlink output or metadata path,
an empty response, or an HTTPS downgrade. Its success path is:

```bash
umask 077
base=artifacts/restricted/compliance/2026-08-07
test ! -e "$base"
test ! -L "$base"
mkdir -p "$base/snapshots" "$base/capture-metadata" \
  "$base/evidence-indexes" "$base/account-evidence" "$base/author-input"
chmod 700 "$base" "$base/snapshots" "$base/capture-metadata" \
  "$base/evidence-indexes" "$base/account-evidence" "$base/author-input"
```

```sh
#!/bin/sh
set -eu
umask 077
test "$#" -eq 3 || exit 64
capture_name=$1
requested_url=$2
output_path=$3
case "$capture_name" in *[!a-zA-Z0-9._-]*|'') exit 64 ;; esac
case "$requested_url" in https://*) ;; *) exit 64 ;; esac
case "$output_path" in
  artifacts/restricted/compliance/2026-08-07/snapshots/*) ;;
  *) exit 64 ;;
esac
metadata_path="artifacts/restricted/compliance/2026-08-07/capture-metadata/${capture_name}.json"
test ! -e "$output_path" && test ! -L "$output_path"
test ! -e "$metadata_path" && test ! -L "$metadata_path"
output_tmp="$(mktemp "$(dirname "$output_path")/.capture.XXXXXX")"
metadata_tmp="$(mktemp "$(dirname "$metadata_path")/.capture.XXXXXX")"
trap 'rm -f -- "$output_tmp" "$metadata_tmp"' EXIT HUP INT TERM
effective_url="$(curl --fail --location --silent --show-error --compressed \
  --remove-on-error --user-agent 'SpecPilot-W0-evidence/1.0' \
  --write-out '%{url_effective}' --output "$output_tmp" "$requested_url")"
case "$effective_url" in https://*) ;; *) exit 1 ;; esac
test -s "$output_tmp"
captured_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sha256="$(shasum -a 256 "$output_tmp" | awk '{print $1}')"
byte_count="$(wc -c < "$output_tmp" | tr -d ' ')"
.venv/bin/python - "$metadata_tmp" "$capture_name" "$requested_url" \
  "$effective_url" "$captured_at" "$sha256" "$(basename "$output_path")" \
  "$byte_count" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "capture_name": sys.argv[2],
    "requested_url": sys.argv[3],
    "effective_url": sys.argv[4],
    "captured_at": sys.argv[5],
    "sha256": sys.argv[6],
    "output_name": sys.argv[7],
    "byte_count": int(sys.argv[8]),
}
path.write_text(
    json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    encoding="utf-8",
)
os.chmod(path, 0o600)
PY
chmod 600 "$output_tmp" "$metadata_tmp"
ln "$output_tmp" "$output_path"
if ! ln "$metadata_tmp" "$metadata_path"; then
  rm -f -- "$output_path"
  exit 1
fi
rm -f -- "$output_tmp" "$metadata_tmp"
trap - EXIT HUP INT TERM
```

Run `chmod 700 artifacts/restricted/compliance/2026-08-07/capture-page` and
`sh -n artifacts/restricted/compliance/2026-08-07/capture-page` before use.

- [x] **Step 2: Capture the official pages**

Capture exact response bytes and metadata for these URLs:

```text
https://www.3gpp.org/terms-of-use
https://www.3gpp.org/specifications-technologies/specifications-by-series
https://www.3gpp.org/specifications-technologies/specifications-by-series/file-name-conventions
https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/
https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/
https://www.etsi.org/resources/intellectual-property-rights/
https://www.etsi.org/terms/
https://api-docs.deepseek.com/
https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html
https://cdn.deepseek.com/policies/zh-CN/deepseek-terms-of-use.html
https://docs.chatanywhere.tech/doc-2694962
https://docs.chatanywhere.tech/doc-8793258
https://docs.chatanywhere.tech/doc-8793261
https://docs.chatanywhere.tech/doc-9081297
https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2
```

Revalidate effective HTTPS URLs and current page identity; do not substitute a
search result or third-party mirror.

Use capture names/output names exactly:

```text
3gpp-terms -> 3gpp-terms.html
3gpp-specifications-by-series -> 3gpp-specifications-by-series.html
3gpp-file-name-conventions -> 3gpp-file-name-conventions.html
3gpp-38.300-archive -> 3gpp-38.300-archive.html
3gpp-38.321-archive -> 3gpp-38.321-archive.html
etsi-ipr -> etsi-ipr.html
etsi-terms -> etsi-terms.html
deepseek-api-docs -> deepseek-api-docs.html
deepseek-privacy -> deepseek-privacy.html
deepseek-terms -> deepseek-terms.html
chatanywhere-models -> chatanywhere-models.html
chatanywhere-terms -> chatanywhere-terms.html
chatanywhere-privacy -> chatanywhere-privacy.html
chatanywhere-regions -> chatanywhere-regions.html
glm-5-2-official -> glm-5-2-official.html
```

Run all captures explicitly:

```bash
capture=artifacts/restricted/compliance/2026-08-07/capture-page
snapshots=artifacts/restricted/compliance/2026-08-07/snapshots
"$capture" 3gpp-terms https://www.3gpp.org/terms-of-use "$snapshots/3gpp-terms.html"
"$capture" 3gpp-specifications-by-series https://www.3gpp.org/specifications-technologies/specifications-by-series "$snapshots/3gpp-specifications-by-series.html"
"$capture" 3gpp-file-name-conventions https://www.3gpp.org/specifications-technologies/specifications-by-series/file-name-conventions "$snapshots/3gpp-file-name-conventions.html"
"$capture" 3gpp-38.300-archive https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/ "$snapshots/3gpp-38.300-archive.html"
"$capture" 3gpp-38.321-archive https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/ "$snapshots/3gpp-38.321-archive.html"
"$capture" etsi-ipr https://www.etsi.org/resources/intellectual-property-rights/ "$snapshots/etsi-ipr.html"
"$capture" etsi-terms https://www.etsi.org/terms/ "$snapshots/etsi-terms.html"
"$capture" deepseek-api-docs https://api-docs.deepseek.com/ "$snapshots/deepseek-api-docs.html"
"$capture" deepseek-privacy https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html "$snapshots/deepseek-privacy.html"
"$capture" deepseek-terms https://cdn.deepseek.com/policies/zh-CN/deepseek-terms-of-use.html "$snapshots/deepseek-terms.html"
"$capture" chatanywhere-models https://docs.chatanywhere.tech/doc-2694962 "$snapshots/chatanywhere-models.html"
"$capture" chatanywhere-terms https://docs.chatanywhere.tech/doc-8793258 "$snapshots/chatanywhere-terms.html"
"$capture" chatanywhere-privacy https://docs.chatanywhere.tech/doc-8793261 "$snapshots/chatanywhere-privacy.html"
"$capture" chatanywhere-regions https://docs.chatanywhere.tech/doc-9081297 "$snapshots/chatanywhere-regions.html"
"$capture" glm-5-2-official https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2 "$snapshots/glm-5-2-official.html"
test "$(find "$snapshots" -type f | wc -l | tr -d ' ')" = 15
test "$(find artifacts/restricted/compliance/2026-08-07/capture-metadata -type f | wc -l | tr -d ' ')" = 15
```

- [x] **Step 3: Capture the DeepSeek account gate with the browser skill**

Use the user's existing session and navigate only to the data-use/training
setting. Store no password, API key, account ID, balance, or unrelated content.
If visible, write a tightly cropped screenshot plus an observation record with
only `status=observed`, `setting_state`, `captured_at`, URL, and screenshot
SHA-256. If authentication or the setting blocks observation, write only
`status=not_captured`, a factual reason, time, and attempted HTTPS URL.
Canonicalize the JSON record and use that record's SHA-256—not the screenshot
hash—as the `deepseek-account-setting` evidence-index entry. The nested
`screenshot_sha256` keeps the screenshot bound without exposing it.

Use exactly these mutually exclusive paths:

```text
artifacts/restricted/compliance/2026-08-07/account-evidence/deepseek-training-setting.png
artifacts/restricted/compliance/2026-08-07/account-evidence/deepseek-training-setting.observation.json
artifacts/restricted/compliance/2026-08-07/account-evidence/deepseek-training-setting.blocked.json
```

Before capture, require all three paths absent and non-symlink. In the observed
branch, save the screenshot to a private temporary file, crop to the setting
only, hash it, publish it no-replace under the PNG path, then use `apply_patch`
to write the observation JSON and `chmod 600` both. In the blocked branch, do
not create a PNG; use `apply_patch` to write only the blocked JSON and
`chmod 600` it. Assert afterwards:

```bash
observed=artifacts/restricted/compliance/2026-08-07/account-evidence/deepseek-training-setting.observation.json
blocked=artifacts/restricted/compliance/2026-08-07/account-evidence/deepseek-training-setting.blocked.json
png=artifacts/restricted/compliance/2026-08-07/account-evidence/deepseek-training-setting.png
test -f "$observed" && test ! -e "$blocked" && test -f "$png" || \
  { test -f "$blocked" && test ! -e "$observed" && test ! -e "$png"; }
```

- [ ] **Step 4: Obtain the author-owned assessment prose**

Present only the exact captured URLs, hashes, times, page labels, and required
field names. The author must read the referenced evidence and provide their own
exact text for every `summary`, `retention_summary`, `training_summary`,
`region_summary`, `subprocessor_summary`, and `uncertainty` value. Do not supply
or prefill proposed paraphrases. Approval of the model slug or authorization
conclusion does not implicitly approve policy summaries.

Store the confirmed text as mode-`0600`
`artifacts/restricted/compliance/2026-08-07/author-input/assessment-text.json`
with exactly this schema:

```text
sources:
  3gpp-ts-38.300: summary string + non-empty uncertainty string array
  3gpp-ts-38.321: summary string + non-empty uncertainty string array
providers:
  deepseek: retention_summary + training_summary + region_summary
            + subprocessor_summary + non-empty uncertainty string array
  chatanywhere: retention_summary + training_summary + region_summary
                + subprocessor_summary + non-empty uncertainty string array
evidence_entries:
  each required kind: summary string + scope string
```

Every string is supplied by the author after reading the indexed pages. Do not
create this file until every value is available.

`evidence_entries` must contain exactly these 16 keys:

```text
3gpp-terms
3gpp-specifications-by-series
3gpp-file-name-conventions
3gpp-38.300-archive
3gpp-38.321-archive
etsi-ipr
etsi-terms
deepseek-api-docs
deepseek-privacy
deepseek-terms
deepseek-account-setting
chatanywhere-models
chatanywhere-terms
chatanywhere-privacy
chatanywhere-regions
glm-5-2-official
```

The DeepSeek index consumes the seven shared 3GPP/ETSI keys, the three
DeepSeek-page keys, and `deepseek-account-setting`. The ChatAnywhere index
consumes the seven shared keys, four ChatAnywhere-page keys, and
`glm-5-2-official`. No key is inferred or silently omitted.

- [ ] **Step 5: Build and validate both canonical indexes**

Each JSON object has exactly:

```text
schema_version: compliance-evidence-index/v1
route: {provider_id, endpoint_purpose, use}
model_slug: deepseek-v4-flash | glm-5.2
entries[]: {kind, url, captured_at, sha256, summary, scope}
```

Canonicalize with the shared helper, name each file by its canonical SHA-256,
and validate with `ComplianceEvidenceIndex`. The DeepSeek index must carry all
three required API-governing entries (`deepseek-api-docs`, `deepseek-privacy`,
`deepseek-terms`) with each page's own document hash, URL, and capture time,
because those are what the conclusion gate binds. It also includes the
account-record hash, URL, and capture time as optional context; the ChatAnywhere
index has neither. Apply the page-retention rule only after every index
validates.

The construction script must load every web URL/time/hash from its generated
metadata JSON and every summary/scope from the author-input JSON, sort entries
by `kind`, instantiate `ComplianceEvidenceIndex`, then publish bytes returned by
`canonical_json(index)` with `os.open(..., O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,
0o600)`. For account evidence, parse exactly one of the two records:

```python
observation_path = account_root / "deepseek-training-setting.observation.json"
blocked_path = account_root / "deepseek-training-setting.blocked.json"
if observation_path.exists() and not blocked_path.exists():
    account = DeepSeekAccountObservation.model_validate_json(
        observation_path.read_bytes()
    )
elif blocked_path.exists() and not observation_path.exists():
    account = DeepSeekAccountNotCaptured.model_validate_json(
        blocked_path.read_bytes()
    )
else:
    raise RuntimeError("account evidence state is invalid")
```

The `deepseek-account-setting` entry uses `canonical_sha256(account)`,
`account.url`, and `account.captured_at`. Either record kind is acceptable there
— it is context, not a gate, so neither state changes which assessments become
complete. After publication, securely reread each index, require
its filename stem to equal `canonical_sha256(parsed_index)`, and require
`canonical_json(parsed_index)` to equal the stored bytes.

- [ ] **Step 6: Update and commit the sanitized evidence checkpoint**

Add only the two evidence-index IDs, route/model identities, capture status
(`observed` or `not_captured` without the setting value), and remaining gate
state to the public status.

```bash
git add -- docs/compliance/2026-08-07-task8-step4-evidence-status.md
git diff --cached --check
git commit -m "docs: record task8 route evidence indexes"
```

---

### Task 5: Materialize and validate the four source-bound envelopes

**Files:**
- Create locally: four JSON files under
  `manifests/local/task8-step4/assessment-drafts/`
- Update: `docs/compliance/2026-08-07-task8-step4-evidence-status.md`

**Interfaces:**
- Consumes: two v1 manifests, two evidence indexes, confirmed author prose,
  account gate, default-v1 premise, and exact DeepSeek conclusion.
- Produces: four validated source-bound envelopes and no successor.

- [ ] **Step 1: Recompute outbound premise and policy hashes**

Compare every payload kind and numeric cap against `default-v1.json`. Compute
the exact premise and policy-file SHA-256 fresh. If they differ from reviewed
values `330e17c024b2da7e2b06563f12f039389b37f1862444a9b388520bfb65406c22`
and `ef19b1b0edd0344060ff0b8b46ab14987801157222f647cc5bce99940035fdd3`,
stop and revise the author assessment before writing envelopes.

The reviewed premise is exactly this single line:

```text
Under egress-policy/v1 default-v1, evidence, compliance, and verifier stages may contain only l1_query, l2_design, or l2_atomic_claim payloads, and the judge stage may contain only a judge payload. Each excerpt is limited to 1 excerpt, 512 model tokens, and 8192 UTF-8 bytes. L1 online unique use is limited to 5 excerpts, 2560 tokens, and 40960 bytes, with 10240 transmitted tokens and 163840 transmitted bytes. L2 online unique use is limited to 12 excerpts, 6144 tokens, and 98304 bytes; each atomic claim is limited to 4 excerpts, 2048 tokens, and 32768 bytes; L2 online transmitted use is limited to 24576 tokens and 393216 bytes. Judge unique use is limited to 5 excerpts, 2560 tokens, and 40960 bytes, with 5120 transmitted tokens and 81920 transmitted bytes. Evaluation-root unique limits are 10 excerpts, 5120 tokens, and 81920 bytes for L1 and 17 excerpts, 8704 tokens, and 139264 bytes for L2; transmitted limits are 15360 tokens and 245760 bytes for L1 and 29696 tokens and 475136 bytes for L2. Corpus-wide unique use is limited to 1024 excerpts, 524288 tokens, and 8388608 bytes. TOC limits are 12 nodes per call and 24 per run; L1 query input is limited to 1024 tokens, L2 design input to 2048 tokens, and one run to 3 L2 claims.
```

Hash the UTF-8 bytes without a trailing newline and require the reviewed
premise SHA-256 before use.

- [ ] **Step 2: Write the fixed four-file matrix**

Each file is a complete `source-bound-assessment/v1` envelope with explicit
source manifest, route, model, evidence index, and nested assessment. DeepSeek
uses `deepseek-v4-flash`; ChatAnywhere uses `glm-5.2`.

Use exactly these filenames:

```text
3gpp-ts-38.300-v18.10.0__deepseek__online-main-deepseek-v4-flash-api.json
3gpp-ts-38.321-v18.10.0__deepseek__online-main-deepseek-v4-flash-api.json
3gpp-ts-38.300-v18.10.0__chatanywhere__offline-judge-glm-5-2-api.json
3gpp-ts-38.321-v18.10.0__chatanywhere__offline-judge-glm-5-2-api.json
```

Build each object through `SourceBoundAssessment.model_validate`, serialize
with `canonical_json`, and publish with `O_EXCL|O_NOFOLLOW` mode `0600`.
Populate snapshots only from capture metadata and assessment prose only from
the author-input file; do not reconstruct either from filenames or prose.

Use this exact field mapping in the restricted builder:

```python
SOURCE_FILES = {
    "3gpp-ts-38.300": (
        "3gpp-ts-38.300-v18.10.0",
        "3GPP TS 38.300 version 18.10.0",
    ),
    "3gpp-ts-38.321": (
        "3gpp-ts-38.321-v18.10.0",
        "3GPP TS 38.321 version 18.10.0",
    ),
}
ROUTES = {
    "deepseek": {
        "route": TASK8_DEEPSEEK_ROUTE,
        "model_slug": "deepseek-v4-flash",
        "policy_kind": "deepseek-privacy",
    },
    "chatanywhere": {
        "route": TASK8_CHATANYWHERE_ROUTE,
        "model_slug": "glm-5.2",
        "policy_kind": "chatanywhere-privacy",
    },
}

terms_metadata = metadata["3gpp-terms"]
for document_id, (filename_prefix, document_label) in SOURCE_FILES.items():
    manifest = manifests_by_document_id[document_id]
    source_text = author_input["sources"][document_id]
    for provider_id, config in ROUTES.items():
        provider_text = author_input["providers"][provider_id]
        index_id, index = indexes_by_provider_id[provider_id]
        policy_metadata = metadata[config["policy_kind"]]
        conclusion = None
        if provider_id == "deepseek" and isinstance(
            account, DeepSeekAccountObservation
        ):
            conclusion = TASK8_DEEPSEEK_CONCLUSION
        assessment = ComplianceAssessmentDraft(
            source_terms=SourceTermsAssessment(
                terms_snapshot=EvidenceSnapshot(
                    snapshot_url=terms_metadata["effective_url"],
                    snapshot_sha256=terms_metadata["sha256"],
                    captured_at=terms_metadata["captured_at"],
                ),
                summary=source_text["summary"],
                uncertainty=tuple(source_text["uncertainty"]),
            ),
            provider_policy=ProviderPolicyAssessment(
                policy_snapshot=EvidenceSnapshot(
                    snapshot_url=policy_metadata["effective_url"],
                    snapshot_sha256=policy_metadata["sha256"],
                    captured_at=policy_metadata["captured_at"],
                ),
                retention_summary=provider_text["retention_summary"],
                training_summary=provider_text["training_summary"],
                region_summary=provider_text["region_summary"],
                subprocessor_summary=provider_text["subprocessor_summary"],
                uncertainty=tuple(provider_text["uncertainty"]),
            ),
            outbound_limit=OutboundLimitAssessment(
                premise=PREMISE,
                premise_sha256=PREMISE_SHA256,
            ),
            author_conclusion=conclusion,
        )
        envelope = SourceBoundAssessment(
            schema_version="source-bound-assessment/v1",
            source_manifest_id=manifest.manifest_id,
            route_binding=config["route"],
            model_slug=config["model_slug"],
            evidence_index_id=index_id,
            assessment=assessment,
        )
        validate_task8_source_bound_assessment(
            envelope,
            manifest_store=manifest_store,
            evidence_index=index,
            policy_evidence=(
                policy_records if provider_id == "deepseek" else ()
            ),
        )
        output_name = (
            f"{filename_prefix}__{provider_id}__"
            f"{config['route'].endpoint_purpose}.json"
        )
        publish_private_no_replace(output_root / output_name, canonical_json(envelope))
```

The restricted builder defines `PREMISE` as the exact reviewed line above,
loads manifests/indexes by parsing their canonical contents, validates the
author-input keys exactly, and implements `publish_private_no_replace` with
`O_EXCL|O_NOFOLLOW`, mode `0600`, file fsync, and directory fsync.

- [ ] **Step 3: Apply the policy-evidence/conclusion branch exactly**

If and only if the DeepSeek index hash-binds all three required API-governing
documents and none of them was captured after the conclusion's `authored_at`,
mechanically copy the exact conclusion into both DeepSeek nested assessments and
verify the 268-byte length and SHA-256. Otherwise omit it from both. Always omit
it from both ChatAnywhere assessments. The account record never decides this
branch.

- [ ] **Step 4: Validate every binding and completion state**

For each file, parse the envelope, load the index whose exact ID appears in the
envelope, and build the `ProviderPolicyEvidence` records from the same
capture-metadata that produced the index entries. Call
`validate_task8_source_bound_assessment` with `policy_evidence=policy_records`
only for the DeepSeek files and `()` for ChatAnywhere. Do not trust filenames.

In the fully bound branch, both DeepSeek nested assessments validate as complete
`ComplianceAssessment`; otherwise each fails only at `author_conclusion`. Both
ChatAnywhere nested assessments always fail complete validation only at that
field. Assert two initial manifests, zero successors, zero provider calls, no
Task 10 outputs, private modes, and Git-ignore coverage.

- [ ] **Step 5: Update and commit the sanitized result**

Record the actual branch without exposing the account setting: either
`deepseek_assessments: complete (2)` or `deepseek_assessments: unsigned (2)`;
always record `chatanywhere_assessments: unsigned (2)`, zero successors, no
derivative, no provider call, Task 10 incomplete, and eligibility `extend`.

```bash
git add -- docs/compliance/2026-08-07-task8-step4-evidence-status.md
git diff --cached --check
git commit -m "docs: record source-bound task8 assessments"
```

---

### Task 6: Align the local proposal and perform final verification

**Files:**
- Modify without staging: `SpecPilot_项目方案.md`
- Read only: all changed tracked files and restricted output identities

**Interfaces:**
- Consumes: completed Tasks 0–5.
- Produces: internally consistent local proposal and verified Task 10 boundary.

- [ ] **Step 1: Apply only the approved proposal edits**

Replace the five `gpt-5.6-luna` judge references with `glm-5.2` over the
ChatAnywhere API route. Preserve the different-model/human-blind-audit
rationale without claiming proven upstream-vendor independence. Align ingestion
with ADR 0001: originals with external relationships are refused; only a
separately reviewed derivative may remove an allowlisted template relationship.
Do not stage this file.

- [ ] **Step 2: Run focused and full verification**

```bash
.venv/bin/python -m pytest tests/unit/compliance \
  tests/unit/manifests tests/cli/test_manifest_commands.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
git diff --check
```

Expected: all non-database tests pass; environment-dependent skips are reported
rather than counted as evidence. Ruff, mypy, and whitespace checks exit 0.

- [ ] **Step 3: Verify the hard safety boundary**

```bash
test ! -e artifacts/public/w0-verification.json
test ! -e docs/reports/w0-foundation-report.md
test -z "$(git ls-files -- artifacts/restricted data/real data/quarantine manifests/local)"
test -z "$(git diff --cached --name-only)"
test "$(git status --short -- 'SpecPilot_项目方案.md' | cut -c1-2)" = '??'
```

Re-read all four envelopes through the validator, recompute every local content
ID, confirm zero successors/provider calls, and inspect the public status for
sensitive paths, excerpts, account values, credentials, or approval language.

- [ ] **Step 4: Run task and whole-branch reviews**

Every implementation task receives specification and quality review. The final
reviewer checks the complete range from the pre-plan base through HEAD,
including deferred findings and actual verification output. Resolve every
Critical or Important finding before branch completion.

## Scope 2 Handoff

This plan never starts Scope 2. The only allowed next action is route research:
try, in order, another 3GPP DOCX without embedded objects and an official ETSI
PDF or 3GPP HTML representation of the same specification. If either succeeds,
Route D remains forbidden. Only after both are recorded as failed, the corpus is
recorded as genuinely irreplaceable, and Route D is explicitly chosen may a
separately reviewed derivative implementation plan be written. Task 10 remains
`extend` until its independent successor, route-to-model binding, and real
synthetic-fixture provider-smoke gates pass.
