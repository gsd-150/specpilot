# RFC Single-Snapshot Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every manifest-authorized RFC command hashes, verifies, and processes one immutable in-memory snapshot obtained through one bounded `O_NOFOLLOW` read.

**Architecture:** Split the RFC boundary into a byte-snapshot phase and a safety-verification phase. Thread the resulting `VerifiedRfc` object through structure extraction, corpus builders, retrieval, and annotation so CLI commands never reopen the user-supplied path after manifest comparison.

**Tech Stack:** Python 3.12+, `os.open`/`O_NOFOLLOW`, defusedxml, dataclasses, Pydantic, pytest, Ruff, mypy.

## Global Constraints

- Preserve the error order in the approved design: regular-file/size checks while reading, then manifest hash, then XML safety/grammar/identity checks.
- A readable bounded snapshot with the wrong hash must return `document_hash_mismatch` without interpreting XML.
- RFC publication version remains `YYYY-MM` from the unique direct `<front><date>`; RFCXML grammar version is a separate required root attribute with exact value `3`.
- No source bytes, XML roots, clause prose, or exception details may enter `repr`, CLI output, annotation records, or logs.
- Preserve the current ignored tmp drafts and the user's untracked `SpecPilot_项目方案.md`.
- Do not create an official annotation or persistent BM25/Qdrant index during verification.
- Existing document-version fixes in the dirty worktree are in scope and must not be reverted.

---

### Task 1: Split the RFC boundary into one-read snapshot phases

**Files:**
- Modify: `src/specpilot/contracts/rfc.py`
- Modify: `src/specpilot/ingestion/rfc.py`
- Modify: `tests/unit/rfc/test_rfc_boundary.py`

**Interfaces:**
- Produces: `RfcByteSnapshot`, `VerifiedRfc`, `RfcInput`, `read_rfc_snapshot`, `verify_rfc_snapshot`, `load_verified_rfc`, and `ensure_verified_rfc` in `specpilot.ingestion.rfc`.
- Preserves: `inspect_rfc_xml(path, limits) -> RfcInspection` as the metadata-only public inspection call.
- Produces: `RfcRejectionCode.UNSUPPORTED_RFCXML_VERSION` with value `unsupported_rfcxml_version`.

- [ ] **Step 1: Write failing boundary tests**

Add imports for the new calls and tests with these exact assertions:

```python
def test_a_byte_snapshot_holds_content_out_of_its_repr(workspace: Path) -> None:
    path = rfc_factory.write_safe(workspace)

    snapshot = read_rfc_snapshot(path, RfcLimits())

    assert snapshot.document_bytes == path.stat().st_size
    assert len(snapshot.document_sha256) == 64
    assert "Synthetic" not in repr(snapshot)


@pytest.mark.parametrize("version", [None, "4"], ids=("missing", "unsupported"))
def test_only_rfcxml_v3_is_supported(
    workspace: Path,
    version: str | None,
) -> None:
    xml = rfc_factory.SAFE_RFC_XML
    xml = (
        xml.replace(' version="3"', "")
        if version is None
        else xml.replace('version="3"', f'version="{version}"')
    )
    path = rfc_factory.write(workspace, "version.xml", xml)

    with pytest.raises(UnsafeRfcError) as raised:
        load_verified_rfc(path, RfcLimits())

    assert raised.value.code is RfcRejectionCode.UNSUPPORTED_RFCXML_VERSION
```

Also assert a verified snapshot's `repr` contains neither `Synthetic` nor an
`Element` serialization, and that `inspect_rfc_xml` still equals
`load_verified_rfc(...).inspection`.

- [ ] **Step 2: Run the boundary tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/unit/rfc/test_rfc_boundary.py
```

Expected: collection or assertion failure because the snapshot API and grammar
rejection code do not exist.

- [ ] **Step 3: Implement the byte and verified snapshots**

Add the enum member in `contracts/rfc.py`, then implement these shapes in
`ingestion/rfc.py`:

```python
from dataclasses import dataclass, field
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class RfcByteSnapshot:
    document_sha256: str
    document_bytes: int
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class VerifiedRfc:
    inspection: RfcInspection
    root: Element = field(repr=False)


RfcInput: TypeAlias = Path | VerifiedRfc
```

Move the current `_read_regular_file` result into `read_rfc_snapshot` and hash
those exact bytes there. Move decode, prologue checks, defused parsing, root
validation, and `root.get("version") == "3"` into `verify_rfc_snapshot`.
Compose them without another open:

```python
def load_verified_rfc(path: Path, limits: RfcLimits) -> VerifiedRfc:
    return verify_rfc_snapshot(read_rfc_snapshot(path, limits))


def ensure_verified_rfc(source: RfcInput, limits: RfcLimits) -> VerifiedRfc:
    return source if isinstance(source, VerifiedRfc) else load_verified_rfc(source, limits)


def inspect_rfc_xml(path: Path, limits: RfcLimits) -> RfcInspection:
    return load_verified_rfc(path, limits).inspection
```

Construct `RfcInspection.document_sha256` and `document_bytes` from the byte
snapshot and `root_tag` from the parsed root. Raise
`UnsafeRfcError(UNSUPPORTED_RFCXML_VERSION)` after root-tag validation and
before returning `VerifiedRfc`.

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/unit/rfc/test_rfc_boundary.py tests/smoke/test_rfc_pipeline.py
.venv/bin/ruff check src/specpilot/contracts/rfc.py src/specpilot/ingestion/rfc.py tests/unit/rfc/test_rfc_boundary.py
.venv/bin/mypy src
```

Expected: all selected tests and static checks pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/specpilot/contracts/rfc.py src/specpilot/ingestion/rfc.py tests/unit/rfc/test_rfc_boundary.py
git commit -m "fix: snapshot RFC bytes before verification"
```

---

### Task 2: Let every local corpus consumer reuse a verified snapshot

**Files:**
- Modify: `src/specpilot/corpus/walk.py`
- Modify: `src/specpilot/corpus/clauses.py`
- Modify: `src/specpilot/corpus/tables.py`
- Modify: `src/specpilot/corpus/indexable.py`
- Modify: `src/specpilot/corpus/qa.py`
- Modify: `src/specpilot/rfc/structure.py`
- Modify: `src/specpilot/retrieval/local.py`
- Modify: `tests/unit/corpus/test_clauses.py`
- Modify: `tests/unit/rfc/test_rfc_structure.py`
- Modify: `tests/unit/retrieval/test_hybrid.py`

**Interfaces:**
- Consumes: `RfcInput`, `VerifiedRfc`, and `ensure_verified_rfc` from Task 1.
- Preserves: every existing path-based caller and return shape.
- Adds: the same functions may receive `VerifiedRfc`, in which case no path is opened.

- [ ] **Step 1: Write failing reuse tests**

In `test_clauses.py`, load a snapshot, delete only the test's temporary source,
and prove clause building still succeeds:

```python
def test_clauses_can_be_built_after_the_snapshot_path_disappears(
    workspace: Path,
) -> None:
    path = rfc_factory.write(workspace, "snapshot.xml", MULTI_PARAGRAPH_XML)
    verified = load_verified_rfc(path, RfcLimits())
    path.unlink()

    clauses = build_clauses(verified, RfcLimits(), ClauseLimits())

    assert len(clauses) == 3
    assert {clause.document_version for clause in clauses} == {"2026-08"}
```

Add equivalent focused assertions for `extract_structure(verified, ...)` and
`LocalCorpus.load([(verified, ClauseLimits())], ...)`. These tests use only
pytest temporary files; no workspace source is deleted.

- [ ] **Step 2: Run the reuse tests and verify RED**

Run the three exact new node IDs. Expected: type/runtime failure because current
functions try to treat `VerifiedRfc` as a filesystem path.

- [ ] **Step 3: Thread `RfcInput` through local readers**

Change `parse_verified` to:

```python
def parse_verified(source: RfcInput, rfc_limits: RfcLimits) -> Element:
    return ensure_verified_rfc(source, rfc_limits).root
```

Replace each relevant `path: Path` annotation with `source: RfcInput` and pass
that value unchanged through clauses, tables, index units, parse QA, and local
corpus loading. In `extract_structure`, call `ensure_verified_rfc` once and use
both `verified.inspection.document_sha256` and `verified.root`; remove its direct
`Path.read_text`/defusedxml parse.

Do not clone or serialize the root. All consumers are read-only walks.

- [ ] **Step 4: Run Task 2 tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/unit/corpus tests/unit/rfc/test_rfc_structure.py tests/unit/retrieval
.venv/bin/ruff check src/specpilot/corpus src/specpilot/rfc/structure.py src/specpilot/retrieval/local.py tests/unit/corpus tests/unit/rfc/test_rfc_structure.py tests/unit/retrieval
.venv/bin/mypy src
```

Expected: all selected tests and static checks pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/specpilot/corpus src/specpilot/rfc/structure.py src/specpilot/retrieval/local.py tests/unit/corpus tests/unit/rfc/test_rfc_structure.py tests/unit/retrieval
git commit -m "refactor: reuse verified RFC snapshots"
```

---

### Task 3: Bind every frozen-source CLI command to one snapshot

**Files:**
- Modify: `src/specpilot/cli.py`
- Modify: `tests/cli/test_corpus_parse.py`
- Modify: `tests/cli/test_annotation_entry.py`

**Interfaces:**
- Consumes: `RfcByteSnapshot`, `VerifiedRfc`, `read_rfc_snapshot`, and `verify_rfc_snapshot` from Task 1.
- Produces: private `_FrozenRfcSource(manifest, document)` from `_frozen_source`.
- Preserves: all CLI payloads and existing refusal codes, adding only `unsupported_rfcxml_version` for hash-matched unsupported grammar.

- [ ] **Step 1: Write a failing corpus path-swap regression**

Add `monkeypatch` to a corpus-parse test. Wrap the CLI module's
`read_rfc_snapshot` so it reads the manifest-matched bytes, replaces the test
path with a valid same-identity XML containing one extra paragraph, and returns
the original snapshot. Assert `corpus parse` succeeds with the original literal
`clause_count`, proving later processing did not reopen the replacement.

```python
replacement_xml = rfc_factory.SAFE_RFC_XML.replace(
    '      <section anchor="scope"',
    '      <t>A replacement-only paragraph.</t>\n'
    '      <section anchor="scope"',
)
original_read = cli_module.read_rfc_snapshot

def read_then_replace(path: Path, limits: RfcLimits) -> RfcByteSnapshot:
    snapshot = original_read(path, limits)
    path.write_text(replacement_xml, encoding="utf-8")
    return snapshot

monkeypatch.setattr(cli_module, "read_rfc_snapshot", read_then_replace)

code = main(parse_arguments)
payload = json.loads(capsys.readouterr().out)
assert code == 0
assert payload["clause_count"] == 2
```

The expected count is the hand-inspected two paragraphs in `SAFE_RFC_XML`; do
not compute it with the production builder.

- [ ] **Step 2: Write a failing annotation path-swap regression**

Build a replacement fixture in a separate pytest path and obtain the ID of a
clause that exists only at its extra ordinal. Create an otherwise valid record
carrying that ID. During `_frozen_source`, swap the command's XML path after the
one read as above. Assert:

```python
replacement_path = rfc_factory.write(corpus, "replacement.xml", replacement_xml)
replacement_only = build_clauses(
    replacement_path,
    RfcLimits(),
    ClauseLimits(),
)[1]
record, arguments = gold_record(
    tmp_path,
    corpus,
    gold_clause_ids=[replacement_only.clause_id],
    gold_section_paths=[replacement_only.section_path],
)

assert code == 2
assert captured.out == ""
assert captured.err == "unknown_gold_clause\n"
assert not annotation_dir.exists()
```

This proves unmanifested replacement content cannot authorize a Gold clause or
reach annotation storage.

- [ ] **Step 3: Run both path-swap tests and verify RED**

Run the two new node IDs. Expected: current CLI either lacks
`read_rfc_snapshot` or reopens the replacement and reports replacement-derived
behavior.

- [ ] **Step 4: Implement the frozen-source carrier and update all callers**

Add:

```python
@dataclass(frozen=True, slots=True)
class _FrozenRfcSource:
    manifest: RfcSourceManifest
    document: VerifiedRfc
```

Refactor `_frozen_source` to read once, compare `snapshot.document_sha256`
before calling `verify_rfc_snapshot`, validate identity, and return the carrier.
Update corpus parse/clauses/QA/normative/overlap, Gold checking, retrieval
search, and embedding measure to use `source.manifest` for metadata and
`source.document` for every structure/corpus read.

No command may pass `arguments.xml` to a parser or corpus builder after
`_frozen_source` succeeds. Enforce that invariant with this scan:

```bash
rg -n "(build_clauses|iter_clause_texts|run_parse_qa|build_normative_index|LocalCorpus\.load|extract_structure)\(.*arguments\.xml" src/specpilot/cli.py
```

Expected: no matches.

- [ ] **Step 5: Add hash-priority and grammar CLI assertions**

Extend `test_corpus_parse.py` so hash-mismatched unsupported XML still returns
`document_hash_mismatch`, while a manifest whose hash matches missing/`4`
grammar versions returns `unsupported_rfcxml_version` with empty stdout.

- [ ] **Step 6: Run Task 3 tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/cli/test_corpus_parse.py tests/cli/test_annotation_entry.py
.venv/bin/ruff check src/specpilot/cli.py tests/cli/test_corpus_parse.py tests/cli/test_annotation_entry.py
.venv/bin/mypy src
```

Expected: all selected tests and static checks pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/specpilot/cli.py tests/cli/test_corpus_parse.py tests/cli/test_annotation_entry.py
git commit -m "fix: bind RFC commands to one snapshot"
```

---

### Task 4: Document, migrate, and verify the complete fix

**Files:**
- Modify: `docs/runbooks/w1-annotation.md`
- Verify only: `tmp/l2-dev-001.json`
- Verify only: `tmp/l2-dev-002.json`
- Verify only: `tmp/l2-dev-003.json`
- Verify only: `tmp/model-drafted-not-gold.rfc9112-6.3.json`

**Interfaces:**
- Consumes: completed snapshot boundary and CLI binding.
- Produces: user-facing refusal-code documentation and final evidence.

- [ ] **Step 1: Update the runbook refusal table**

Add `unsupported_rfcxml_version` with the cause “the hash-matched XML is not
RFCXML v3.” Retain the already-added publication identity/version rows.

- [ ] **Step 2: Run all automated verification**

Run:

```bash
git diff --check
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/python -m pytest -q
```

Expected: no diff errors, no Ruff or mypy findings, and the complete pytest
suite passes with only the repository's declared skips.

- [ ] **Step 3: Run both frozen RFC parse smokes**

Invoke `specpilot.cli.main` for RFC 9110 and RFC 9112 with their stored R0
manifest IDs and frozen XML paths. Assert exit zero, `document_version` exactly
`2022-06`, zero dangling cross-references, and no source prose in stdout.

- [ ] **Step 4: Revalidate the model-assisted non-Gold draft without persistence**

Validate `L2Annotation`, rerun `corpus overlap`, and run `annotation add` only
against a `TemporaryDirectory`. Expected: schema valid, Jaccard `0.2239`, Gold
ID `817e50534fc9d2e00b485d0d445b95992b1fdc25ef354febcd87bfc1be60e7bb`,
and temporary source-aware add exit zero.

Confirm the three `l2-dev-00*.json` files still fail for their empty question
and missing human Verifier-pair fields; do not fill them.

- [ ] **Step 5: Request a final read-only code review**

Give the reviewer the approved design, this plan, the base SHA, and the final
working-tree diff. Resolve every Critical/Important issue, then rerun Steps 2–4.

- [ ] **Step 6: Commit Task 4 documentation**

```bash
git add docs/runbooks/w1-annotation.md
git commit -m "docs: record RFC snapshot refusals"
```

- [ ] **Step 7: Report completion**

Report exact test counts, static-check results, real-corpus smoke results, the
new Gold ID and Jaccard, ignored-template status, index rebuild requirement,
commits created, and untouched user-owned files.
