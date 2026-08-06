# ADR 0001: Quarantine and provider transport are hard boundaries

**Status:** accepted, W0
**Date:** 2026-08-07

## Context

SpecPilot reads third-party specification documents and sends bounded excerpts
of them to a cloud model. Two things can go wrong that no amount of downstream
care will fix:

1. A hostile or malformed document reaches a parser, a shell, or a viewer.
2. More source text leaves the machine than the compliance premise assumed.

Both failures are silent by default. A macro-bearing DOCX parses fine until it
doesn't; an over-broad excerpt produces a perfectly good answer. Neither shows
up in evaluation quality numbers, which is exactly why they cannot be managed by
looking at results.

## Decision

Both are enforced as boundaries with no bypass, not as checks that callers are
expected to remember.

### Ingestion: reject and quarantine, never sanitize in place

The outer ZIP is preflighted whole before any member is opened. Exactly one
member with the expected name is accepted; traversal, absolute paths, symlinks,
special files, encryption, nested archives, and declared-size lies are refused.
Extraction streams with its own byte ceiling, so a lying header cannot become a
zip bomb.

OOXML inspection uses `defusedxml` with DTD, entity, and external resolution all
disabled, and refuses macros, active content, embedded objects, nested packages,
and any external relationship.

A refused archive goes to a content-addressed quarantine with a rejection code
and no file contents. **A rejected input is never repaired into an accepted
one.** A future sanitizer may produce a separate derivative under its own
review; it may never silently promote the original.

### Egress: one gate, and it owns its own inputs

`PolicyBoundTransport.send` is the only path to a provider. It resolves an
adapter, prepares, reserves, sends once, and records the attempt, in that order.
Adapters are private and nothing returns one.

The enforcer owns the policy, the authorization clock, and the manifest
resolver. That last one matters more than it looks: a `SourceManifest` is
content-addressed, so a caller cannot forge one that is internally inconsistent
— but it can trivially construct a consistent one that says `authorized=true`
and was never written to the store. Content addressing proves integrity, not
provenance. The enforcer therefore resolves manifests by ID through a store it
holds, and compares.

Budgets are durable and atomic. `check_and_reserve` locks the corpus row and
then the evaluation-root row, re-runs the pure enforcer against stored state,
and writes both back in one transaction.

## Consequences

**Cap arithmetic is not reimplemented in SQL.** A second implementation would be
free to drift from the enforcer, and drift in that direction is a silently
raised ceiling. SQL constraints are defence in depth; the pure enforcer is the
authority.

**The corpus row serializes reservations across a whole corpus.** Deliberate.
The corpus scope is the outermost cap, and at tens of cases correctness beats
reservation throughput by a wide margin.

**Failure modes are asymmetric on purpose.** An unknown ledger state
(`ReservationAmbiguous`) blocks the send, because the alternative is spending a
budget nobody can measure. A send that provably happened but could not be
accounted for seals the run: `check_and_reserve` refuses a sealed run inside the
same transaction that checks every cap, rather than relying on a caller-side
check that a future call site can forget.

**A replay is not a retry.** A repeated idempotency key returns the stored
reservation and charges nothing further, because that request never reached a
provider. A new key is a real resend and is charged again. Conflating them
either double-spends budget on nothing or lets retries escape accounting.

**Authorization expires and cannot be revoked.** The manifest store is
create-only, so a decision runs out rather than being withdrawn. This is a real
limitation, accepted for W0 because `expires_at` is mandatory and short
horizons are cheap. If revocation becomes necessary, it needs a new decision
record type, not a mutable manifest.

## Alternatives rejected

**Sanitizing unsafe documents on ingest.** Turns a clear refusal into a
judgement call about whether the sanitizer was thorough, made under time
pressure by whoever hits it.

**Enforcing caps in application code with the ledger as an audit log.** Makes
every concurrent path a place where two callers can both pass the check. The
concurrency test found exactly this class of bug in an ordering that looked
fine.

**Per-case budgets only.** This was the original design and it was wrong. With
`evaluation_root_id` as the outermost scope, one clause reused across cases
counted afresh every time, and the per-case caps together permitted more
distinct source text than the specification contains. The corpus scope exists
because the outbound-limit premise is about the total, not the per-call number.
