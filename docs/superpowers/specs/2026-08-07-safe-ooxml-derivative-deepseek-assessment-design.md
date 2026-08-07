# Safe OOXML Derivative and DeepSeek Assessment Design

**Status:** user-approved design

**Date:** 2026-08-07

## Goal

Resume the blocked Task 8 Step 4 prerequisite without weakening SpecPilot's
fail-closed ingestion boundary. Produce separately reviewed, content-addressed
OOXML derivatives for the two fixed Release 18 sources, bind their provenance
in source-manifest/v2, finish the provider evidence, and add the author's
explicit DeepSeek conclusion to the two DeepSeek assessments only.

## Blocking evidence

The exact official TS 38.300 v18.10.0 outer archive was accepted, but its DOCX
was correctly refused with `embedded_active_content`. The two hashes are
retained execution evidence; the structural counts are preliminary local
observations that must be reproducibly recomputed and bound into the new
content-addressed analysis record before implementation relies on them:

- archive SHA-256:
  `cd99c7a86796046d906c73e52f375a4bcda1bec8003ca6f158adb2bb669f8145`;
- original DOCX SHA-256:
  `c287873582310c0f609eab0ff2ee33634c4a2a1d10dd6ce6e74d7e96e2a83819`;
- 119 `w:object` nodes, each with one OLE reference and one distinct static
  preview reference;
- 84 EMF previews and 35 WMF previews, with no missing preview target;
- 36 OLE parts, 94 OLE relationships, 25 package relationships, and one
  external `attachedTemplate` relationship.

This is a real policy violation, not a false positive. ADR 0001 remains
authoritative: the rejected original is never repaired or promoted. Any usable
artifact must be a new derivative with its own review and provenance.

## User-owned decisions

The author made these decisions explicitly:

1. Use the existing frozen DeepSeek route identifiers:
   `provider_id=deepseek` and
   `endpoint_purpose=online-main-deepseek-v4-flash-api`.
2. Interpret the supplied timestamps as Beijing time 2026-08-07 04:00 through
   2026-09-06 04:00, stored as `2026-08-06T20:00:00Z` through
   `2026-09-05T20:00:00Z`.
3. Apply the conclusion independently to both TS 38.300 v18.10.0 and
   TS 38.321 v18.10.0.
4. Leave both ChatAnywhere/glm-5.2 assessments without an
   `author_conclusion`.
5. Do not create an authorized successor manifest in this scope. Default deny
   remains active and Task 10's only eligible result remains `extend`.
6. If an embedded object lacks a verifiable static preview, refuse the whole
   source and publish no derivative.

The authorization statement is copied byte-for-byte into each selected
DeepSeek assessment. Only the separately confirmed route identifiers and UTC
timestamps differ from the author's initially supplied JSON:

```json
{
  "authorized": true,
  "authorization_statement": "基于截至2026-08-07记录的3GPP/ETSI官方条款、适用于本账户的服务商数据政策，以及default-v1强制出站限制，我授权仅将该冻结源文档的白名单字段和受限片段通过DeepSeek官方API路由用于SpecPilot在线主链处理。",
  "author_id": "chunxue",
  "provider_id": "deepseek",
  "endpoint_purpose": "online-main-deepseek-v4-flash-api",
  "authored_at": "2026-08-06T20:00:00Z",
  "expires_at": "2026-09-05T20:00:00Z"
}
```

The UTF-8 bytes of `authorization_statement` are exactly 268 bytes with
SHA-256
`b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215`.
Validation compares those bytes after parsing, not JSON lexical formatting;
leading or trailing whitespace is forbidden rather than silently accepted.

This remains the author's self-assessment. Schema validation and implementation
review do not constitute external approval or legal advice.

## Non-goals

- Do not weaken `inspect_docx`, add a bypass, or accept the rejected original.
- Do not build a general-purpose Office repair service.
- Do not execute or render OLE, Visio, package, EMF, or WMF payloads.
- Do not introduce a PDF/HTML ingestion path in this change.
- Do not send source content, excerpts, or real provider requests.
- Do not write a ChatAnywhere conclusion.
- Do not invoke `source-manifest authorize-successor`.
- Do not create Task 10's final verification JSON or foundation report.

## Architecture

### 1. Original-evidence boundary

The official ZIP and rejected DOCX remain immutable restricted evidence. The
flat retained DOCX is migrated without overwrite into a content-addressed
quarantine record. The record stores the source hashes, stable refusal code,
capture time, and safe counts only. It never stores document text, XML, external
target strings, or unrestricted paths.

All directories are mode `0700`; all files are mode `0600`. Every traversal is
component-by-component with no symlink following. One private bundle directory
contains the derivative and its derivation record. The implementation fsyncs
both files and that directory, then publishes the entire bundle using one
platform no-replace atomic directory operation (`renameat2(RENAME_NOREPLACE)`
on Linux or `renamex_np(RENAME_EXCL)` on macOS). That rename is the visibility
commit point. The implementation then fsyncs the parent for durability. If the
no-replace primitive is unavailable, derivation fails before producing visible
output. If parent fsync fails after the rename, the complete bundle may remain
visible in a `publication_durability_ambiguous` state; no v2 manifest is
created, and the bundle is never deleted speculatively.

### 2. Narrow derivation policy

`OoxmlDerivationPolicy` is an immutable, canonical policy object with its own
SHA-256. Version 1 allows exactly two transformations:

1. Remove an external `attachedTemplate` relationship and the unique matching
   `w:attachedTemplate` reference.
2. Replace a recognized embedded-object presentation with its already present
   static preview, then remove the OLE/package relationship and unreachable
   embedded payload.

Everything else fails closed. Macros, ActiveX, executables, encryption, invalid
ZIP/XML, unsafe member paths, unknown external relationships, unknown active
content, or unexpected package graphs are never sanitized.

### 3. OOXML graph analyzer

The analyzer snapshots the input through a securely opened descriptor and
applies the existing ZIP/member/size/XML limits before deriving a plan. It
parses only OPC metadata and the minimum WordprocessingML nodes needed to map
references. XML parsing uses `defusedxml` with DTD, entity, and external
resolution disabled.

A removable object must have all of these properties:

- exactly one `w:object` container;
- exactly one OLE or package `r:id` consumed by that container and defined in
  the owning source part's relationship part;
- exactly one distinct internal image relationship used as its preview;
- an existing regular preview member with an allowlisted image relationship
  type, bounded size, and matching format signature;
- no second inbound relationship to the embedded payload;
- no shared or ambiguous preview relationship;
- no target, relationship, content-type, or source-part mismatch.

The first implementation supports the observed EMF and WMF previews as opaque
restricted bytes. It verifies their signatures and bounds but never decodes or
renders them. Downstream W0 parsing must not invoke an image decoder. A future
UI/rendering path requires a separate image-safety decision.

### 4. Deterministic transformer

For every approved object, the transformer:

1. replaces the complete `w:object` with a newly constructed inert `w:pict`
   node from a reviewed constant template, preserving only the verified
   preview `r:id` and parsed numeric width/height geometry;
2. removes the `o:OLEObject` element and its relationship;
3. removes the now-unreachable embedded target and target relationship part;
4. removes the matching content-type override or an unused default;
5. keeps the pre-existing preview media member unchanged.

No VML subtree or free-form attribute is copied from the input. The constructed
node has an explicit QName/attribute allowlist and fixed namespace bindings.
Hyperlinks, alternate image sources, event or behavior attributes, unparsed
style fragments, foreign namespaces, and any element outside the fixed picture
template cause refusal. Geometry accepts only bounded numeric width and height;
all other presentation data is discarded.

Removal is calculated over the complete OPC relationship graph, not by path
pattern. The approved OLE/package `r:id` must be consumed only by the approved
XML node. Starting at its target, the analyzer computes the exact payload-owned
relationship closure. Any inbound edge from a retained part, any retained edge
to a proposed deletion, or any ambiguous/shared ownership causes refusal. Only
that closed set and its owned relationship parts may be deleted. A content-type
Override is removed only for a deleted part; a Default is removed only when its
extension was used by that deleted closure and no retained member uses it.

For the template case it removes only the approved relationship and its unique
`w:attachedTemplate` consumer. It does not fetch, resolve, or log the target.

The writer sorts normalized member names, uses ZIP timestamp
`1980-01-01T00:00:00`, fixed creator/permission bits, and one fixed compression
algorithm and level. Unchanged non-XML member bytes are copied exactly. Changed
XML uses a fixed namespace-prefix table, UTF-8 without BOM, LF newlines, one
fixed XML declaration, lexicographically ordered attributes, and policy-defined
child order. Both determinism runs start independently from the same immutable
original snapshot, never from the first derivative.

The implementation hash covers a canonical sorted inventory of the exact
sanitizer, graph-validator, serialization, policy, and CLI source-file bytes.
The dependency-lock hash covers the exact bytes of a new pinned derivative
runtime lock file, including Python and `defusedxml` identities. The same input,
policy, implementation inventory, and dependency lock must produce
byte-identical output.

### 5. Post-transform validation and atomic publication

No derivative is published until all checks pass:

- the existing `inspect_docx` accepts it;
- every internal relationship target exists and remains inside the package;
- every referenced `r:id` has exactly one relationship definition in its
  owning part;
- every retained part has one valid content type;
- no forbidden part, relationship, target mode, or active element remains;
- an exact normalized package diff contains only the pre-authorized XML-node,
  relationship, content-type, and closed-payload deletions described above;
- the normalized semantic inventory before and after is identical;
- a second derivation run produces the same bytes and SHA-256.

The exact normalized package diff is the authoritative preservation invariant:
all retained non-XML member bytes are identical, and canonical XML trees are
identical except for the enumerated allowlisted changes. The semantic inventory
is a bounded regression detector, not proof of full document equivalence. It
hashes ordered body paragraph text and boundaries, heading/style identity,
table-cell text and geometry, bookmarks, footnotes, endnotes, and internal
cross-reference identities. It does not claim coverage of rendered layout,
headers/footers, comments, text boxes, fields, numbering semantics, tracked
changes, content controls, equations, section properties, accessibility text,
or information that exists only inside an embedded payload. The inventory
contains hashes and counts, never prose.

### 6. Content-addressed derivation record

`ooxml-derivation/v1` records:

- original archive SHA-256;
- original DOCX SHA-256;
- derivative DOCX SHA-256;
- derivation-policy SHA-256;
- sanitizer implementation SHA-256;
- dependency-lock SHA-256;
- before/after semantic-inventory SHA-256 values, which must match;
- removed part/relationship/object counts;
- retained preview count and format counts;
- a content-free removal-inventory root;
- canonical derivation-record ID.

The derivation record contains no wall-clock field, so its bytes and ID are
functions only of the original, policy, implementation/dependency identities,
approved graph changes, and derivative. Execution time is recorded separately
in the initial v2 manifest's `created_at` and in the restricted execution
report; it cannot change bundle identity.

The record and derivative are the two files in the single content-addressed
bundle described above. Byte-identical replay of an existing complete bundle
is success after secure reread; any conflicting byte, partial bundle, hash
mismatch, or pre-existing non-directory/symlink target is refusal. Neither file
is individually visible before the bundle publication operation. Replay of an
exact bundle after ambiguous parent fsync revalidates both files and fsyncs the
parent before permitting initial-v2-manifest creation.

### 7. Source manifest v2

The existing source-manifest/v1 model, canonical JSON bytes, and IDs remain
unchanged and are protected by golden canonical-byte and manifest-ID fixtures.
Version 2 uses separate `SourceManifestV2Draft` and `SourceManifestV2` types; it
is not implemented by adding optional fields to v1. Its exact initial shape is:

- `schema_version`: literal `source-manifest/v2`;
- `document_id` and `document_version`: existing `Identifier` type;
- `download_url`: existing HTTPS-only URL type;
- `origin.archive_sha256` and `origin.docx_sha256`: lowercase SHA-256;
- `origin.downloaded_at`: RFC3339 timestamp;
- `processable_artifact.media_type`: literal
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`;
- `processable_artifact.sha256`: derivative DOCX SHA-256;
- `processable_artifact.derivation_record_id`: derivation-record SHA-256;
- `created_at`: RFC3339 timestamp;
- `predecessor_manifest_id`: literal null;
- `cloud_egress_authorized`: literal false;
- `compliance_assessment` and `provider_route_binding`: literal null;
- `manifest_id`: canonical hash of all preceding fields.

This scope implements only initial v2 creation and v2 reading. The existing
`source-manifest authorize-successor` command must reject a v2 predecessor with
a stable unsupported-version refusal; no v2 successor constructor or CLI path
is implemented here. `ManifestStore` gains explicit v2 initial create/read
methods rather than changing v1 `read_source` semantics. `EgressRequest`,
`SourceManifestResolver`, and `EgressPolicyEnforcer` remain v1-only and reject a
v2 ID with a stable default-deny unsupported-version result. A future v2
successor design must preserve the complete origin, derivative, and derivation
identity and must pass the source-binding checks below.

### 8. Source-bound assessment envelope

The existing `ComplianceAssessment` remains the four-section decision object,
but it does not identify a source. Every Task 8 file therefore uses a separate
`source-bound-assessment/v1` envelope with structured fields:

- `source_manifest_id`, naming one stored initial source-manifest/v2;
- `derivation_record_id`, which must equal the record named by that manifest;
- `route_binding`, containing `provider_id`, `endpoint_purpose`, and `use`;
- `model_slug`, exactly `deepseek-v4-flash` for DeepSeek or `glm-5.2` for
  ChatAnywhere and matched to the hash-bound evidence index;
- `assessment`, containing `source_terms`, `provider_policy`,
  `outbound_limit`, and an optional `author_conclusion` while it is a draft.

Envelope validation resolves the stored v2 manifest and derivation record,
checks both IDs and origin/derivative hashes, and checks that a present
conclusion names the exact envelope route. Source identity is never inferred
from a filename or prose summary. The two DeepSeek envelopes become complete
only when both have hash-bound `status=observed` personal-account evidence;
otherwise they remain drafts. The two ChatAnywhere envelopes always remain
valid drafts without a conclusion in this scope.

`model_slug` is an evidence binding, not an egress authorization. The current
manifest/enforcer still lacks a route-to-model allowlist, which is another
reason v2 remains non-authorizable in this scope.

Any future authorization command for v2 must accept only a complete bound
envelope and compare its structured IDs and route with the stored predecessor.
That future path is outside this scope; the current command rejects v2.

## Data flow

1. Freeze the official outer ZIP and verify its hash.
2. Extract and inspect the original DOCX. Preserve the mandatory refusal and
   content-addressed quarantine record.
3. Analyze the unsafe package into a content-free derivation plan.
4. Transform only the two allowlisted graph patterns into a private temporary
   output.
5. Reinspect, prove graph closure, compare semantic inventories, and rerun for
   determinism.
6. Atomically publish the derivative and derivation record.
7. Create one initial default-deny source-manifest/v2 per accepted derivative.
8. Capture the official 3GPP/ETSI, DeepSeek, ChatAnywhere, and GLM evidence and
   the applicable DeepSeek personal-account setting evidence.
9. Build four source-bound route-specific assessment envelopes. If the
   observed-account gate passes, complete only the two DeepSeek assessments
   with the user-owned conclusion above; otherwise leave them unsigned.
10. Publish the sanitized Task 8 status record. Leave successor count at zero
    and Task 10 incomplete with route eligibility `extend`.

TS 38.300 and TS 38.321 are independent. A successful derivative for one never
permits inference, partial completion, or authorization for the other.

## Failure model

The CLI returns one stable, non-sensitive code and empty standard output on
refusal. Required codes include:

- `unsupported_active_content`;
- `missing_static_preview`;
- `ambiguous_object_graph`;
- `unsafe_static_preview`;
- `relationship_graph_invalid`;
- `semantic_inventory_changed`;
- `derivative_inspection_failed`;
- `nondeterministic_derivative`;
- `derivation_not_published`;
- `publication_durability_ambiguous`.

A policy refusal exits 2, writes zero bytes to stdout, and writes exactly
`<stable_code>\n` to stderr. Normalized I/O failure exits 3 with `io_error\n`;
bad usage exits 4 with one stable usage code. No exception text is emitted.
Members, relationships, and findings are sorted before classification. Fault
precedence is: source/filesystem integrity; ZIP/member/limit safety; XML safety;
non-sanitizable active content; object/preview graph; transformation/OPC
closure; normalized package diff/semantic inventory; existing-inspector result;
determinism; publication. Within one class the lexicographically first stable
code wins, so ZIP or relationship ordering cannot change the result.

An analysis, transformation, validation, determinism, or pre-commit
bundle-publication failure leaves the original evidence unchanged and publishes
no derivative bundle or v2 manifest. A parent-fsync failure after the visibility
commit point may leave one complete but durability-ambiguous bundle; it never
publishes a v2 manifest until exact replay revalidates the bundle and fsyncs the
parent. If subsequent initial-v2-manifest creation fails, the validated
content-addressed bundle may remain unreferenced, but no manifest is published;
an exact replay may finish the manifest step. A later evidence or
assessment-stage failure may retain an already validated default-deny bundle
and initial v2 manifest, but publishes no completed DeepSeek assessment and no
successor. No failure records source text or a raw relationship target. If
account evidence cannot establish the policy/settings applicable to the
personal DeepSeek account, assessment generation stops before adding the
author's conclusion; it does not fabricate or infer an account state. The
account record must have `status=observed` and be included by exact hash in the
DeepSeek evidence index. A blocked or `not_captured` record leaves both
DeepSeek envelopes unsigned.

## Assessment completion boundary

Each document has a separate DeepSeek assessment envelope and a separate
ChatAnywhere assessment envelope. Sections 1–3 remain bound to their exact page
snapshots, evidence index, account evidence, and the verified `default-v1`
policy/premise hashes.
Each source summary identifies both the immutable official origin and the exact
processable derivative through its source-manifest/v2 and derivation-record
IDs; it never describes the derivative as an untouched official DOCX.

In the successful observed-account-evidence path, the two DeepSeek assessments
contain the exact author-provided conclusion in this design; otherwise both
remain unsigned. The two ChatAnywhere assessments contain no
`author_conclusion` and their nested assessment objects must fail complete
`ComplianceAssessment` validation only at that missing field. No tool may alter
the author's statement or infer a ChatAnywhere decision.

Writing a complete assessment does not authorize egress by itself. No
successor manifest is created, no provider transport is invoked, and no route
is reachable in this scope.

## Testing and review

### Unit and property tests

- TDD fixtures for OLE, embedded Visio/package, external template, missing or
  shared preview, duplicate/dangling `r:id`, stale content types, target
  traversal, nested target relationships, macro/ActiveX, malformed XML, and
  unsupported external relationships.
- Property tests varying ZIP ordering, timestamps, compression metadata, and
  relationship ordering while requiring identical output.
- Tests for copied VML hyperlinks/events/foreign namespaces/free-form style,
  outside inbound OPC edges, retained edges to deletion, and overly broad
  content-type Default removal; every case must refuse.
- Exact normalized package-diff tests proving no XML tree, retained member, or
  relationship changes outside the enumerated allowlist.
- Tests proving the original is byte-identical and no output exists after every
  pre-visibility derivation failure; post-commit durability, manifest, and
  evidence failures must retain only the explicitly allowed complete state.
- Tests proving no diagnostic includes document text, external targets, or
  unrestricted part names.
- Tests proving stable-code precedence and exact exit/stdout/stderr bytes are
  independent of member and relationship ordering.
- Tests for all-or-nothing no-replace bundle publication, fsync boundaries,
  byte-identical replay, conflicts, and unavailable platform primitives.
- v1 canonical-byte/manifest-ID regression tests and v2 origin/derivative/
  derivation invariants.
- Source-bound envelope tests proving an assessment cannot be swapped between
  documents, derivation records, routes, or model slugs.

### Integration tests

- Run the sanitizer in the existing unprivileged, no-network, read-only-input
  ingestion boundary with bounded CPU, memory, files, and PIDs.
- Confirm the derivative is accepted by the existing inspector and by the
  stronger OPC closure validator.
- Confirm two independent runs are byte-identical.
- Confirm the initial v2 manifest is provenance/storage-only and every current
  egress resolver/enforcer path rejects it default-deny.
- Apply the flow separately to the retained TS 38.300 input and the exact
  TS 38.321 v18.10.0 input. If either violates a non-allowlisted condition,
  stop that source rather than broadening policy.

### Assessment tests

- In the successful observed-account path, validate the two DeepSeek files as
  complete `ComplianceAssessment` objects; in blocked/not-captured paths,
  validate that both remain drafts missing only `author_conclusion`.
- Resolve each envelope's stored v2 manifest and derivation record and verify
  every structured binding and origin/derivative hash.
- Verify the statement is exactly 268 UTF-8 bytes with SHA-256
  `b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215`,
  and verify the author ID, normalized route IDs, and normalized timestamps.
- Verify the two ChatAnywhere files fail complete validation only because
  `author_conclusion` is absent.
- Verify `status=observed` account evidence is hash-bound before either
  DeepSeek conclusion is present; blocked or `not_captured` evidence must leave
  both unsigned.
- Verify every evidence hash/time and the `default-v1` premise/policy hash.
- Verify there are exactly two initial v2 manifests, zero successors, no real
  provider calls, and no source-bearing egress.
- Verify the existing authorization CLI rejects a v2 predecessor and that no
  v2 successor constructor/path exists.

### Review gates

Each implementation task receives an independent specification and quality
review. After all tasks, a whole-branch review and full verification run are
mandatory. Review confirms internal consistency and evidence binding; it does
not recharacterize the author's conclusion as external approval.

## Repository and plan impact

- Add focused derivation policy, analyzer/transformer, provenance contract,
  initial-v2 manifest support, source-bound assessment envelope, CLI, and tests.
- Extend the restricted evidence-index route identity with exact `model_slug`
  while preserving its canonical content addressing.
- Amend the earlier Task 8 assessment design and the evidence implementation
  plan's goal, architecture, global constraints, Task 1, Task 3, Task 4, and
  handoff. The earlier blanket prohibition on `author_conclusion` is superseded
  only for mechanically copying the exact author-provided conclusion into the
  two DeepSeek envelopes after every source, evidence, policy, and observed
  account gate passes. It continues to bind both ChatAnywhere envelopes and
  every incomplete/blocked path.
- Task 1 preserves the original refusal, creates and validates derivatives,
  and then creates v2 default-deny manifests. Task 3 validates two complete
  DeepSeek envelopes plus two unsigned ChatAnywhere envelopes only after the
  observed-account gate; otherwise Task 3 keeps all affected DeepSeek envelopes
  unsigned. Task 4 records the exact resulting split while still proving zero
  successors and `extend` only.
- Align the user-owned project proposal's ingestion paragraph with ADR 0001:
  external relationships are refused in originals; only a separately reviewed
  derivative may remove the allowlisted template relationship.
- Preserve the proposal as user-owned/untracked and never stage or commit it.
- Do not create or modify Task 10 final artifacts during this prerequisite.

## Accepted limitation

Static previews can omit information that exists only inside an embedded
Visio/OLE payload. W0 never executes or parses that payload, so exact visual or
semantic equivalence cannot be claimed. The derivative is accepted only when
the normalized text/table/navigation inventory is unchanged and every object
has a retained static preview. That inventory is only a bounded regression
detector; the exact normalized package diff is the enforceable preservation
check, and neither check proves visual equivalence to executable embedded
content. The sanitized status record must state this limitation explicitly.
