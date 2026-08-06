-- SpecPilot W0 egress reservation ledger.
--
-- Two design decisions worth stating, because both are easy to "fix" wrongly:
--
-- 1. Cap arithmetic is NOT reimplemented in SQL. The pure enforcer in
--    specpilot.egress.enforcer is the single source of that logic and is
--    covered by the unit suite; a second implementation in SQL would be free to
--    drift from it, and the drift would show up as a silently raised ceiling.
--    The transaction therefore locks the budget rows, re-runs the pure enforcer
--    against the stored state, and writes the result back. The normalized
--    tables below are the append-only audit trail, plus constraints that act as
--    defence in depth against a caller that bypasses the enforcer.
--
-- 2. Lock order is always corpus ledger first, then evaluation root. Any
--    reservation touches both, so a fixed order is what prevents deadlock.
--    Locking the corpus row serializes reservations across the whole corpus.
--    That is deliberate: the corpus scope is the outermost cap, and at this
--    evaluation's size (tens of cases) correctness is worth far more than
--    reservation throughput.
--
-- No column in this schema holds query text, claim text, excerpt text, prompts,
-- responses, credentials, or file paths. Only hashes, coordinates, and counts.

CREATE TABLE IF NOT EXISTS egress_policy_snapshot (
    policy_hash     text PRIMARY KEY
                    CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    schema_version  text        NOT NULL,
    first_seen_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per evaluation case. This row is the per-case budget and is locked
-- FOR UPDATE by every reservation that belongs to it.
CREATE TABLE IF NOT EXISTS egress_evaluation_root (
    evaluation_root_id  text PRIMARY KEY,
    policy_hash         text NOT NULL REFERENCES egress_policy_snapshot (policy_hash),
    task_level          text NOT NULL CHECK (task_level IN ('L1', 'L2')),
    corpus_manifest_id  text NOT NULL CHECK (corpus_manifest_id ~ '^[0-9a-f]{64}$'),
    usage_snapshot      jsonb       NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- One row per frozen corpus: the only scope above evaluation_root. Locked
-- before the root row by every reservation.
CREATE TABLE IF NOT EXISTS egress_corpus_ledger (
    corpus_manifest_id  text PRIMARY KEY
                        CHECK (corpus_manifest_id ~ '^[0-9a-f]{64}$'),
    policy_hash         text NOT NULL REFERENCES egress_policy_snapshot (policy_hash),
    corpus_usage        jsonb       NOT NULL,
    unique_excerpts     integer     NOT NULL CHECK (unique_excerpts >= 0),
    unique_tokens       integer     NOT NULL CHECK (unique_tokens >= 0),
    unique_bytes        bigint      NOT NULL CHECK (unique_bytes >= 0),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS egress_reservation (
    reservation_id      uuid PRIMARY KEY,
    idempotency_key     text NOT NULL,
    evaluation_root_id  text NOT NULL REFERENCES egress_evaluation_root (evaluation_root_id),
    run_id              text NOT NULL,
    policy_hash         text NOT NULL REFERENCES egress_policy_snapshot (policy_hash),
    corpus_manifest_id  text NOT NULL REFERENCES egress_corpus_ledger (corpus_manifest_id),
    stage               text NOT NULL
                        CHECK (stage IN ('evidence', 'compliance', 'verifier', 'judge')),
    provider_id         text NOT NULL,
    endpoint_purpose    text NOT NULL,
    provider_use        text NOT NULL
                        CHECK (provider_use IN ('online_main', 'offline_judge')),
    model_id            text NOT NULL,
    state               text NOT NULL
                        CHECK (state IN ('reserved', 'sending', 'succeeded', 'failed_known')),
    created_at          timestamptz NOT NULL DEFAULT now(),

    -- The idempotency contract. A repeated key inside one run and policy
    -- resolves to this same row, and the caller must return it without
    -- re-applying caps.
    CONSTRAINT egress_reservation_idempotent
        UNIQUE (evaluation_root_id, run_id, policy_hash, idempotency_key)
);

-- Which disclosures each reservation covers. Sizes are stored so the audit
-- trail can be reconciled against the snapshot without any excerpt text.
CREATE TABLE IF NOT EXISTS egress_reservation_disclosure (
    reservation_id  uuid NOT NULL REFERENCES egress_reservation (reservation_id),
    disclosure_id   text NOT NULL CHECK (disclosure_id ~ '^[0-9a-f]{64}$'),
    token_count     integer NOT NULL CHECK (token_count > 0),
    byte_count      integer NOT NULL CHECK (byte_count > 0),
    PRIMARY KEY (reservation_id, disclosure_id)
);

-- Which distinct source text each provider route has ever seen. Cross-provider
-- resend of one excerpt is one corpus disclosure but two route disclosures.
CREATE TABLE IF NOT EXISTS egress_route_disclosure (
    corpus_manifest_id  text NOT NULL REFERENCES egress_corpus_ledger (corpus_manifest_id),
    provider_id         text NOT NULL,
    endpoint_purpose    text NOT NULL,
    disclosure_id       text NOT NULL CHECK (disclosure_id ~ '^[0-9a-f]{64}$'),
    first_disclosed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (corpus_manifest_id, provider_id, endpoint_purpose, disclosure_id)
);

CREATE TABLE IF NOT EXISTS egress_attempt (
    attempt_id          uuid PRIMARY KEY,
    reservation_id      uuid NOT NULL REFERENCES egress_reservation (reservation_id),
    provider_id         text NOT NULL,
    endpoint_purpose    text NOT NULL,
    outcome             text NOT NULL
                        CHECK (outcome IN ('succeeded', 'failed_known')),
    transmitted_tokens  integer NOT NULL CHECK (transmitted_tokens >= 0),
    transmitted_bytes   bigint  NOT NULL CHECK (transmitted_bytes >= 0),
    duration_ms         integer NOT NULL CHECK (duration_ms >= 0),
    public_error_code   text,
    recorded_at         timestamptz NOT NULL DEFAULT now()
);

-- A run is sealed when a send provably happened but its accounting could not be
-- written. check_and_reserve refuses sealed runs, so the seal is enforced at the
-- same atomic point as every cap rather than by a caller-side check that some
-- future call site can forget.
CREATE TABLE IF NOT EXISTS egress_run_seal (
    evaluation_root_id  text NOT NULL,
    run_id              text NOT NULL,
    reason              text        NOT NULL,
    sealed_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (evaluation_root_id, run_id)
);

CREATE INDEX IF NOT EXISTS egress_reservation_run_idx
    ON egress_reservation (evaluation_root_id, run_id);
CREATE INDEX IF NOT EXISTS egress_attempt_reservation_idx
    ON egress_attempt (reservation_id);
