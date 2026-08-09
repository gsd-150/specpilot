# Corpus Manifest Freeze and Verification Design

**Date:** 2026-08-09
**Status:** Approved
**Scope:** Complete W2 Task 6 for the existing RFC 9110/9112 corpus and its
current Qdrant collection.

## 1. Objective

Implement the immutable `corpus_manifest` required by product-plan section
6.4. A sealed manifest must identify the exact source snapshots, parsing and
indexing behavior, sparse and fusion configuration, dense collection, Qdrant
snapshot, and complete retrievable-unit inventory. Loading a sealed corpus must
fail closed when the live collection no longer matches the manifest.

The first production use of this implementation freezes the already-audited
RFC 9110/9112 corpus. It does not rebuild or replace that collection, rerun
pooling, mutate gold annotations, or publish restricted corpus content.

## 2. Chosen approach

Use a dedicated contract, create-only store, freeze service, and verification
service.

Two smaller and larger alternatives were rejected:

- A model plus a CLI serializer would create a manifest but would not make it
  authoritative at load time. Schema or inventory drift could still be served.
- A Qdrant deployment and credential redesign would provide stronger
  infrastructure-level separation, but Qdrant 1.12 has no per-collection ACL
  in this deployment and W2 Task 6 does not own secret rotation or service
  identity provisioning. The implementation therefore enforces capability
  separation at the SpecPilot application boundary while keeping Qdrant admin
  access as an explicit recovery authority.

## 3. Contract

`CorpusManifestDraft` and `CorpusManifest` are frozen Pydantic models with
`extra="forbid"`. The final manifest ID is the SHA-256 of canonical JSON with
the ID field excluded, matching the source-manifest content-addressing model.

The v1 contract binds:

- schema version and optional predecessor corpus-manifest ID;
- a unique tuple of source-manifest IDs, canonically ordered by each referenced
  manifest's `(document_id, document_version, manifest_id)`;
- parser, chunker, index-text, BM25-tokenizer, and embedding-pipeline versions;
- the embedding weights SHA-256 and dense vector width;
- BM25 `k1`, `b`, tokenizer version, and built-index fingerprint;
- baseline retrieval parameters: dense top-20, BM25 top-20, RRF `k=60`, final
  top-5, deduplication by the exact tuple `(corpus_manifest_id, document_id,
  clause_id, child_span)`, and stable ties by `(document_id,
  numeric_clause_path, child_start)`;
- the Qdrant collection name and a normalized collection schema;
- exact point count;
- the derived corpus SHA-256;
- the complete point/content inventory root;
- Qdrant snapshot name, checksum, and byte size;
- deterministic parse-QA evidence hashes for each source;
- creation time.

The Qdrant URL is deliberately excluded. It describes where a copy is hosted,
not what the corpus is. A deployment may move the same verified snapshot
without changing corpus identity.

### 3.1 Version authorities

Version fields are read from code constants rather than accepted as arbitrary
CLI strings. The initial authorities are:

- RFC XML parser contract: `rfcxml-v3/v1`;
- retrievable-unit chunker contract: `rfc-clause-table/v1`;
- index text: `IndexTextPolicy.version` (`index-text/v1`);
- BM25 tokenizer: `TOKENIZER_VERSION` (`bm25-rfc/v1`);
- embedding pipeline: `PIPELINE_VERSION` (`clause/v1`).

Changing the behavior governed by one of these constants requires incrementing
its version and produces a new corpus-manifest ID.

### 3.2 Normalized collection schema

The manifest records a typed, stable schema rather than Qdrant's entire
version-specific response object. It binds:

- one unnamed dense vector;
- vector size 1024 and cosine distance;
- vector datatype and quantization settings when present;
- effective HNSW construction/full-scan settings and the dense query-parameter
  contract;
- sparse-vector configuration (empty for this corpus);
- Qdrant payload-index schema;
- the exact locator payload contract and nullable fields.

Operational counters, optimizer progress, segment count, and collection status
are not schema and are not hashed. Snapshot checksum binds the actual stored
collection files, while the inventory root binds their logical corpus meaning.

## 4. Corpus identity and inventory

All units are ordered by `unit_id`. Duplicate local unit IDs, Qdrant point IDs,
or Qdrant payload `unit_id` values are hard failures.

Source documents are ordered by `(document_id, document_version, manifest_id)`
before `LocalCorpus` and BM25 are built. This makes CLI pair order irrelevant
while retaining RFC 9110 before RFC 9112 and therefore preserves the sealed
pooling run's BM25 fingerprint
`8506ccdede80489ab86f368208d97f4d62739bc5b72629a85a663c72d508c8d3`.

`derived_corpus_sha256` preserves the algorithm used to name the current real
collection:

```text
sha256("\n".join(unit_id + "\x1f" + sha256(indexed_text)))
```

This allows the existing collection
`specpilot_ff4841e2d846388014efa06870fbbdb7` to be verified rather than rebuilt.

The stronger inventory root is the SHA-256 of canonical JSON containing one
ordered entry per unit. Each entry contains:

- deterministic Qdrant point ID;
- unit ID;
- canonical locator-payload SHA-256;
- source-text SHA-256;
- indexed-text SHA-256;
- dense-vector SHA-256, calculated over the stored vector's 1,024 values packed
  in order as little-endian IEEE-754 float32 bytes.

A missing, named, non-1,024-dimensional, or non-finite vector is invalid and
cannot contribute an inventory hash.

Verification scrolls the complete Qdrant collection with payloads and vectors.
It requires every point ID and payload to equal the locally reconstructed unit,
and it recomputes every vector hash. The vector hash detects a live collection
whose embedding changed while its ID, payload, and count stayed constant. The
snapshot checksum binds the historical storage image; the inventory root binds
the live points and vectors to the exact source and indexed content without
placing source text or individual vectors in the manifest.

## 5. Secure storage

`CorpusManifestStore` is separate from the source `ManifestStore`. It reuses
the same secure filesystem primitives and invariants:

- directory mode 0700 and file mode 0600;
- regular-file, single-link, `O_NOFOLLOW` reads;
- canonical JSON only;
- create-only publication with fsync;
- byte-identical replay succeeds;
- a conflicting existing path or tampered record fails closed;
- unsupported schema versions are rejected explicitly.

Keeping the stores separate avoids broadening source-manifest version dispatch
and makes corpus-manifest enumeration and idempotent freeze lookup explicit.

The store is also the durable frozen-collection registry. A private lock
subdirectory holds one lock file named by the SHA-256 of the collection name.
Application writers acquire a shared write lease for their whole lifetime;
freeze acquires the exclusive lease. A write lease is refused whenever any
stored corpus manifest binds that collection.

## 6. Freeze workflow

`specpilot corpus freeze` accepts paired repeated source manifest/XML arguments,
the source- and corpus-manifest directories, model directory, Qdrant URL,
collection name, and an explicit RFC3339 creation timestamp.

Under the collection's exclusive inter-process filesystem lease, it performs
these steps:

1. Securely read and verify every source snapshot against its source manifest.
2. Load the real tokenizer and run every blocking parse-QA line for each source.
   Any failed or unmeasured line refuses freezing.
3. Rebuild `LocalCorpus` and BM25 from the verified in-memory snapshots.
4. Hash the actual embedding weights and derive all code-owned versions and
   retrieval parameters.
5. Derive the corpus hash and require the supplied collection name to equal the
   versioned name calculated from that hash and pipeline versions.
6. Open Qdrant as a read-only application handle and verify normalized schema,
   exact point count, point IDs, payloads, vectors, and inventory root.
7. If a stored manifest already binds this exact freeze intent, verify its
   snapshot is still listed with the same checksum and size, then return that
   manifest without creating another snapshot.
8. Otherwise create a real Qdrant snapshot and require non-empty name,
   checksum, and positive size.
9. Re-read schema, count, and the full payload/vector inventory. If any value
   changed across the snapshot boundary, refuse without publishing a manifest.
10. Create-only publish the manifest and return only identifiers, counts,
    hashes, and snapshot metadata. No corpus prose is emitted.

A crash after Qdrant creates a snapshot but before filesystem publication may
leave an unreferenced snapshot. The command never guesses that an old snapshot
matches the current corpus; a later successful run may create another snapshot.
This is a recoverable storage leak, not a false attestation. Successful replay
is idempotent.

The freeze intent is every manifest field except `manifest_id`, snapshot
metadata, and `created_at`. Snapshot metadata does not exist until the snapshot
is created, and `created_at` records the successful first seal rather than
changing corpus semantics. An explicitly supplied predecessor is part of the
intent and must already exist in the same secure store; the command never
chooses a predecessor implicitly.

If an otherwise matching stored manifest names a missing snapshot, or the
listed snapshot's checksum or size changed, replay fails closed. It does not
silently create a second non-successor manifest for the same intent. Recovery
requires an explicit successor whose predecessor is the affected manifest; the
successor creates and binds a new snapshot. This is distinct from the crash
case above, where no manifest was ever published and an unreferenced snapshot
is not authoritative.

## 7. Load-time verification

The verifier takes a corpus-manifest ID plus the local source/XML/model/Qdrant
locations. It:

1. securely reads and content-verifies the manifest;
2. requires the supplied source-manifest set to equal the bound set;
3. reconstructs corpus, derived hash, QA evidence, BM25 fingerprint, and
   embedding weight hash;
4. requires the bound Qdrant snapshot to exist with matching checksum and size;
5. rechecks collection name, normalized schema, exact count, point IDs,
   payloads, vectors, and inventory root;
6. returns a `VerifiedCorpus` containing the manifest, local corpus, BM25
   index, and a frozen dense handle.

No warning path exists. Unsupported versions, missing snapshots, and any
source, model, configuration, schema, count, point, payload, or content mismatch
are refusal conditions.

The CLI exposes the same path as `specpilot corpus verify` so operators and CI
can exercise the startup gate directly.

## 8. Read-only capability boundary

A read-only `DenseIndex` exposes no application mutation path. Mutable creation,
upsert, and drop move to a writer capability that can only be constructed while
holding a shared collection write lease from `CorpusManifestStore`. The lease
checks the durable manifest registry before construction and remains held for
the writer's lifetime. Freeze holds the exclusive lease from its first
collection check through manifest publication, so a pre-existing application
writer must finish before freeze and no new one can enter after publication.
Collection creation also no longer silently deletes an existing collection.
Serving receives only the read-only handle returned by verification.

All SpecPilot collection mutation entry points require that writer capability;
there is no public `open(..., frozen=False)` escape hatch. This removes write
authority from the SpecPilot ingestion/serving identity after seal and closes
the application-level freeze/publication race. Direct Qdrant administration
remains outside that identity and is treated as explicit disaster-recovery
authority. A later deployment hardening task may give serving Qdrant's global
read-only API key, but this design does not pretend that the current
unauthenticated local server provides per-collection ACLs.

## 9. Retrieval-protocol alignment

The manifest records the exact section 8.5.1 baseline, and this task aligns the
existing RRF implementation with that declaration rather than attesting to its
current `unit_id` tie-break fallback.

The v1 corpus uses whole clauses or whole tables, not child chunks. Its
canonical retrieval identity therefore maps `clause_id` to `unit_id`, uses a
null `child_span`, and uses `child_start=0`. `IndexUnit` gains the non-content
locator fields needed to calculate a numeric clause path and stable tie key;
this does not change unit IDs, source text, indexed text, embeddings, or the
existing collection. RRF must receive complete locator tie keys and fail when a
candidate lacks one. Equal scores then sort by document ID, numeric clause
path, and child start as the manifest states. Deduplication uses the full bound
tuple even though child-span duplicates cannot occur under chunker v1.

## 10. Failure reporting and privacy

CLI failure output remains one stable machine-readable code and no payload.
Codes distinguish invalid usage, source/QA/model/config mismatches, collection
schema/count/inventory mismatch, missing or changed snapshot, unsupported
manifest versions, and I/O failure. Exceptions and source text are never
printed.

The manifest contains IDs, hashes, versions, locator schema, counts, and
snapshot metadata only. Unit IDs, individual content hashes, vectors, source
text, and the full inventory remain local and are not committed to Git.

## 11. Test strategy

Implementation follows test-driven development.

Unit tests cover:

- frozen models, strict fields, canonical IDs, sorted unique sources, and
  successor binding;
- every bound-field change producing a different ID;
- deterministic corpus and inventory hashes and sensitivity to point, payload,
  source-text, indexed-text, and vector-only changes;
- create-only store replay, permission checks, canonical decoding, tamper and
  unsupported-version refusal;
- durable write-lease refusal after seal, freeze/writer exclusion, freeze
  idempotence, and snapshot binding;
- verification refusal for source, model, BM25, collection name, schema, count,
  inventory, and snapshot mismatches;
- read-only handles exposing no mutation surface and writer construction or
  mutation being refused after seal;
- exact section 8.5.1 deduplication and stable RRF tie ordering;
- CLI output containing no source text.

Qdrant integration tests cover snapshot creation/listing, exact record and
vector inventory, schema normalization, successful verification, refusal after
payload and vector-only mutations, and a writer blocked across the freeze
publication boundary. Missing or changed snapshot replay and crash-orphan
behavior are tested separately.

Final acceptance runs focused tests, lint, strict type checking, the full test
suite, and then freezes and verifies the real 1,922-point RFC corpus. The
resulting corpus-manifest ID and Qdrant snapshot metadata are recorded in a W2
completion report; restricted source and inventory data stay ignored.

## 12. Non-goals

- Re-embedding or rebuilding the current dense collection.
- Changing BM25 scoring, the RRF formula or `k`, chunking, gold, or the
  completed pooling decisions. This task only aligns RRF deduplication and
  stable tie ordering with the already-frozen baseline.
- Implementing W3 online retrieval orchestration or W5 `real-init` recovery.
- Restoring snapshots automatically.
- Claiming server-enforced per-collection ACLs that Qdrant 1.12 does not offer
  in the current deployment.
