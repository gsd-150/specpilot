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
