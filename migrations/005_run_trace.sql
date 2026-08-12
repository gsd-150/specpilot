-- Owner-bound asynchronous runs and their sanitized append-only trace.
--
-- The question itself is intentionally absent.  A process loss therefore
-- expires a bounded queue/worker lease and becomes observable as interrupted;
-- this schema does not create a durable payload from which a provider send
-- could be silently repeated.  Event JSON is a closed metadata envelope and
-- duplicates kind/sequence only so the database can verify it agrees with the
-- indexed columns before accepting the row.

BEGIN;

-- CHECK expressions call these immutable validators so malformed JSON types
-- fail as ordinary constraint violations.  Each helper guards jsonb_typeof
-- before extracting/casting; raw inserts cannot turn a bad payload into an
-- unexpected cast error or hide arbitrary keys in a nested object.
CREATE FUNCTION specpilot_trace_exact_object(value jsonb, allowed_keys text[])
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
BEGIN
    IF jsonb_typeof(value) <> 'object' THEN
        RETURN false;
    END IF;
    RETURN NOT EXISTS (
        SELECT 1
        FROM jsonb_object_keys(value) AS found(key)
        WHERE NOT (found.key = ANY (allowed_keys))
    );
END;
$$;

CREATE FUNCTION specpilot_trace_identifier(value jsonb, maximum integer)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    rendered text;
BEGIN
    IF jsonb_typeof(value) <> 'string' THEN
        RETURN false;
    END IF;
    rendered := value #>> '{}';
    RETURN char_length(rendered) BETWEEN 1 AND maximum
        AND rendered ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$';
END;
$$;

CREATE FUNCTION specpilot_trace_reason(value jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) <> 'string' THEN false
        ELSE value #>> '{}' ~ '^[a-z][a-z0-9_]{0,63}$'
    END
$$;

CREATE FUNCTION specpilot_trace_sha256(value jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) <> 'string' THEN false
        ELSE value #>> '{}' ~ '^[0-9a-f]{64}$'
    END
$$;

CREATE FUNCTION specpilot_trace_uuid(value jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) <> 'string' THEN false
        ELSE value #>> '{}' ~*
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    END
$$;

CREATE FUNCTION specpilot_trace_integer(
    value jsonb,
    minimum numeric,
    maximum numeric
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    rendered text;
    parsed numeric;
BEGIN
    IF jsonb_typeof(value) <> 'number' THEN
        RETURN false;
    END IF;
    rendered := value::text;
    IF rendered !~ '^-?(0|[1-9][0-9]*)$' THEN
        RETURN false;
    END IF;
    parsed := rendered::numeric;
    RETURN parsed BETWEEN minimum AND maximum;
END;
$$;

CREATE FUNCTION specpilot_trace_argument_keys(value jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    item jsonb;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 16 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element)
    LOOP
        IF jsonb_typeof(item) <> 'string'
            OR char_length(item #>> '{}') NOT BETWEEN 1 AND 64
            OR item #>> '{}' !~ '^[A-Za-z][A-Za-z0-9_]*$'
        THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION specpilot_trace_candidates(value jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    item jsonb;
    score numeric;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 20 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element)
    LOOP
        IF NOT specpilot_trace_exact_object(item, ARRAY['candidate_id', 'score'])
            OR NOT (item ?& ARRAY['candidate_id', 'score'])
            OR NOT specpilot_trace_identifier(item -> 'candidate_id', 128)
            OR jsonb_typeof(item -> 'score') <> 'number'
        THEN
            RETURN false;
        END IF;
        score := (item ->> 'score')::numeric;
        IF score < -1000000000000 OR score > 1000000000000 THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION specpilot_trace_evidence(value jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    item jsonb;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 5 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element)
    LOOP
        IF NOT specpilot_trace_exact_object(
                item, ARRAY['evidence_id', 'content_hash']
            )
            OR NOT (item ?& ARRAY['evidence_id', 'content_hash'])
            OR NOT specpilot_trace_sha256(item -> 'evidence_id')
            OR NOT specpilot_trace_sha256(item -> 'content_hash')
        THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION specpilot_trace_checks(value jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    item jsonb;
    passed boolean;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 20 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element)
    LOOP
        IF NOT specpilot_trace_exact_object(
                item, ARRAY['evidence_id', 'passed', 'fault_code']
            )
            OR NOT (item ?& ARRAY['evidence_id', 'passed', 'fault_code'])
            OR jsonb_typeof(item -> 'passed') <> 'boolean'
            OR NOT (
                jsonb_typeof(item -> 'evidence_id') = 'null'
                OR specpilot_trace_sha256(item -> 'evidence_id')
            )
            OR NOT (
                jsonb_typeof(item -> 'fault_code') = 'null'
                OR specpilot_trace_reason(item -> 'fault_code')
            )
        THEN
            RETURN false;
        END IF;
        passed := (item ->> 'passed')::boolean;
        IF (passed AND jsonb_typeof(item -> 'fault_code') <> 'null')
            OR (NOT passed AND jsonb_typeof(item -> 'fault_code') = 'null')
        THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION specpilot_valid_run_event(
    event_kind text,
    event_sequence integer,
    event_payload jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    nullable_reason boolean;
    status_value text;
    verdict_value text;
    admitted_value boolean;
BEGIN
    IF jsonb_typeof(event_payload) <> 'object'
        OR NOT (event_payload ?& ARRAY['kind', 'sequence'])
        OR jsonb_typeof(event_payload -> 'kind') <> 'string'
        OR event_payload ->> 'kind' <> event_kind
        OR NOT specpilot_trace_integer(event_payload -> 'sequence', 1, 10000)
        OR (event_payload ->> 'sequence')::integer <> event_sequence
    THEN
        RETURN false;
    END IF;

    CASE event_kind
        WHEN 'state_transition' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY['kind', 'sequence', 'previous_status', 'status', 'reason']
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'previous_status', 'status', 'reason'
                ])
                OR NOT (
                    jsonb_typeof(event_payload -> 'previous_status') = 'null'
                    OR event_payload ->> 'previous_status' IN (
                        'queued', 'running', 'answered', 'refused',
                        'egress_blocked', 'failed', 'interrupted'
                    )
                )
                OR jsonb_typeof(event_payload -> 'status') <> 'string'
                OR event_payload ->> 'status' NOT IN (
                    'queued', 'running', 'answered', 'refused',
                    'egress_blocked', 'failed', 'interrupted'
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'reason') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'reason')
                )
            THEN RETURN false; END IF;
        WHEN 'plan_summary' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY['kind', 'sequence', 'plan_id', 'step_count', 'max_tool_calls']
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'plan_id', 'step_count', 'max_tool_calls'
                ])
                OR NOT specpilot_trace_identifier(event_payload -> 'plan_id', 128)
                OR NOT specpilot_trace_integer(event_payload -> 'step_count', 1, 4)
                OR NOT specpilot_trace_integer(event_payload -> 'max_tool_calls', 1, 6)
            THEN RETURN false; END IF;
        WHEN 'agent_step' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY[
                        'kind', 'sequence', 'agent', 'step_id', 'phase',
                        'duration_ms', 'error_code'
                    ]
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'agent', 'step_id', 'phase',
                    'duration_ms', 'error_code'
                ])
                OR event_payload ->> 'agent' NOT IN (
                    'orchestrator', 'evidence_agent', 'answer', 'verifier'
                )
                OR NOT specpilot_trace_identifier(event_payload -> 'step_id', 128)
                OR event_payload ->> 'phase' NOT IN ('started', 'finished')
                OR NOT (
                    jsonb_typeof(event_payload -> 'duration_ms') = 'null'
                    OR specpilot_trace_integer(
                        event_payload -> 'duration_ms', 0, 3600000
                    )
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'error_code') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'error_code')
                )
            THEN RETURN false; END IF;
        WHEN 'tool_finished' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY[
                        'kind', 'sequence', 'step_id', 'tool', 'argument_keys',
                        'result_count', 'duration_ms', 'retry_count', 'error_code'
                    ]
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'step_id', 'tool', 'argument_keys',
                    'result_count', 'duration_ms', 'retry_count', 'error_code'
                ])
                OR NOT specpilot_trace_identifier(event_payload -> 'step_id', 128)
                OR event_payload ->> 'tool' NOT IN (
                    'search_clauses', 'get_clause', 'get_toc',
                    'expand_references', 'lookup_term'
                )
                OR NOT specpilot_trace_argument_keys(event_payload -> 'argument_keys')
                OR NOT specpilot_trace_integer(
                    event_payload -> 'result_count', 0, 1000000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'duration_ms', 0, 3600000
                )
                OR NOT specpilot_trace_integer(event_payload -> 'retry_count', 0, 1)
                OR NOT (
                    jsonb_typeof(event_payload -> 'error_code') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'error_code')
                )
            THEN RETURN false; END IF;
        WHEN 'candidate_summary' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload, ARRAY['kind', 'sequence', 'candidates']
                )
                OR NOT (event_payload ?& ARRAY['kind', 'sequence', 'candidates'])
                OR NOT specpilot_trace_candidates(event_payload -> 'candidates')
            THEN RETURN false; END IF;
        WHEN 'evidence_summary' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload, ARRAY['kind', 'sequence', 'evidence']
                )
                OR NOT (event_payload ?& ARRAY['kind', 'sequence', 'evidence'])
                OR NOT specpilot_trace_evidence(event_payload -> 'evidence')
            THEN RETURN false; END IF;
        WHEN 'egress_summary' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY[
                        'kind', 'sequence', 'stage', 'reservation_id', 'ledger_id',
                        'admitted', 'request_tokens', 'request_bytes',
                        'cost_microunits', 'error_code'
                    ]
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'stage', 'reservation_id', 'ledger_id',
                    'admitted', 'request_tokens', 'request_bytes',
                    'cost_microunits', 'error_code'
                ])
                OR jsonb_typeof(event_payload -> 'stage') <> 'string'
                OR event_payload ->> 'stage' NOT IN (
                    'planning', 'evidence', 'compliance', 'verifier', 'judge'
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'reservation_id') = 'null'
                    OR specpilot_trace_uuid(event_payload -> 'reservation_id')
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'ledger_id') = 'null'
                    OR specpilot_trace_uuid(event_payload -> 'ledger_id')
                )
                OR jsonb_typeof(event_payload -> 'admitted') <> 'boolean'
                OR NOT specpilot_trace_integer(
                    event_payload -> 'request_tokens', 0, 1000000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'request_bytes', 0, 1000000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'cost_microunits', 0, 1000000000
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'error_code') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'error_code')
                )
            THEN RETURN false; END IF;
            admitted_value := (event_payload ->> 'admitted')::boolean;
            nullable_reason := jsonb_typeof(event_payload -> 'error_code') = 'null';
            IF (admitted_value AND NOT nullable_reason)
                OR (NOT admitted_value AND nullable_reason)
            THEN RETURN false; END IF;
        WHEN 'usage_summary' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY[
                        'kind', 'sequence', 'stage', 'prompt_tokens',
                        'completion_tokens', 'request_bytes', 'duration_ms',
                        'cost_microunits'
                    ]
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'stage', 'prompt_tokens',
                    'completion_tokens', 'request_bytes', 'duration_ms',
                    'cost_microunits'
                ])
                OR jsonb_typeof(event_payload -> 'stage') <> 'string'
                OR event_payload ->> 'stage' NOT IN (
                    'planning', 'evidence', 'compliance', 'verifier', 'judge'
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'prompt_tokens', 0, 1000000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'completion_tokens', 0, 1000000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'request_bytes', 0, 1000000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'duration_ms', 0, 3600000
                )
                OR NOT specpilot_trace_integer(
                    event_payload -> 'cost_microunits', 0, 1000000000
                )
            THEN RETURN false; END IF;
        WHEN 'answer_outcome' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload,
                    ARRAY[
                        'kind', 'sequence', 'verdict', 'refusal_reason',
                        'provider_error', 'reservation_id', 'replayed',
                        'parse_fault_code'
                    ]
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'verdict', 'refusal_reason',
                    'provider_error', 'reservation_id', 'replayed',
                    'parse_fault_code'
                ])
                OR jsonb_typeof(event_payload -> 'verdict') <> 'string'
                OR event_payload ->> 'verdict' NOT IN ('answered', 'refused')
                OR NOT (
                    jsonb_typeof(event_payload -> 'refusal_reason') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'refusal_reason')
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'provider_error') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'provider_error')
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'reservation_id') = 'null'
                    OR specpilot_trace_uuid(event_payload -> 'reservation_id')
                )
                OR jsonb_typeof(event_payload -> 'replayed') <> 'boolean'
                OR NOT (
                    jsonb_typeof(event_payload -> 'parse_fault_code') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'parse_fault_code')
                )
            THEN RETURN false; END IF;
            verdict_value := event_payload ->> 'verdict';
            nullable_reason :=
                jsonb_typeof(event_payload -> 'refusal_reason') = 'null';
            IF (verdict_value = 'answered' AND NOT nullable_reason)
                OR (verdict_value = 'refused' AND nullable_reason)
            THEN RETURN false; END IF;
        WHEN 'verifier_summary' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload, ARRAY['kind', 'sequence', 'checks', 'duration_ms']
                )
                OR NOT (event_payload ?& ARRAY[
                    'kind', 'sequence', 'checks', 'duration_ms'
                ])
                OR NOT specpilot_trace_checks(event_payload -> 'checks')
                OR NOT specpilot_trace_integer(
                    event_payload -> 'duration_ms', 0, 3600000
                )
            THEN RETURN false; END IF;
        WHEN 'terminal' THEN
            IF NOT specpilot_trace_exact_object(
                    event_payload, ARRAY['kind', 'sequence', 'status', 'reason']
                )
                OR NOT (event_payload ?& ARRAY['kind', 'sequence', 'status', 'reason'])
                OR jsonb_typeof(event_payload -> 'status') <> 'string'
                OR event_payload ->> 'status' NOT IN (
                    'answered', 'refused', 'egress_blocked', 'failed', 'interrupted'
                )
                OR NOT (
                    jsonb_typeof(event_payload -> 'reason') = 'null'
                    OR specpilot_trace_reason(event_payload -> 'reason')
                )
            THEN RETURN false; END IF;
            status_value := event_payload ->> 'status';
            nullable_reason := jsonb_typeof(event_payload -> 'reason') = 'null';
            IF (status_value = 'answered' AND NOT nullable_reason)
                OR (status_value <> 'answered' AND nullable_reason)
            THEN RETURN false; END IF;
        ELSE
            RETURN false;
    END CASE;
    RETURN true;
END;
$$;

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
        specpilot_valid_run_event(kind, sequence, payload)
    )
);

CREATE INDEX specpilot_run_owner_created_idx
    ON specpilot_run (session_id, created_at DESC);
CREATE INDEX specpilot_run_expired_lease_idx
    ON specpilot_run (lease_expires_at)
    WHERE status IN ('queued', 'running');

COMMIT;
