-- Admit only the opaque local-cache hit summary.  Provider responses, cache
-- paths, keys, run/session identities, and prompt content remain outside the
-- durable run trace.

BEGIN;

ALTER TABLE specpilot_run_event
    DROP CONSTRAINT specpilot_run_event_kind_check,
    ADD CONSTRAINT specpilot_run_event_kind_check CHECK (
        kind IN (
            'state_transition', 'plan_summary', 'agent_step', 'tool_finished',
            'candidate_summary', 'evidence_summary', 'egress_summary',
            'cache_summary', 'usage_summary', 'answer_outcome',
            'verifier_summary', 'terminal', 'checkpoint_summary',
            'compliance_summary', 'semantic_summary', 'recovery_summary',
            'resume_summary'
        )
    );

ALTER FUNCTION specpilot_valid_w4_run_event(text, integer, jsonb)
    RENAME TO specpilot_valid_w4_run_event_v15;

CREATE FUNCTION specpilot_valid_w4_run_event(
    event_kind text, event_sequence integer, event_payload jsonb
)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    IF event_kind <> 'cache_summary' THEN
        RETURN specpilot_valid_w4_run_event_v15(
            event_kind, event_sequence, event_payload
        );
    END IF;
    RETURN specpilot_trace_exact_object(event_payload, ARRAY[
            'kind','sequence','hit','stage','request_hash','record_hash'
        ])
        AND event_payload ?& ARRAY[
            'kind','sequence','hit','stage','request_hash','record_hash'
        ]
        AND event_payload ->> 'kind' = event_kind
        AND specpilot_trace_integer(event_payload -> 'sequence', 1, 10000)
        AND (event_payload ->> 'sequence')::integer = event_sequence
        AND jsonb_typeof(event_payload -> 'hit') = 'boolean'
        AND event_payload ->> 'stage' IN (
            'planning','evidence','compliance','verifier','judge'
        )
        AND specpilot_trace_sha256(event_payload -> 'request_hash')
        AND specpilot_trace_sha256(event_payload -> 'record_hash');
END;
$$;

ALTER TABLE specpilot_run_event
    DROP CONSTRAINT specpilot_run_event_payload_check,
    ADD CONSTRAINT specpilot_run_event_payload_check
        CHECK (specpilot_valid_w4_run_event(kind, sequence, payload));

COMMIT;
