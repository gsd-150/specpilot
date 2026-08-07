# Safe OOXML Derivative and DeepSeek Assessment Design

**Status:** revised after review. Scope 1 is ready to execute. Scope 2 requires a
recorded route decision first.

**Date:** 2026-08-07 (revised 2026-08-07)

## What changed in this revision

The first draft treated one problem as one scope. Review found it was two, and
that only one of them is blocked:

1. **The compliance assessment is not blocked by the OOXML refusal.** The four
   assessment sections describe source terms and provider data policy. Neither
   depends on whether a DOCX parses. `source-manifest create` takes hashes as
   arguments and never requires the DOCX to pass inspection, so an initial
   source-manifest/v1 for each frozen original is creatable today. This is now
   Scope 1 and needs no sanitizer, no v2 schema, and no derivative.
2. **The derivative is a route decision, not an implementation detail.** The
   product plan already priced this risk and pre-registered a cheaper escape.
   The draft never mentioned it. That comparison is now section "Route decision"
   below and must be recorded before Scope 2 begins.

Three engineering reductions also follow from review and are folded in:
directory-bundle atomicity replaced by the existing link primitive and the
cross-environment byte-determinism promise downgraded to what the dependency
lock can actually support. Scope 1 also records the resolved ChatAnywhere
`glm-5.2` route and hash-bound evidence identity.

## Blocking evidence

The exact official TS 38.300 v18.10.0 outer archive was accepted, but its DOCX
was correctly refused with `embedded_active_content`. The two hashes are
retained execution evidence; the structural counts are preliminary local
observations that must be reproducibly recomputed and bound into a
content-addressed analysis record before any implementation relies on them:

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

---

# Scope 1 — Source-bound assessments on source-manifest/v1

Executable now. Nothing here waits on the OOXML question.

## User-owned decisions

The author made these decisions explicitly:

1. Use the frozen DeepSeek route identifiers: `provider_id=deepseek` and
   `endpoint_purpose=online-main-deepseek-v4-flash-api`.
2. Re-author the conclusion at `2026-08-07T14:44:00Z` through
   `2026-09-06T14:44:00Z` (Beijing time 2026-08-07 22:44 through
   2026-09-06 22:44). The original `2026-08-06T20:00:00Z` predated the evidence
   capture at `2026-08-07T05:41Z`, which the policy-evidence gate now refuses:
   a conclusion cannot rest on documents frozen after it was written. The
   statement bytes are unchanged.
3. Apply the conclusion independently to both TS 38.300 v18.10.0 and
   TS 38.321 v18.10.0.
4. Use the resolved ChatAnywhere judge route: `provider_id=chatanywhere`,
   `endpoint_purpose=offline-judge-glm-5-2-api`, `use=offline_judge`, and
   `model_slug=glm-5.2`.
5. Leave both ChatAnywhere assessments without an `author_conclusion`.
6. Do not create an authorized successor manifest in this scope. Default deny
   remains active and Task 10's only eligible result remains `extend`.

The authorization statement is copied byte-for-byte into each selected DeepSeek
assessment. Only the separately confirmed route identifiers and UTC timestamps
differ from the author's initially supplied JSON:

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

The UTF-8 bytes of `authorization_statement` are exactly 268 bytes with SHA-256
`b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215`. Both were
recomputed during review and match. Validation compares those bytes after
parsing, not JSON lexical formatting; leading or trailing whitespace is
forbidden rather than silently accepted.

This remains the author's self-assessment. Schema validation and implementation
review do not constitute external approval or legal advice. Only after every
evidence, policy, and observed-account gate passes may a tool mechanically copy
the exact confirmed DeepSeek conclusion. It may not write, infer, paraphrase,
alter, or upgrade that conclusion; source and policy prose remain text the
author supplied or confirmed.

## Resolved Scope 1 routes

| Route | `provider_id` | `endpoint_purpose` | `use` | `model_slug` |
| --- | --- | --- | --- | --- |
| DeepSeek main chain | `deepseek` | `online-main-deepseek-v4-flash-api` | `online_main` | `deepseek-v4-flash` |
| ChatAnywhere judge | `chatanywhere` | `offline-judge-glm-5-2-api` | `offline_judge` | `glm-5.2` |

## Source-bound assessment envelope

`ComplianceAssessment` is the four-section decision object, but it does not
identify a source. Every Task 8 file therefore uses a separate
`source-bound-assessment/v1` envelope with structured fields:

- `source_manifest_id`, naming one stored initial source manifest;
- `route_binding`, containing `provider_id`, `endpoint_purpose`, and `use`;
- `model_slug`, matched to the evidence index's canonical model slug;
- `evidence_index_id`, the canonical SHA-256 ID of that exact evidence index;
- `assessment`, containing `source_terms`, `provider_policy`, `outbound_limit`,
  and an optional `author_conclusion` while it is a draft.

In Scope 1 the manifest is a source-manifest/v1 and there is no derivation
record. Scope 2 adds a `derivation_record_id` field and the v2 binding; the
envelope schema is designed so that addition is additive.

Envelope validation resolves the stored canonical initial-v1 manifest by
`source_manifest_id`; that resolution verifies the origin hashes already bound
into the manifest ID. The envelope does not duplicate origin fields. Validation
also checks the canonical evidence-index ID, its route and model slug, and that
a present conclusion names the exact envelope route. Source, route, model, and
index identity are never inferred from a filename or prose summary. This stops
an assessment being swapped between documents, routes, models, or evidence.

`model_slug` is an evidence binding, not an egress authorization. The manifest
and enforcer still lack a route-to-model allowlist.

## Scope 1 gates

Exactly two DeepSeek envelopes become complete only when the evidence index
hash-binds every provider document that governs the authorized API route —
`deepseek-api-docs`, `deepseek-privacy`, and `deepseek-terms` — with each
entry's document hash, URL, and capture time matching a supplied
`ProviderPolicyEvidence` record, no required document captured after the
conclusion's `authored_at`, and every other gate passing. Otherwise both remain
unsigned. Generation never fabricates or infers a provider policy state.

The three required kinds do not all carry data-handling text. The captured
`deepseek-api-docs` page is the API quickstart: it establishes route and model
identity — that `deepseek-v4-flash` was a documented production slug at
`https://api.deepseek.com` when the conclusion was authored — and nothing about
retention, training, region, or subprocessors. It stays required because
authorizing a route means binding proof that the route existed as described;
its `scope` entry must say so plainly, and the data-handling premise rests on
`deepseek-terms` and `deepseek-privacy`.

The gate binds API-governing documents rather than an account toggle on
purpose. The conclusion authorizes an API route; a data-use switch inside a
provider's consumer chat product governs a different surface, so it cannot
carry that decision. `deepseek-account-setting` is therefore recorded as
optional context, and an `observed`, `blocked`, or `not_captured` record alone
neither completes nor blocks any envelope. Where the author does rely on such a
setting, the surface mismatch belongs in `uncertainty`.

The two ChatAnywhere envelopes always remain valid drafts without a conclusion
in this scope, and their nested assessment objects must fail complete
`ComplianceAssessment` validation only at that missing field.

Writing a complete assessment does not authorize egress. No successor manifest
is created, no provider transport is invoked, and no route becomes reachable.

## Scope 1 data flow

1. Create one initial default-deny source-manifest/v1 per frozen original, from
   the recorded archive and DOCX hashes. The DOCX refusal does not block this.
2. Capture the official 3GPP/ETSI, DeepSeek, and ChatAnywhere evidence plus the
   applicable DeepSeek personal-account setting evidence.
3. Build four source-bound, route-specific assessment envelopes.
4. If the observed-account gate passes, complete only the two DeepSeek
   assessments with the conclusion above; otherwise leave them unsigned.
5. Publish the sanitized Task 8 status record. Successor count stays zero and
   Task 10 stays incomplete with route eligibility `extend`.

Each source summary identifies the immutable official origin by hash. Where no
derivative exists it says so; it never describes a processed artifact as an
untouched official DOCX.

TS 38.300 and TS 38.321 are independent. Evidence or completion for one never
permits inference for the other.

## What Scope 1 still needs from the author

These are facts the tooling cannot supply and must not invent:

- `download_url` and `downloaded_at` for each frozen original.
- The `summary`, `retention_summary`, `training_summary`, `region_summary`,
  `subprocessor_summary`, and every `uncertainty` entry. These are the author's
  own paraphrase of pages the author read; a generated summary would make
  `author_id` name someone who did not form the view.
- The personal-account evidence capture.

---

# Scope 2 — Reviewed OOXML derivative

**Blocked on a recorded route decision.** Do not begin implementation until the
comparison below is written into the go/no-go record.

## Route decision

The refusal is correct and the corpus is unusable as-is. Four responses exist.

**Route C — switch corpus.** Product plan §3.2 pre-registered IETF RFC for this
situation and gave, among its reasons, that plain text or XML distribution
*省掉整套 OOXML 沙箱风险面* — it removes the entire OOXML sandbox risk surface.
That surface is now measured, not hypothetical: 119 embedded objects and an
external template relationship. The plan also warned that hitting this wall
without a pre-registered fallback would mean redoing corpus selection after W0
and wrecking the schedule, which is precisely why RFC was named in advance.
Cost: the telecom-specification narrative, and every L2 case is rebuilt.

**Cheaper variants of the same idea, untried.** Both take about an hour and both
keep the telecom narrative:

- a different 3GPP specification whose DOCX carries no embedded objects;
- the ETSI PDF or the 3GPP HTML rendering of the same specification.

The first draft excluded the second by non-goal (`Do not introduce a PDF/HTML
ingestion path`) without an argument. That exclusion is withdrawn: it may still
be the right call, but it has to be made on its merits and recorded.

**Route D — this document.** Build the derivative. It is a new subsystem — OPC
graph analyzer, deterministic transformer, provenance record, manifest schema
version, CLI, and their tests — comparable in size to all of W0, in week zero of
a seven-week project. It is the right answer only if the corpus is genuinely
irreplaceable and both cheaper variants have been tried and recorded as failing.

**One property worth stating before choosing.** The design retains the rejected
original as immutable restricted evidence. The dangerous bytes therefore stay on
the machine either way. What the derivative buys is that everything downstream —
parsers, and in later weeks a UI — only ever opens a file with no active
content. That is real defence in depth, and it is a smaller benefit than the
effort suggests. A member allowlist at parse time would capture much of it for a
fraction of the cost, at the price of a weaker boundary.

Scope 2 is written out below so the decision is made against a real design
rather than a guess at one. Choosing it changes nothing about W0's outcome:
Task 10 still records `extend`.

## Non-goals

- Do not weaken `inspect_docx`, add a bypass, or accept the rejected original.
- Do not build a general-purpose Office repair service.
- Do not execute, render, or decode OLE, Visio, package, EMF, or WMF payloads.
- Do not send source content, excerpts, or real provider requests.
- Do not invoke `source-manifest authorize-successor`.
- Do not create Task 10's final verification JSON or foundation report.

## 1. Original-evidence boundary and publication

The official ZIP and rejected DOCX remain immutable restricted evidence. The
flat retained DOCX is migrated without overwrite into a content-addressed
quarantine record storing source hashes, the stable refusal code, capture time,
and safe counts only. It never stores document text, XML, external target
strings, or unrestricted paths.

All directories are mode `0700`; all files are mode `0600`. Every traversal is
component-by-component with no symlink following.

**Publication uses the existing no-replace link primitive, not a directory
rename.** The first draft required an all-or-nothing bundle directory published
with `renameat2(RENAME_NOREPLACE)` or `renamex_np(RENAME_EXCL)`. Neither is
exposed by Python's `os` module — it has only `rename` and `renames` — so that
design forces a `ctypes` call into libc, and it drags in a
`publication_durability_ambiguous` state with its own state machine and tests.

None of that is necessary, because both artifacts are content-addressed:

1. Write the derivative to a private temporary file, fsync it, and publish it
   under its own SHA-256 with `os.link(..., follow_symlinks=False)`, which fails
   with `FileExistsError` rather than replacing. Fsync the directory.
2. Do the same for the derivation record, which names the derivative hash.

Publishing the derivative first makes partial visibility harmless. A derivative
with no record is unreferenced content-addressed garbage that no reader resolves.
A record is never visible before the derivative it names, so a reader can never
follow a record to a missing file. The record's arrival is the commit point, and
`ManifestStore` already implements and tests exactly this sequence.

Byte-identical replay of an existing artifact is success after secure reread.
Any conflicting byte, hash mismatch, or pre-existing non-regular target is
refusal.

## 2. Narrow derivation policy

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

If an embedded object lacks a verifiable static preview, refuse the whole source
and publish no derivative.

## 3. OOXML graph analyzer

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
UI or rendering path requires a separate image-safety decision.

## 4. Deterministic transformer

For every approved object, the transformer:

1. replaces the complete `w:object` with a newly constructed inert `w:pict`
   node from a reviewed constant template, preserving only the verified preview
   `r:id` and parsed numeric width/height geometry;
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
to a proposed deletion, or any ambiguous or shared ownership causes refusal.
Only that closed set and its owned relationship parts may be deleted. A
content-type Override is removed only for a deleted part; a Default is removed
only when its extension was used by that deleted closure and no retained member
uses it.

For the template case it removes only the approved relationship and its unique
`w:attachedTemplate` consumer. It does not fetch, resolve, or log the target.

The writer sorts normalized member names, uses ZIP timestamp
`1980-01-01T00:00:00`, fixed creator/permission bits, and one fixed compression
algorithm and level. Unchanged non-XML member bytes are copied exactly. Changed
XML uses a fixed namespace-prefix table, UTF-8 without BOM, LF newlines, one
fixed XML declaration, lexicographically ordered attributes, and policy-defined
child order.

### What determinism does and does not promise

Both determinism runs start independently from the same immutable original
snapshot, never from the first derivative, and must produce byte-identical
output **within one locked environment**.

The first draft promised more than the lock can support. The dependency-lock
hash covers Python and `defusedxml` identities, but DEFLATE output is produced
by zlib, whose version moves independently of both. Either pin the zlib identity
into the lock as well, or state the narrower claim. The narrower claim is
recommended and is what this revision adopts:

- **Promised:** two runs in the same locked environment produce identical bytes.
  This is what the tests check, and it is what catches nondeterminism introduced
  by dictionary ordering, timestamps, or ZIP member order.
- **Not promised:** that a different machine or a different zlib reproduces the
  same bytes.

Identity does not depend on the stronger claim. The derivation record binds the
derivative's SHA-256, so what was produced is pinned regardless of whether
anyone else can reproduce those exact bytes.

The implementation hash covers a canonical sorted inventory of the exact
sanitizer, graph-validator, serialization, policy, and CLI source-file bytes.

## 5. Post-transform validation

No derivative is published until all checks pass:

- the existing `inspect_docx` accepts it;
- every internal relationship target exists and remains inside the package;
- every referenced `r:id` has exactly one relationship definition in its owning
  part;
- every retained part has one valid content type;
- no forbidden part, relationship, target mode, or active element remains;
- an exact normalized package diff contains only the pre-authorized XML-node,
  relationship, content-type, and closed-payload deletions described above;
- the normalized semantic inventory before and after is identical;
- a second derivation run in the same environment produces the same bytes.

The exact normalized package diff is the authoritative preservation invariant:
all retained non-XML member bytes are identical, and canonical XML trees are
identical except for the enumerated allowlisted changes.

The semantic inventory is a bounded regression detector, not proof of full
document equivalence. It hashes ordered body paragraph text and boundaries,
heading and style identity, table-cell text and geometry, bookmarks, footnotes,
endnotes, and internal cross-reference identities. It does not claim coverage of
rendered layout, headers and footers, comments, text boxes, fields, numbering
semantics, tracked changes, content controls, equations, section properties,
accessibility text, or information that exists only inside an embedded payload.
The inventory contains hashes and counts, never prose.

## 6. Content-addressed derivation record

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

The record contains no wall-clock field, so its bytes and ID are functions only
of the original, policy, implementation and dependency identities, approved
graph changes, and derivative. Execution time is recorded separately in the
initial v2 manifest's `created_at` and in the restricted execution report; it
cannot change artifact identity.

## 7. Source manifest v2

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

`ManifestStore` gains explicit v2 initial create and read methods rather than
changing v1 `read_source` semantics. `EgressRequest`, `SourceManifestResolver`,
and `EgressPolicyEnforcer` remain v1-only and reject a v2 ID with a stable
default-deny unsupported-version result.

**A v2 manifest has no reader in this scope.** Every consumer rejects it and no
successor constructor exists, so the two v2 manifests are provenance archive
until a future scope adds a v2 authorization path. That is acceptable, but it
should be counted honestly when pricing Scope 2 rather than presented as
capability.

### The current unsupported-version refusal is wrong today

`source-manifest authorize-successor` must reject a v2 predecessor with a stable
unsupported-version code. It currently reports `manifest_not_found`, verified
during review against a v2-shaped file:

```
$ specpilot source-manifest authorize-successor --predecessor <v2-id> ...
manifest_not_found
exit=2
```

The file exists and is readable; only its schema version is unsupported.
Reporting "not found" sends a reader looking for a missing file. This fix is
small, independent of the route decision, and worth making either way.

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
- `derivation_not_published`.

`publication_durability_ambiguous` is removed along with the bundle directory it
described.

A policy refusal exits 2, writes zero bytes to stdout, and writes exactly
`<stable_code>\n` to stderr. Normalized I/O failure exits 3 with `io_error\n`;
bad usage exits 4 with one stable usage code. No exception text is emitted.
Members, relationships, and findings are sorted before classification. Fault
precedence is: source and filesystem integrity; ZIP/member/limit safety; XML
safety; non-sanitizable active content; object and preview graph;
transformation and OPC closure; normalized package diff and semantic inventory;
existing-inspector result; determinism; publication. Within one class the
lexicographically first stable code wins, so ZIP or relationship ordering cannot
change the result.

Any failure leaves the original evidence unchanged. A failure before the
derivative is linked publishes nothing. A failure between the derivative link
and the record link leaves an unreferenced content-addressed blob, which no
reader resolves and which an exact replay completes. A later manifest or
assessment failure may retain a validated derivative and record but publishes no
completed DeepSeek assessment and no successor. No failure records source text
or a raw relationship target.

## Testing and review

### Unit and property tests

- TDD fixtures for OLE, embedded Visio/package, external template, missing or
  shared preview, duplicate or dangling `r:id`, stale content types, target
  traversal, nested target relationships, macro/ActiveX, malformed XML, and
  unsupported external relationships.
- Property tests varying ZIP ordering, timestamps, compression metadata, and
  relationship ordering while requiring identical output.
- Tests for copied VML hyperlinks, events, foreign namespaces and free-form
  style; outside inbound OPC edges; retained edges to deletion; and overly broad
  content-type Default removal. Every case must refuse.
- Exact normalized package-diff tests proving no XML tree, retained member, or
  relationship changes outside the enumerated allowlist.
- Tests proving the original is byte-identical and no derivative link exists
  after every pre-publication failure.
- Tests proving no diagnostic includes document text, external targets, or
  unrestricted part names.
- Tests proving stable-code precedence and exact exit, stdout, and stderr bytes
  are independent of member and relationship ordering.
- Tests for no-replace link publication, fsync boundaries, byte-identical
  replay, conflicts, and the unreferenced-blob recovery path.
- v1 canonical-byte and manifest-ID regression tests, and v2 origin, derivative,
  and derivation invariants.

### Integration tests

- Run the sanitizer in the existing unprivileged, no-network, read-only-input
  ingestion boundary with bounded CPU, memory, files, and PIDs. That boundary is
  already built and verified by running it: no network, read-only root, read-only
  input mount, writable output only, uid 10002.
- Confirm the derivative is accepted by the existing inspector and by the
  stronger OPC closure validator.
- Confirm two runs in the same locked environment are byte-identical.
- Confirm the initial v2 manifest is provenance and storage only, and that every
  current egress resolver and enforcer path rejects it default-deny.
- Apply the flow separately to TS 38.300 and TS 38.321. If either violates a
  non-allowlisted condition, stop that source rather than broadening policy.

### Assessment tests (Scope 1)

- In the successful observed-account path, validate the two DeepSeek files as
  complete `ComplianceAssessment` objects; in blocked or not-captured paths,
  validate that both remain drafts missing only `author_conclusion`.
- Resolve each envelope's stored canonical initial-v1 manifest and verify every
  structured binding; the manifest ID resolution verifies its already-bound
  origin hashes without duplicating origin fields in the envelope.
- Verify the statement is exactly 268 UTF-8 bytes with SHA-256
  `b88021706a85f89dd98aa91e2233a404a1396f8f2a831fc33e6686f31fadc215`, and verify
  the author ID, normalized route IDs, and normalized timestamps.
- Verify the two ChatAnywhere files fail complete validation only because
  `author_conclusion` is absent.
- Verify every required API-governing document is hash-bound and was captured no
  later than `authored_at` before either DeepSeek conclusion is present.
- Verify every evidence hash and time, and the `default-v1` premise and policy
  hashes.
- Verify zero successors, no real provider calls, and no source-bearing egress.
- Verify the authorization CLI rejects an unsupported manifest version with the
  version-specific code rather than `manifest_not_found`.

### Review gates

Each implementation task receives an independent specification and quality
review. After all tasks, a whole-branch review and full verification run are
mandatory. Review confirms internal consistency and evidence binding; it does
not recharacterize the author's conclusion as external approval.

## Repository and plan impact

- Scope 1 adds the source-bound assessment envelope, its validation, and its
  tests. It touches no ingestion code.
- Scope 2 adds the derivation policy, analyzer and transformer, provenance
  contract, initial-v2 manifest support, CLI, and tests.
- Extend the restricted evidence-index canonical shape with the resolved exact
  `model_slug`, and bind each envelope to that index through
  `evidence_index_id`.
- Amend the earlier Task 8 assessment design and the evidence implementation
  plan. The earlier blanket prohibition on `author_conclusion` is superseded
  only for mechanically copying the exact author-provided conclusion into the
  two DeepSeek envelopes after every evidence, policy, and observed-account gate
  passes. It continues to bind both ChatAnywhere envelopes and every incomplete
  or blocked path.
- Align the product plan's ingestion paragraph with ADR 0001: external
  relationships are refused in originals; only a separately reviewed derivative
  may remove the allowlisted template relationship.
- The go/no-go runbook now carries the missing branch. A corpus that cannot be
  safely ingested is its own Route C trigger, separate from the compliance
  conclusion, and Route D exists with an explicit instruction to price it
  against C first.
- Preserve the product plan as user-owned and untracked; never stage or commit
  it.
- Do not create or modify Task 10 final artifacts during this prerequisite.

## Accepted limitation

Static previews can omit information that exists only inside an embedded Visio
or OLE payload. W0 never executes or parses that payload, so exact visual or
semantic equivalence cannot be claimed. The derivative is accepted only when the
normalized text, table, and navigation inventory is unchanged and every object
has a retained static preview. That inventory is only a bounded regression
detector; the exact normalized package diff is the enforceable preservation
check, and neither proves visual equivalence to executable embedded content. The
sanitized status record must state this limitation explicitly.
