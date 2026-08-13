-- Sanitized W4 L2 recovery state.  The payload deliberately admits hashes and
-- frozen identities only; no question, claim, retrieval query, excerpt, or
-- provider response can become durable through this schema.

BEGIN;

ALTER TABLE specpilot_run
    DROP CONSTRAINT specpilot_run_task_level_check,
    ADD CONSTRAINT specpilot_run_task_level_check
        CHECK (task_level IN ('L1', 'L2'));

ALTER TABLE specpilot_run_event
    DROP CONSTRAINT specpilot_run_event_kind_check,
    ADD CONSTRAINT specpilot_run_event_kind_check CHECK (
        kind IN (
            'state_transition', 'plan_summary', 'agent_step', 'tool_finished',
            'candidate_summary', 'evidence_summary', 'egress_summary',
            'usage_summary', 'answer_outcome', 'verifier_summary', 'terminal',
            'checkpoint_summary', 'compliance_summary', 'semantic_summary',
            'recovery_summary', 'resume_summary'
        )
    );

CREATE FUNCTION specpilot_valid_w4_run_event(
    event_kind text, event_sequence integer, event_payload jsonb
)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    IF event_kind NOT IN (
        'checkpoint_summary', 'compliance_summary', 'semantic_summary',
        'recovery_summary', 'resume_summary'
    ) THEN
        RETURN specpilot_valid_run_event(event_kind, event_sequence, event_payload);
    END IF;
    IF jsonb_typeof(event_payload) <> 'object'
        OR NOT (event_payload ?& ARRAY['kind', 'sequence'])
        OR event_payload ->> 'kind' <> event_kind
        OR NOT specpilot_trace_integer(event_payload -> 'sequence', 1, 10000)
        OR (event_payload ->> 'sequence')::integer <> event_sequence
    THEN RETURN false; END IF;
    CASE event_kind
        WHEN 'checkpoint_summary' THEN
            RETURN specpilot_trace_exact_object(event_payload, ARRAY[
                    'kind','sequence','stage','checkpoint_version',
                    'tool_attempts_used','recovery_attempted'
                ])
                AND event_payload ?& ARRAY[
                    'kind','sequence','stage','checkpoint_version',
                    'tool_attempts_used','recovery_attempted'
                ]
                AND event_payload ->> 'stage' IN (
                    'planned','evidence_collected','candidate_built',
                    'deterministic_verified','recovery_completed',
                    'semantic_verified','completed'
                )
                AND specpilot_trace_integer(event_payload -> 'checkpoint_version', 1, 2147483647)
                AND specpilot_trace_integer(event_payload -> 'tool_attempts_used', 0, 8)
                AND jsonb_typeof(event_payload -> 'recovery_attempted') = 'boolean';
        WHEN 'compliance_summary' THEN
            RETURN specpilot_trace_exact_object(event_payload, ARRAY[
                    'kind','sequence','candidate_count','claim_ids'
                ])
                AND event_payload ?& ARRAY[
                    'kind','sequence','candidate_count','claim_ids'
                ]
                AND specpilot_trace_integer(event_payload -> 'candidate_count', 1, 3)
                AND jsonb_typeof(event_payload -> 'claim_ids') = 'array'
                AND jsonb_array_length(event_payload -> 'claim_ids') BETWEEN 1 AND 3;
        WHEN 'semantic_summary' THEN
            RETURN specpilot_trace_exact_object(event_payload, ARRAY[
                    'kind','sequence','claim_id','supports','reason'
                ])
                AND event_payload ?& ARRAY[
                    'kind','sequence','claim_id','supports','reason'
                ]
                AND specpilot_trace_sha256(event_payload -> 'claim_id')
                AND jsonb_typeof(event_payload -> 'supports') = 'boolean'
                AND specpilot_trace_reason(event_payload -> 'reason');
        WHEN 'recovery_summary' THEN
            RETURN specpilot_trace_exact_object(event_payload, ARRAY[
                    'kind','sequence','kind_name','reason','remaining_tool_attempts'
                ])
                AND event_payload ?& ARRAY[
                    'kind','sequence','kind_name','reason','remaining_tool_attempts'
                ]
                AND event_payload ->> 'kind_name' IN (
                    'scoped_search','get_clause','expand_references'
                )
                AND specpilot_trace_reason(event_payload -> 'reason')
                AND specpilot_trace_integer(
                    event_payload -> 'remaining_tool_attempts', 0, 8
                );
        WHEN 'resume_summary' THEN
            RETURN specpilot_trace_exact_object(
                    event_payload, ARRAY['kind','sequence','attempt']
                )
                AND event_payload ?& ARRAY['kind','sequence','attempt']
                AND specpilot_trace_integer(event_payload -> 'attempt', 2, 2147483647);
    END CASE;
    RETURN false;
END;
$$;

ALTER TABLE specpilot_run_event
    DROP CONSTRAINT specpilot_run_event_payload_check,
    ADD CONSTRAINT specpilot_run_event_payload_check
        CHECK (specpilot_valid_w4_run_event(kind, sequence, payload));

CREATE FUNCTION specpilot_checkpoint_evidence(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE item jsonb;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 12 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element) LOOP
        IF NOT specpilot_trace_exact_object(item, ARRAY[
                'evidence_id','content_hash','quote_hash','clause_id',
                'document_id','document_version','section_number',
                'paragraph_start','paragraph_end','token_start','token_end'
            ])
            OR NOT (item ?& ARRAY[
                'evidence_id','content_hash','quote_hash','clause_id',
                'document_id','document_version','section_number',
                'paragraph_start','paragraph_end','token_start','token_end'
            ])
            OR NOT specpilot_trace_sha256(item -> 'evidence_id')
            OR NOT specpilot_trace_sha256(item -> 'content_hash')
            OR NOT specpilot_trace_sha256(item -> 'quote_hash')
            OR NOT specpilot_trace_sha256(item -> 'clause_id')
            OR NOT specpilot_trace_identifier(item -> 'document_id', 128)
            OR NOT specpilot_trace_identifier(item -> 'document_version', 128)
            OR NOT (jsonb_typeof(item -> 'section_number') = 'null'
                    OR specpilot_trace_identifier(item -> 'section_number', 64))
            OR NOT specpilot_trace_integer(item -> 'paragraph_start', 0, 2147483647)
            OR NOT specpilot_trace_integer(item -> 'paragraph_end', 0, 2147483647)
            OR NOT specpilot_trace_integer(item -> 'token_start', 0, 2147483647)
            OR NOT specpilot_trace_integer(item -> 'token_end', 0, 2147483647)
            OR (item ->> 'paragraph_end')::integer < (item ->> 'paragraph_start')::integer
            OR (item ->> 'token_end')::integer < (item ->> 'token_start')::integer
        THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION specpilot_valid_checkpoint(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE stage_value text;
BEGIN
    IF NOT specpilot_trace_exact_object(value, ARRAY[
        'schema_version','run_id','attempt','checkpoint_version','stage','task_level',
        'query_hash','evaluation_root_id','source_manifest_id','corpus_manifest_id',
        'policy_hash','configuration_hash','compliance_prompt_hash','verifier_prompt_hash',
        'provider_id','model_id','plan_id','plan_hash','evidence','tool_attempts_used',
        'reservation_ids','reconstruction_generations','recovery_attempted',
        'recovery_reason','completed_claim_ids','completed_results','last_accessed_at'
    ]) OR NOT (value ?& ARRAY[
        'schema_version','run_id','attempt','checkpoint_version','stage','task_level',
        'query_hash','evaluation_root_id','source_manifest_id','corpus_manifest_id',
        'policy_hash','configuration_hash','compliance_prompt_hash','verifier_prompt_hash',
        'provider_id','model_id','plan_id','plan_hash','evidence','tool_attempts_used',
        'reservation_ids','reconstruction_generations','recovery_attempted',
        'recovery_reason','completed_claim_ids','completed_results','last_accessed_at'
    ]) THEN RETURN false; END IF;
    stage_value := value ->> 'stage';
    RETURN value ->> 'schema_version' = 'run-checkpoint/v1'
        AND specpilot_trace_uuid(value -> 'run_id')
        AND specpilot_trace_integer(value -> 'attempt', 1, 2147483647)
        AND specpilot_trace_integer(value -> 'checkpoint_version', 1, 2147483647)
        AND stage_value IN ('planned','evidence_collected','candidate_built',
                            'deterministic_verified','recovery_completed',
                            'semantic_verified','completed')
        AND value ->> 'task_level' = 'L2'
        AND specpilot_trace_sha256(value -> 'query_hash')
        AND specpilot_trace_identifier(value -> 'evaluation_root_id', 128)
        AND specpilot_trace_sha256(value -> 'source_manifest_id')
        AND specpilot_trace_sha256(value -> 'corpus_manifest_id')
        AND specpilot_trace_sha256(value -> 'policy_hash')
        AND specpilot_trace_sha256(value -> 'configuration_hash')
        AND specpilot_trace_sha256(value -> 'compliance_prompt_hash')
        AND specpilot_trace_sha256(value -> 'verifier_prompt_hash')
        AND specpilot_trace_identifier(value -> 'provider_id', 128)
        AND specpilot_trace_identifier(value -> 'model_id', 128)
        AND (jsonb_typeof(value -> 'plan_id') = 'null'
             OR specpilot_trace_identifier(value -> 'plan_id', 128))
        AND (jsonb_typeof(value -> 'plan_hash') = 'null'
             OR specpilot_trace_sha256(value -> 'plan_hash'))
        AND ((jsonb_typeof(value -> 'plan_id') = 'null') =
             (jsonb_typeof(value -> 'plan_hash') = 'null'))
        AND specpilot_checkpoint_evidence(value -> 'evidence')
        AND specpilot_trace_integer(value -> 'tool_attempts_used', 0, 8)
        AND jsonb_typeof(value -> 'reservation_ids') = 'array'
        AND jsonb_array_length(value -> 'reservation_ids') <= 16
        AND jsonb_typeof(value -> 'reconstruction_generations') = 'array'
        AND jsonb_array_length(value -> 'reconstruction_generations') <= 8
        AND jsonb_typeof(value -> 'recovery_attempted') = 'boolean'
        AND (jsonb_typeof(value -> 'recovery_reason') = 'null'
             OR specpilot_trace_reason(value -> 'recovery_reason'))
        AND (value ->> 'recovery_attempted' = 'true'
             OR jsonb_typeof(value -> 'recovery_reason') = 'null')
        AND (stage_value <> 'recovery_completed'
             OR value ->> 'recovery_attempted' = 'true')
        AND jsonb_typeof(value -> 'completed_claim_ids') = 'array'
        AND jsonb_array_length(value -> 'completed_claim_ids') <= 3
        AND jsonb_typeof(value -> 'completed_results') = 'array'
        AND jsonb_array_length(value -> 'completed_results') <= 3
        AND jsonb_typeof(value -> 'last_accessed_at') = 'string';
END;
$$;

CREATE TABLE specpilot_run_attempt (
    run_id uuid NOT NULL REFERENCES specpilot_run(run_id) ON DELETE CASCADE,
    attempt integer NOT NULL CHECK (attempt >= 1),
    resume_key_hash char(64) CHECK (resume_key_hash IS NULL OR resume_key_hash ~ '^[0-9a-f]{64}$'),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    end_reason varchar(64) CHECK (end_reason IS NULL OR end_reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    PRIMARY KEY (run_id, attempt),
    UNIQUE (run_id, resume_key_hash)
);

CREATE TABLE specpilot_run_checkpoint (
    run_id uuid PRIMARY KEY REFERENCES specpilot_run(run_id) ON DELETE CASCADE,
    checkpoint_version integer NOT NULL CHECK (checkpoint_version >= 1),
    stage varchar(32) NOT NULL CHECK (stage IN (
        'planned','evidence_collected','candidate_built','deterministic_verified',
        'recovery_completed','semantic_verified','completed'
    )),
    payload jsonb NOT NULL CHECK (specpilot_valid_checkpoint(payload)),
    last_accessed_at timestamptz NOT NULL,
    CONSTRAINT specpilot_run_checkpoint_columns_match_payload CHECK (
        checkpoint_version = (payload ->> 'checkpoint_version')::integer
        AND stage = payload ->> 'stage'
        AND run_id::text = payload ->> 'run_id'
        AND last_accessed_at = (payload ->> 'last_accessed_at')::timestamptz
    )
);

CREATE INDEX specpilot_run_checkpoint_active_access_idx
    ON specpilot_run_checkpoint (last_accessed_at)
    WHERE stage <> 'completed';

COMMIT;
