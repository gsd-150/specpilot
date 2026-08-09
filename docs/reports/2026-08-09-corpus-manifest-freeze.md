# RFC corpus manifest freeze report

**Seal timestamp:** `2026-08-09T18:30:16Z`
**Status:** frozen, replayed without a second snapshot, and verified

## Bound state

| Evidence | Value |
|---|---|
| Source manifest: RFC 9110 | `af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691` |
| Source manifest: RFC 9112 | `3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2` |
| Pooling run | `603777f3379d3bab86bdf00bf4b1bef717300b83d634025e96ba3b9a4c06c750` |
| Pooling seal | `8300c410e82aa2e58b736ff8c523f90eaebe1914d52ffc6c0a853ad8184049e2` |
| Corpus manifest | `1abafff704358c2357ead5b837d212f130cadfa330dfa30d1df0a24f76d74295` |
| Collection | `specpilot_ff4841e2d846388014efa06870fbbdb7` |
| Point count | 1,922 |
| Vector schema | unnamed cosine float32, width 1,024 |
| BM25 fingerprint | `8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3` |
| Derived corpus SHA-256 | `46616bd050308f6f77782afe8706b8e2d8f577de9b9b698e228e1c52b40596eb` |
| Dense inventory root | `70bed824fc70871c49a1d350afa6d7e1fabc37c5a17f170d5db66c0b0cdfb19c` |
| Snapshot | `specpilot_ff4841e2d846388014efa06870fbbdb7-8882750702395667-2026-08-09-18-30-21.snapshot` |
| Snapshot checksum | `a84fb3ac7352c0f73a56978cb4945ea6ec54bae5528504d6581d005cb72ea1c0` |
| Snapshot size | 18,776,064 bytes |

Freeze reconstructed both source snapshots, reran every parse-QA line, and
passed the blocking QA gate before observing the live dense inventory. The
manifest binds the current parser, chunker, index-text and embedding-pipeline
versions, the local BGE-M3 weights hash, the retrieval protocol, the exact
Qdrant schema, and the complete payload/vector inventory root. No source prose,
vector, individual payload, individual inventory entry, model body, or snapshot
body is included here.

## Replay and refusal evidence

The first command returned `status="frozen"`. Repeating the same freeze with
the same explicit timestamp returned `status="replayed"`, the same manifest
and snapshot metadata, and left the snapshot count at 1 before and after. The
startup gate then returned `status="verified"` with the same collection, count,
inventory root, and snapshot binding.

Real Qdrant mutation tests use a distinct collection derived from a changed
synthetic RFC identity for every test. They prove that payload-only drift and
vector-only drift both refuse `dense_point_inventory_mismatch`, that a deleted
snapshot refuses `corpus_snapshot_mismatch`, and that publication permanently
revokes application writer leases. Test-only disaster-recovery administration
cleans up only each test's own collection and snapshots.

## Implementation and verification

The implementation sequence is recorded by commits `896df9b`, `4d610c3`,
`ba8bb51`, `aeccc00`, `c70d564`, `9acba5d`, `2b49fd5`, `c87cd4c`, `14fcba6`,
`762fd34`, `148b9e5`, `ea75f66`, `baa3154`, `6da2c93`, `419d36b`, `5b72284`,
`cfdcba7`, and `8b1f876`. Task 9 adds the live freeze tests and this completion
record.

- Ruff: all checks passed.
- mypy: no issues in 63 source files.
- Unit plus CLI: 1,152 passed.
- Full suite with live Qdrant: 1,179 passed, 25 skipped.
- Mandatory Qdrant integration: 17 passed, 0 skipped.

The 25 full-suite skips are the documented PostgreSQL integration cases whose
`SPECPILOT_TEST_DSN` was absent. Qdrant 1.12.4 was already healthy on
`127.0.0.1:6333` under the `colima` Docker context before Task 9 and remains
running; Task 9 started no PostgreSQL, API, MCP, or provider service and did not
change the pre-existing container-runtime state.

## Local-only artifacts

The manifest is stored below `manifests/local/r0/corpus`; source snapshots are
below `artifacts/restricted/sources/ietf`; model weights are below
`data/cache/models/bge-m3`; and the snapshot remains in Qdrant storage. All are
ignored by Git. The corpus-manifest store and lock directory are mode `0700`,
and the manifest record is mode `0600`.
