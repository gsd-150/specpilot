# Task 7 implementation report

## Outcome

Implemented optional local provider-response caching at base
`70f6c6f158dd98636a2c2b2dbe43afff3cdb4f78` without creating a policy or
ledger bypass.

- `CacheKey` binds provider/model, stage, validated projected payload,
  configuration and prompt identities, source and corpus manifests, and the
  active egress-policy hash. The API runtime uses its one configured prompt
  ID/hash consistently while stage remains an independent key dimension.
- `LocalResponseCache` stores canonical content-addressed records below a
  descriptor-validated `0700` root. Records and hashed run/session index
  markers are atomic `0600` files opened with `O_NOFOLLOW`; TTL expiry,
  corruption, namespace drift, and cache I/O faults fail closed with stable
  codes that contain no paths or response content.
- Every transport request resolves its adapter and runs `enforcer.prepare`
  before cache lookup. Hits make no ledger reservation and do not call the
  adapter. Misses preserve prepare/reserve/single-send/attempt accounting and
  publish only after the successful attempt is recorded.
- Cache identity is propagated through planning, L1 answering, compliance,
  semantic verification, worker audit, and L2 checkpoints. Hits never invent
  reservation IDs, are excluded from checkpoint `reservation_ids`, and emit a
  closed `CacheSummaryEvent` instead of an admitted `EgressSummaryEvent`.
- Migration 016 admits only the exact cache-summary shape. The browser's
  strict event decoder and safe timeline projection were updated in the same
  change so a valid cache-hit SSE frame cannot be rejected at the public
  boundary.
- Real runtime defaults remain disabled. Enabling requires both an absolute
  cache directory and a positive TTL. Retention CLI commands delete by hashed
  run/session index or expiry and output only a deleted-record count.

## RED evidence

- Filesystem/key tests initially failed because `specpilot.providers.cache`
  did not exist.
- Transport-order tests initially failed because `PolicyBoundTransport` had no
  cache constructor or hit path.
- Run-contract tests initially failed because `cache_summary` was absent from
  the closed discriminated event union.
- Retention CLI tests initially failed because the `cache` command tree did
  not exist.
- The browser parity test rejected a valid `cache_summary` as an unknown trace
  event before its strict decoder was extended.
- Security self-review reproduced a corrupted hashed-index marker being
  ignored by `delete_run`. The deletion then succeeded instead of failing
  closed. The regression now proves `cache_index_invalid` is raised and the
  associated record remains untouched.

## GREEN evidence

- `env PYTHONPATH=src make check
  SPECPILOT_PYTHON=../../.venv/bin/python`: Ruff passed, mypy passed for
  **112 source files**, **1,617 unit tests passed**, and **197 CLI tests
  passed**.
- Fresh isolated PostgreSQL:
  `tests/integration/providers/test_transport_ledger_flow.py` plus
  `tests/integration/runs/test_migration.py`: **105 passed**, covering fresh
  migrations 001 through 016 and the explicit 015-to-016 upgrade.
- Cache filesystem/key suite: **17 passed**, including 0700/0600 modes,
  symlink refusal, TTL, record corruption, hashed deletion, and index-marker
  corruption with pre-delete validation.
- `npm test -- --run`: **151 passed**; `npm run build`: passed and regenerated
  the committed trace bundle.
- Generated static assets contain no `?token=`/`token=` credential query
  pattern, and `git diff --check` passed.

## Residual risks and boundaries

- No live provider call was made. All provider behavior was exercised with
  fakes and the existing isolated transport integration harness.
- The cache is deliberately local and synchronous. Cross-host sharing,
  distributed invalidation, eviction by size, and Task 8 or later packaging
  behavior remain outside Task 7.
- Expired or deleted records can leave harmless hashed markers until their
  run/session retention command is invoked. Marker validation still fails
  closed, and missing records make deletion idempotent.
- Cache records intentionally contain provider responses and therefore must
  remain under the explicitly configured private cache directory; durable run
  traces contain only bounded opaque hashes.

## Review round 1

Four retention and identity defects were reproduced and corrected.

- Transport callers now pass a validated `CacheLinkage` copied from the real
  durable `RunRecord.run_id/session_id` (or the owner-validated resume
  session). `evaluation_root_id` is no longer used as a retention session.
  Every hit associates the current run and session while holding the same key
  lock used to read the record; association failure rolls back new markers and
  fails closed before returning the response.
- New records become observable only after both required hashed index markers
  exist. Publication orders run marker, session marker, then record as the
  commit point. Index and cancellation-shaped failures remove any record and
  newly created markers, while preserving the primary control-flow exception.
- A persistent private lock record per cache key provides bounded
  cross-process `flock` exclusion. Lock records are canonical `0600` regular
  files under a `0700` directory, opened with `O_NOFOLLOW` and inode/name
  revalidation. All get, put, association, expiry, and indexed deletion paths
  obey the lock; multi-key deletion acquires hashes in sorted order. A
  deterministic delete/put race proves a validated old name cannot unlink the
  replacement record.
- Cache namespace binding now selects the configured stage prompt hash:
  global prompt ID/hash for all keys, compliance hash for compliance, and
  verifier hash for semantic/verifier (and judge) stages. The two stage hashes
  are explicit runtime/Compose inputs; no prompt identifiers are fabricated.

Review verification from the final tree:

- Cache filesystem/concurrency suite: **28 passed**, including index-first
  publication rollback, hit-association rollback, stale-index republish,
  symlink/mode refusal, hard lock timeout, cancellation-shaped cleanup, and
  the deterministic replacement race.
- `make check`: Ruff and mypy passed; **1,628 unit tests** and **197 CLI tests
  passed**.
- Fresh PostgreSQL transport plus migrations: **105 passed**.
- Browser decoder suite: **151 passed** and the production build succeeded.

## Review round 2

Two remaining cache-hit defects were reproduced and corrected.

- A deleted record can be repopulated under the same content key while older
  run/session markers remain. Hit association now decodes the existing marker
  canonically under the key lock and replaces it only when its record hash is
  absent or differs from the currently validated live record. Corrupt markers
  and markers that still bind the live record are never overwritten. The
  cross-session regression deletes the old run, repopulates from a new
  session, reuses the original session, and verifies both new run and session
  retention links.
- `PolicyBoundTransport.send` no longer executes synchronous cache filesystem
  or `flock` waits on the event loop. Cache get/association and put run through
  `asyncio.to_thread`; the task is shielded so caller cancellation returns
  promptly while the bounded filesystem operation finishes. A done callback
  consumes every detached result or exception. The cache's five-second lock
  bound, `finally`-based descriptor release, and atomic publication/association
  rollback remain authoritative for the background operation.

Review verification from the final tree:

- Cache filesystem suite: **29 passed**.
- `make check`: Ruff and mypy passed; **1,629 unit tests** and **197 CLI tests
  passed**.
- Fresh PostgreSQL transport plus migrations: **107 passed**, including a
  ticker that advances during lock contention and prompt cancellation followed
  by observed background association, retention deletion, and lock release.
- Browser suite: **151 passed** and the production build succeeded.
