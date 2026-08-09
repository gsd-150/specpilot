# L1 Pooling Completeness Audit

**Date:** 2026-08-09  
**Scope:** all 20 current L1 annotation heads  
**Method:** §8.2.3, independent BM25 top-5 plus dense top-5 union  
**Run:** `603777f3379d3bab86bdf00bf4b1bef717300b83d634025e96ba3b9a4c06c750`  
**Seal:** `8300c410e82aa2e58b736ff8c523f90eaebe1914d52ffc6c0a853ad8184049e2`

## Result

The author adjudicated all 20 registered items. Nineteen existing Gold sets
were confirmed complete, one Gold set was extended by one clause, and no item
was blocked. The extension repaired the known consecutive-paragraph case: the
existing list paragraph was retained and the immediately preceding paragraph
that carries the governing obligation was added. No Gold clause was removed.

The five expected-refusal items remained without Gold. Pooling surfaced no
in-scope clause that answered them, so the audit did not manufacture evidence
for an unanswerable question.

| Measure | Result |
|---|---:|
| Registered L1 items | 20 |
| Frozen candidate union | 154 |
| `gold_complete` | 19 |
| `gold_extended` | 1 |
| `audit_blocked` | 0 |
| Added Gold clauses | 1 |
| Decision applications | 20 |
| Author review time recorded by the CLI | 1,285 s |
| L1 awaiting adjudication after seal | 0 |

## Frozen retrieval inputs

The collection found at the start of the work had 1,924 points, but none of
its unit identifiers matched the current 1,922-unit frozen corpus. It belonged
to an older unit-identity/splitting state and was retained unchanged. Before
the audit was registered, a new versioned collection was built from the current
corpus under `specpilot_ff4841e2d846388014efa06870fbbdb7`; encoding and writing
the 1,922 units took 52.612 seconds on MPS.

Registration then verified all of the following and refused on mismatch:

- vector width: 1,024;
- exact point count: 1,922;
- complete point-payload `unit_id` inventory;
- BGE-M3 weights SHA-256;
- source manifest identities;
- BM25 corpus fingerprint;
- candidate content hashes and per-route ranks.

The run stores the dense inventory SHA-256 rather than relying on point count
alone. Qdrant access also ignores host proxy settings so local corpus vectors
and inventory cannot be redirected through an egress proxy.

## Integrity checks

The final verification read every content-addressed run, decision,
application, annotation successor, and seal record back through its validating
store. It confirmed one successor per L1 head, a two-clause Gold set for the
extended item, route-specific provenance followed by human review, and no
blocked or unapplied decision. Restricted directories are mode `0700` and all
pooling JSON records are mode `0600`.

This audit closes L1 pooling completeness only. It does not add new locked
samples, complete the L1 40-item target, change L2 progress, or produce a
retrieval quality metric.
