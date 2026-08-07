# R0: RFC Corpus Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SpecPilot a frozen, hash-verified IETF RFC corpus with its own
source manifests, its own source-terms assessment, and a parse path that reads
cross-references as elements rather than recovering them from text — so that W1
can begin against a corpus the ingestion boundary actually accepts.

**Why this plan exists:** W0 recorded route `C`. Five 3GPP DOCX distributions
were measured and all carry embedded OLE and Visio objects, so the boundary
refuses them correctly. The corpus moves; the boundary does not.

**Architecture:** A new `specpilot.ingestion.rfc` boundary verifies an
already-fetched RFC document — byte caps, strict UTF-8, and a `defusedxml` parse
that refuses any DOCTYPE, entity declaration, external entity, or processing
instruction. `source-manifest/v2` describes a document that has no archive and
no DOCX. Everything provider-side is untouched.

**Tech Stack:** Python 3.12+, Pydantic 2, defusedxml, pytest, Hypothesis, Ruff,
mypy. No new runtime dependency.

## Global Constraints

- **The OOXML and archive boundary stays.** It is working, tested, and its
  refusal of the 3GPP corpus is the evidence that produced route `C`. Deleting
  it would erase a demonstrated capability and the reason for the route. No
  task in this plan removes, weakens, or bypasses it, and none of its limits
  are raised.
- `source-manifest/v1` stays byte-identical. Existing v1 manifests, their
  canonical bytes, and their IDs must be unchanged by every task here. The
  golden v1 byte/ID test is preserved and extended, never edited to fit v2.
- Everything provider-side is corpus independent and is not touched by this
  plan: the egress policy and caps, the ledger, the transport, the evidence
  indexes, the API-policy conclusion gate, and both provider assessments.
- The 3GPP source manifests and their source-terms assessments are retained as
  records of what was assessed. They are not deleted, edited, or repurposed.
- No successor manifest is created. RFC source manifests are default-deny like
  every other initial manifest, and this plan does not authorize egress.
- Real RFC text is never committed. The repository receives implementation,
  tests, synthetic fixtures, and sanitized records only.
- Restricted directories stay `0700` and restricted files `0600`, with
  no-replace publication and symlink refusal, exactly as W0 established.
- The frozen suite is **RFC 9110** (HTTP Semantics) and **RFC 9112** (HTTP/1.1),
  mirroring the 38.300/38.321 pairing of an overall description plus one
  specific layer. RFC 9111 is a deliberate extension point, not scope here.
- Both `.txt` and `.xml` are frozen per document. The XML is what the parser
  consumes; the text is the human-checkable rendition and is hash-bound so a
  reviewer can compare them.

## Measured starting facts

Captured 2026-08-07 during route research. Every number below is re-derived by
Task 4 rather than trusted from here.

| Document | Sections | Cross-doc section refs | XML `<section>` | XML `<xref>` |
|---|---|---|---|---|
| RFC 9110 | 291 | 86 | 305 | 2519 |
| RFC 9112 | 59 | 56 | — | — |

RFC 9110's XML carries zero DOCTYPE, entity declarations, external entities, and
stylesheet processing instructions. The boundary in Task 2 still refuses all
four, because "the documents we looked at had none" is not a property of the
format.

## File map locked for R0

- `src/specpilot/contracts/rfc.py` — RFC document identity and inspection result.
- `src/specpilot/contracts/manifests.py` — add `source-manifest/v2`; v1 untouched.
- `src/specpilot/manifests/store.py` — read and create v2 alongside v1.
- `src/specpilot/ingestion/rfc.py` — the RFC verification boundary.
- `src/specpilot/rfc/structure.py` — sections and cross-references from v3 XML.
- `tests/helpers/rfc_factory.py` — synthetic RFC XML fixtures, hostile and safe.
- `tests/unit/rfc/` — boundary, structure, and manifest v2 tests.
- `docs/compliance/rfc-source-terms.md` — BCP 78 assessment record.

---

### Task 1: `source-manifest/v2` for documents with no archive

**Files:**
- Modify: `src/specpilot/contracts/manifests.py`
- Modify: `src/specpilot/manifests/store.py`
- Test: `tests/unit/manifests/test_source_manifest_v2.py`
- Modify: `tests/unit/manifests/test_manifest_store.py`

**Interfaces:**
- Produces: `RfcSourceManifestDraft` / `RfcSourceManifest` with
  `schema_version: Literal["source-manifest/v2"]`, `document_id`,
  `document_version`, `download_url`, `text_sha256`, `xml_sha256`,
  `downloaded_at`, `created_at`, plus the same predecessor, authorization,
  assessment, and route-binding fields v1 carries.
- Preserves: every v1 byte, ID, and public method signature.

- [x] **Step 1: Write the failing v1-preservation and v2 tests**

Assert first that a golden v1 draft still produces its exact canonical bytes and
manifest ID. Then assert v2 rejects `archive_sha256` and `docx_sha256` as extra
fields, requires both `text_sha256` and `xml_sha256`, is default-deny, and gets
a different manifest ID from a v1 draft carrying otherwise identical values.

- [x] **Step 2: Run and verify RED**

Run: `.venv/bin/python -m pytest tests/unit/manifests -q`

Expected: the v2 contract does not exist; v1 tests still pass.

- [x] **Step 3: Implement v2 beside v1**

Do not generalize v1 into a shared base whose field order could shift v1's
canonical bytes. Define v2 separately and let the duplication stand — canonical
byte stability outranks DRY here.

- [x] **Step 4: Teach the store both versions**

`read_source` dispatches on the declared `schema_version`: v1 to the existing
path, v2 to the new contract, anything else still raises
`UnsupportedManifestVersionError` with its stable CLI code. Create-only,
no-replace, `0600`, fsync semantics are unchanged.

- [x] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest tests/unit/manifests tests/cli -q`,
`.venv/bin/ruff check .`, `.venv/bin/mypy src`.

---

### Task 2: The RFC verification boundary

**Files:**
- Create: `src/specpilot/contracts/rfc.py`
- Create: `src/specpilot/ingestion/rfc.py`
- Create: `tests/helpers/rfc_factory.py`
- Test: `tests/unit/rfc/test_rfc_boundary.py`

**Interfaces:**
- Produces: `inspect_rfc_xml(path: Path, limits: RfcLimits) -> RfcInspection`
  and stable `RfcRejectionCode` values.
- Consumes: an already-fetched file. Fetching is not part of the boundary.

- [x] **Step 1: Write failing hostile-input tests**

Parameterize over: a DOCTYPE declaration, an internal entity declaration, a
billion-laughs expansion, an external `SYSTEM` entity, an external parameter
entity, an `xml-stylesheet` processing instruction, a file exceeding
`max_bytes`, invalid UTF-8, and a well-formed but non-`<rfc>` root. Each must
raise with its own stable code and must not parse document content.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement the boundary**

Read within a byte cap before parsing. Decode as strict UTF-8. Parse with
`defusedxml` with entity resolution and external access disabled. Refuse every
construct above by inspecting the raw prologue as well as the parsed tree, so a
refusal never depends on the parser having been configured correctly. Record
document hash, byte count, and root element only — never document text.

- [x] **Step 4: Verify GREEN**

---

### Task 3: Sections and cross-references from v3 XML

**Files:**
- Create: `src/specpilot/rfc/structure.py`
- Test: `tests/unit/rfc/test_rfc_structure.py`

**Interfaces:**
- Produces: `extract_structure(inspection) -> RfcStructure` with ordered
  sections carrying anchor, number, and title, plus resolved cross-references
  separating intra-document from inter-document targets.

- [x] **Step 1: Write failing structure tests against synthetic fixtures**

Cover nested sections, an `<xref>` to a local anchor, an `<xref>` to another
RFC, a dangling anchor, and duplicate anchors. Cross-reference extraction is the
reason this corpus was chosen, so a dangling target must be reported, not
silently dropped.

- [x] **Step 2: Run and verify RED**

- [x] **Step 3: Implement extraction**

Walk the parsed tree only. Never re-read the file, never fetch a target, and
never resolve an external reference over the network.

- [x] **Step 4: Verify GREEN**

---

### Task 4: Freeze the suite and write the BCP 78 assessment

**Files:**
- Create locally: restricted RFC sources and their capture records
- Create locally: `manifests/local/r0/source/*.json`
- Create: `docs/compliance/rfc-source-terms.md`

**Interfaces:**
- Produces: exactly two default-deny `source-manifest/v2` records.
- Consumes: author-confirmed URLs and download times, and the Task 2 boundary.

- [x] **Step 1: Fetch and publish both documents privately**

`umask 077`, no-replace hard-link publication, `0600`, capture record carrying
`download_url`, `downloaded_at`, `text_sha256`, `xml_sha256`, and byte counts.

- [x] **Step 2: Run each XML through the Task 2 boundary and record the result**

- [x] **Step 3: Obtain the author's BCP 78 source-terms assessment**

The author reads the IETF Trust Legal Provisions and BCP 78 and writes the
summary and uncertainty entries. Tooling must never write, infer, paraphrase, or
upgrade that text — the same rule that governed the 3GPP assessment.

- [x] **Step 4: Create both v2 manifests and verify the invariant**

Two manifests, zero successors, zero authorized, private modes, Git-ignored.

- [x] **Step 5: Commit only the sanitized record**

---

### Task 5: Retarget the 3GPP-shaped tests and re-verify

**Files:**
- Modify: the DOCX-shaped test modules that describe corpus inputs
- Modify: `docs/roadmaps/2026-08-06-specpilot-master-roadmap.md`

- [ ] **Step 1: Separate boundary tests from corpus tests**

Thirteen test modules mention DOCX or archive shapes. Most of them —
`test_archive_limits`, `test_archive_preflight`, `test_ooxml_inspection`,
`test_quarantine`, `test_sandbox_worker`, `ooxml_factory` — test the retained
boundary and must not change. Only the modules that assert a *corpus* is
DOCX-shaped are retargeted. List which is which before editing anything.

- [ ] **Step 2: Retarget only the corpus-shaped assertions**

- [ ] **Step 3: Full verification**

Run the full suite with a PostgreSQL DSN, Ruff, mypy, and both smokes. Confirm
the OOXML boundary tests still pass unchanged and no cap was relaxed.

- [ ] **Step 4: Update the roadmap state and commit**

---

## Plan self-review record

- **Scope decision:** R0 covers corpus replacement only. Parsing QA, chunking,
  embeddings, retrieval, and evaluation stay in W1 and later.
- **Retention decision:** the archive and OOXML boundary is retained deliberately.
  It is the evidence for route `C` and a demonstrated capability; the corpus
  changed, the safety posture did not.
- **Type consistency:** the Task 2 boundary emits an inspection that Task 3
  consumes; Task 4 binds the document hashes into `source-manifest/v2`; nothing
  in this plan reaches the enforcer, ledger, or transport.
- **Placeholder scan:** every step names concrete behavior, files, and
  verification. The author-owned assessment in Task 4 Step 3 is marked as such
  and is not something tooling completes.
