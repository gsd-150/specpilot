-- A reserved recovery action is bound to one opaque claim identity.  The
-- field exists only in that pre-MCP crash window; no claim prose is retained.

-- Version 013 did not retain the claim that owns a reserved recovery action.
-- It cannot be inferred safely, so reject this upgrade before dropping any
-- constraint or changing even a non-reserved checkpoint payload. Operators
-- must resume or resolve those runs under 013 first; inventing an owner would
-- let a later reconstructed batch attach a lost MCP result to the wrong claim.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM specpilot_run_checkpoint
        WHERE stage = 'recovery_reserved'
           OR payload ->> 'stage' = 'recovery_reserved'
    ) THEN
        RAISE EXCEPTION
            'W4_014_RECOVERY_RESERVED_DRAIN_REQUIRED: resolve recovery_reserved checkpoints before migration 014';
    END IF;
END;
$$;

BEGIN;

ALTER TABLE specpilot_run_checkpoint
    DROP CONSTRAINT specpilot_run_checkpoint_payload_check;

UPDATE specpilot_run_checkpoint
SET payload = jsonb_set(payload, '{recovery_claim_id}', 'null'::jsonb, true)
WHERE NOT payload ? 'recovery_claim_id';

CREATE OR REPLACE FUNCTION specpilot_valid_checkpoint(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE stage_value text;
BEGIN
    IF NOT specpilot_trace_exact_object(value, ARRAY[
        'schema_version','run_id','attempt','checkpoint_version','stage','task_level',
        'query_hash','evaluation_root_id','source_manifest_id','corpus_manifest_id',
        'policy_hash','configuration_hash','compliance_prompt_hash','verifier_prompt_hash',
        'provider_id','model_id','plan_id','plan_hash','evidence','tool_attempts_used',
        'reservation_ids','reconstruction_generations','recovery_attempted',
        'recovery_reason','recovery_claim_id','candidate_count','completed_claim_ids',
        'completed_results','last_accessed_at'
    ]) OR NOT (value ?& ARRAY[
        'schema_version','run_id','attempt','checkpoint_version','stage','task_level',
        'query_hash','evaluation_root_id','source_manifest_id','corpus_manifest_id',
        'policy_hash','configuration_hash','compliance_prompt_hash','verifier_prompt_hash',
        'provider_id','model_id','plan_id','plan_hash','evidence','tool_attempts_used',
        'reservation_ids','reconstruction_generations','recovery_attempted',
        'recovery_reason','recovery_claim_id','candidate_count','completed_claim_ids',
        'completed_results','last_accessed_at'
    ]) THEN RETURN false; END IF;
    stage_value := value ->> 'stage';
    RETURN value ->> 'schema_version' = 'run-checkpoint/v1'
        AND specpilot_trace_uuid(value -> 'run_id')
        AND specpilot_trace_integer(value -> 'attempt', 1, 2147483647)
        AND specpilot_trace_integer(value -> 'checkpoint_version', 1, 2147483647)
        AND stage_value IN ('planned','evidence_collected','candidate_built',
                            'deterministic_verified','recovery_reserved',
                            'recovery_completed','semantic_verified','completed')
        AND value ->> 'task_level' = 'L2'
        AND specpilot_trace_sha256(value -> 'query_hash')
        AND specpilot_trace_identifier(value -> 'evaluation_root_id', 128)
        AND specpilot_trace_sha256(value -> 'source_manifest_id')
        AND specpilot_trace_sha256(value -> 'corpus_manifest_id')
        AND specpilot_trace_sha256(value -> 'policy_hash')
        AND specpilot_trace_sha256(value -> 'configuration_hash')
        AND specpilot_trace_sha256(value -> 'compliance_prompt_hash')
        AND specpilot_trace_sha256(value -> 'verifier_prompt_hash')
        AND value ->> 'compliance_prompt_hash' <> value ->> 'verifier_prompt_hash'
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
        AND specpilot_checkpoint_uuids(value -> 'reservation_ids', 8)
        AND specpilot_checkpoint_generations(value -> 'reconstruction_generations')
        AND jsonb_typeof(value -> 'recovery_attempted') = 'boolean'
        AND (jsonb_typeof(value -> 'recovery_reason') = 'null'
             OR specpilot_trace_reason(value -> 'recovery_reason'))
        AND (value ->> 'recovery_attempted' = 'true'
             OR jsonb_typeof(value -> 'recovery_reason') = 'null')
        AND (
            (stage_value = 'recovery_reserved'
             AND specpilot_trace_sha256(value -> 'recovery_claim_id'))
            OR (stage_value <> 'recovery_reserved'
                AND jsonb_typeof(value -> 'recovery_claim_id') = 'null')
        )
        AND (stage_value NOT IN ('recovery_reserved','recovery_completed')
             OR value ->> 'recovery_attempted' = 'true')
        AND specpilot_trace_integer(value -> 'candidate_count', 0, 3)
        AND specpilot_checkpoint_results(value -> 'completed_results')
        AND jsonb_array_length(value -> 'completed_results') <=
            (value ->> 'candidate_count')::integer
        AND specpilot_checkpoint_result_ids(
            value -> 'completed_claim_ids', value -> 'completed_results'
        )
        AND jsonb_typeof(value -> 'last_accessed_at') = 'string';
END;
$$;

ALTER TABLE specpilot_run_checkpoint
    ADD CONSTRAINT specpilot_run_checkpoint_payload_check
    CHECK (specpilot_valid_checkpoint(payload));

COMMIT;
