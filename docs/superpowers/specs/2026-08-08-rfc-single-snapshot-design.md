# RFC Single-Snapshot Integrity Design

## Problem

The RFC source path is currently reopened between manifest hashing, XML safety
inspection, identity extraction, and corpus processing. A local writer can
replace that path between reads. The command can therefore validate the frozen
bytes but build clauses, retrieval units, or annotation Gold checks from other
bytes carrying the same RFC number and publication date.

The same split exists inside `parse_verified`: `inspect_rfc_xml` securely reads
and parses one file description, then `Path.read_text` opens the path again.

## Goals

- Read one RFC source through one bounded `O_NOFOLLOW` file description.
- Compare the manifest hash before interpreting XML syntax or content.
- Perform XML safety checks, RFC identity extraction, and every downstream
  corpus operation over the same in-memory snapshot.
- Keep RFC publication version (`YYYY-MM`) separate from RFCXML grammar version.
- Refuse missing or unsupported RFCXML grammar versions instead of attempting a
  v3-specific parse over an unknown format.
- Preserve existing stable refusal codes and the rule that a refusal writes no
  annotation.

## Non-goals

- No network fetching, source-manifest mutation, or index persistence is added.
- No annotation question, Gold clause, verdict, or Verifier pair is generated.
- No parser generalization beyond the frozen RFCXML v3 corpus is attempted.
- The source manifest and frozen TXT/XML bytes are not rewritten.

## Options Considered

### 1. Immutable in-memory snapshot — selected

One secure bounded read produces a byte snapshot. Its hash is compared with the
manifest; only matching bytes are decoded and safety-parsed. The resulting
verified snapshot carries metadata and a parsed root, both excluded from logs
and serialization. Corpus and annotation consumers receive that object instead
of reopening the path.

This has the smallest security state: no cleanup, no second filesystem lookup,
and one source of truth for hash, identity, and content.

### 2. Private temporary-file snapshot

Copy verified bytes to a mode-0600 temporary file and pass its path through the
existing APIs. This reduces signature changes, but introduces publication,
cleanup, disk-capacity, and crash-lifetime concerns while still relying on path
lookups. It is rejected.

### 3. Rehash after processing

Keep reopening the original path and compare its hash again before output or
storage. A writer can replace the file, let unmanifested bytes be processed,
then restore the original before the final hash. It does not establish which
bytes produced the result and is rejected.

## Architecture

`specpilot.ingestion.rfc` owns two explicit phases:

1. `read_rfc_snapshot(path, limits) -> RfcByteSnapshot` opens with
   `O_NOFOLLOW`, requires a regular file, enforces the byte cap, reads once,
   and records the SHA-256. It does not decode or parse XML.
2. `verify_rfc_snapshot(snapshot) -> VerifiedRfc` performs strict UTF-8 decode,
   hostile-prologue checks, defused XML parsing, root validation, and RFCXML
   grammar validation. `version="3"` is required; missing or other values raise
   `UnsafeRfcError` with `unsupported_rfcxml_version`.

`load_verified_rfc(path, limits)` composes both phases for standalone callers.
`inspect_rfc_xml` remains metadata-only by returning the verified snapshot's
inspection. The raw bytes and XML root are `repr=False` and never enter CLI
payloads.

`parse_verified`, `extract_structure`, clause/table builders, parse QA,
index-unit construction, and `LocalCorpus.load` accept either a `Path` or an
already `VerifiedRfc`. Path callers retain their current behavior; a verified
caller never reopens the filesystem.

The CLI's `_frozen_source` returns a private pair of the stored manifest and its
`VerifiedRfc`:

1. resolve the manifest;
2. create one bounded byte snapshot;
3. compare `snapshot.document_sha256` with `manifest.xml_sha256`;
4. only on equality, safety-verify and parse that snapshot;
5. compare XML RFC number/publication month with manifest identity;
6. pass the same verified object to the selected command.

Regular-file and byte-cap errors necessarily occur while obtaining the
snapshot. For any readable bounded snapshot, `document_hash_mismatch` remains
higher priority than XML safety, grammar-version, or identity errors.

## Error Handling

- Wrong hash: `document_hash_mismatch`.
- Unsafe XML: retain the existing `RfcRejectionCode` value.
- Missing or unsupported root RFCXML `version`: `unsupported_rfcxml_version`.
- Missing/invalid RFC number or unique publication date:
  `invalid_document_identity`.
- Manifest/XML identity disagreement: `document_id_mismatch` or
  `document_version_mismatch`.
- Annotation record/manifest version disagreement:
  `document_version_mismatch` before clause lookup or storage.

No exception message or source text is emitted by the CLI.

## Testing

- A CLI regression swaps the source path immediately after the one secure read;
  parsing and clause counts must still come from the manifest-matched snapshot.
- A Gold-validation regression performs the same swap and proves no replacement
  clause can be accepted or stored.
- Hash mismatch plus hostile/unsupported/identity-invalid XML still reports
  `document_hash_mismatch` for a bounded regular snapshot.
- Missing and non-`3` RFCXML grammar versions are rejected directly and through
  the CLI when their bytes match the manifest.
- Existing symlink, size, DTD/entity, malformed XML, manifest mismatch,
  publication-version, clause/table/index/local propagation, and annotation
  no-write tests remain green.
- Full pytest, Ruff, mypy, both frozen RFC parse smokes, overlap measurement,
  and source-aware temporary annotation add are rerun before completion.

## Migration

Publication-version unit IDs already migrate from the erroneous RFCXML grammar
version to `2022-06`; the RFC 9112 §6.3 clause remains
`817e50534fc9d2e00b485d0d445b95992b1fdc25ef354febcd87bfc1be60e7bb`.
No official annotation exists, so no annotation successor is required. Any
local BM25 or Qdrant index containing the old IDs must be rebuilt; new and old
point IDs must not be mixed.
