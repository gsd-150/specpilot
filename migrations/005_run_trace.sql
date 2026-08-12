-- Owner-bound asynchronous runs and their sanitized append-only trace.
--
-- The question itself is intentionally absent.  A process loss therefore
-- expires a bounded queue/worker lease and becomes observable as interrupted;
-- this schema does not create a durable payload from which a provider send
-- could be silently repeated.  Event JSON is a closed metadata envelope and
-- duplicates kind/sequence only so the database can verify it agrees with the
-- indexed columns before accepting the row.

BEGIN;

CREATE TABLE specpilot_run (
    run_id               uuid PRIMARY KEY,
    request_id           uuid        NOT NULL UNIQUE,
    session_id           text        NOT NULL
        CHECK (
            char_length(session_id) BETWEEN 1 AND 128
            AND session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        ),
    task_level           text        NOT NULL CHECK (task_level = 'L1'),
    profile              text        NOT NULL
        CHECK (
            char_length(profile) BETWEEN 1 AND 128
            AND profile ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        ),
    source_manifest_id   text        NOT NULL
        CHECK (source_manifest_id ~ '^[0-9a-f]{64}$'),
    corpus_manifest_id   text        NOT NULL
        REFERENCES egress_corpus_ledger_head (corpus_manifest_id)
        CHECK (corpus_manifest_id ~ '^[0-9a-f]{64}$'),
    policy_hash          text        NOT NULL
        REFERENCES egress_policy_snapshot (policy_hash)
        CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    configuration_hash   text        NOT NULL
        CHECK (configuration_hash ~ '^[0-9a-f]{64}$'),
    prompt_id            text        NOT NULL
        CHECK (
            char_length(prompt_id) BETWEEN 1 AND 128
            AND prompt_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        ),
    prompt_hash          text        NOT NULL
        CHECK (prompt_hash ~ '^[0-9a-f]{64}$'),
    provider_id          text        NOT NULL
        CHECK (
            char_length(provider_id) BETWEEN 1 AND 128
            AND provider_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        ),
    model_id             text        NOT NULL
        CHECK (
            char_length(model_id) BETWEEN 1 AND 128
            AND model_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        ),
    query_hash           text        NOT NULL
        CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    status               text        NOT NULL,
    terminal_reason      text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    completed_at         timestamptz,
    lease_owner          text
        CHECK (
            lease_owner IS NULL
            OR (
                char_length(lease_owner) BETWEEN 1 AND 128
                AND lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            )
        ),
    lease_expires_at     timestamptz,
    last_heartbeat_at    timestamptz,

    CONSTRAINT specpilot_run_status_check CHECK (
        status IN (
            'queued', 'running', 'answered', 'refused', 'egress_blocked',
            'failed', 'interrupted'
        )
    ),
    CONSTRAINT specpilot_run_terminal_reason_check CHECK (
        terminal_reason IS NULL
        OR terminal_reason ~ '^[a-z][a-z0-9_]{0,63}$'
    ),
    CONSTRAINT specpilot_run_state_metadata_check CHECK (
        (
            status IN ('queued', 'running')
            AND terminal_reason IS NULL
            AND completed_at IS NULL
            AND lease_owner IS NOT NULL
            AND lease_expires_at IS NOT NULL
        )
        OR (
            status IN (
                'answered', 'refused', 'egress_blocked', 'failed', 'interrupted'
            )
            AND (
                (status = 'answered' AND terminal_reason IS NULL)
                OR (status <> 'answered' AND terminal_reason IS NOT NULL)
            )
            AND completed_at IS NOT NULL
            AND lease_owner IS NULL
            AND lease_expires_at IS NULL
            AND last_heartbeat_at IS NULL
        )
    ),
    CONSTRAINT specpilot_run_start_check CHECK (
        (status = 'queued' AND started_at IS NULL)
        OR status NOT IN ('queued', 'running')
        OR (status = 'running' AND started_at IS NOT NULL)
    ),
    CONSTRAINT specpilot_run_timestamp_order_check CHECK (
        (started_at IS NULL OR started_at >= created_at)
        AND (
            completed_at IS NULL
            OR completed_at >= COALESCE(started_at, created_at)
        )
        AND (
            lease_expires_at IS NULL
            OR lease_expires_at > created_at
        )
        AND (
            last_heartbeat_at IS NULL
            OR (
                lease_expires_at IS NOT NULL
                AND last_heartbeat_at >= COALESCE(started_at, created_at)
                AND last_heartbeat_at <= lease_expires_at
            )
        )
    )
);

CREATE TABLE specpilot_run_event (
    run_id       uuid        NOT NULL
        REFERENCES specpilot_run (run_id) ON DELETE CASCADE,
    sequence     integer     NOT NULL CHECK (sequence BETWEEN 1 AND 10000),
    kind         text        NOT NULL,
    payload      jsonb       NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence),

    CONSTRAINT specpilot_run_event_kind_check CHECK (
        kind IN (
            'state_transition', 'plan_summary', 'agent_step', 'tool_finished',
            'candidate_summary', 'evidence_summary', 'egress_summary',
            'usage_summary', 'answer_outcome', 'verifier_summary', 'terminal'
        )
    ),
    CONSTRAINT specpilot_run_event_payload_check CHECK (
        jsonb_typeof(payload) = 'object'
        AND payload ?& ARRAY['kind', 'sequence']
        AND payload ->> 'kind' = kind
        AND (payload ->> 'sequence')::integer = sequence
        AND CASE kind
            WHEN 'state_transition' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'previous_status', 'status', 'reason'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'previous_status', 'status', 'reason'
                ]::text[] = '{}'::jsonb
            WHEN 'plan_summary' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'plan_id', 'step_count', 'max_tool_calls'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'plan_id', 'step_count', 'max_tool_calls'
                ]::text[] = '{}'::jsonb
            WHEN 'agent_step' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'agent', 'step_id', 'phase',
                    'duration_ms', 'error_code'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'agent', 'step_id', 'phase',
                    'duration_ms', 'error_code'
                ]::text[] = '{}'::jsonb
            WHEN 'tool_finished' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'step_id', 'tool', 'argument_keys',
                    'result_count', 'duration_ms', 'retry_count', 'error_code'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'step_id', 'tool', 'argument_keys',
                    'result_count', 'duration_ms', 'retry_count', 'error_code'
                ]::text[] = '{}'::jsonb
            WHEN 'candidate_summary' THEN
                payload ?& ARRAY['kind', 'sequence', 'candidates']
                AND payload - ARRAY[
                    'kind', 'sequence', 'candidates'
                ]::text[] = '{}'::jsonb
            WHEN 'evidence_summary' THEN
                payload ?& ARRAY['kind', 'sequence', 'evidence']
                AND payload - ARRAY[
                    'kind', 'sequence', 'evidence'
                ]::text[] = '{}'::jsonb
            WHEN 'egress_summary' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'stage', 'reservation_id', 'ledger_id',
                    'admitted', 'request_tokens', 'request_bytes',
                    'cost_microunits', 'error_code'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'stage', 'reservation_id', 'ledger_id',
                    'admitted', 'request_tokens', 'request_bytes',
                    'cost_microunits', 'error_code'
                ]::text[] = '{}'::jsonb
            WHEN 'usage_summary' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'stage', 'prompt_tokens',
                    'completion_tokens', 'request_bytes', 'duration_ms',
                    'cost_microunits'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'stage', 'prompt_tokens',
                    'completion_tokens', 'request_bytes', 'duration_ms',
                    'cost_microunits'
                ]::text[] = '{}'::jsonb
            WHEN 'answer_outcome' THEN
                payload ?& ARRAY[
                    'kind', 'sequence', 'verdict', 'refusal_reason',
                    'provider_error', 'reservation_id', 'replayed',
                    'parse_fault_code'
                ]
                AND payload - ARRAY[
                    'kind', 'sequence', 'verdict', 'refusal_reason',
                    'provider_error', 'reservation_id', 'replayed',
                    'parse_fault_code'
                ]::text[] = '{}'::jsonb
            WHEN 'verifier_summary' THEN
                payload ?& ARRAY['kind', 'sequence', 'checks', 'duration_ms']
                AND payload - ARRAY[
                    'kind', 'sequence', 'checks', 'duration_ms'
                ]::text[] = '{}'::jsonb
            WHEN 'terminal' THEN
                payload ?& ARRAY['kind', 'sequence', 'status', 'reason']
                AND payload - ARRAY[
                    'kind', 'sequence', 'status', 'reason'
                ]::text[] = '{}'::jsonb
            ELSE false
        END
    )
);

CREATE INDEX specpilot_run_owner_created_idx
    ON specpilot_run (session_id, created_at DESC);
CREATE INDEX specpilot_run_expired_lease_idx
    ON specpilot_run (lease_expires_at)
    WHERE status IN ('queued', 'running');

COMMIT;
