-- A bounded cursor is sufficient to resume between claims without retaining
-- candidate prose.  Re-declare the closed JSON validator with this one field.

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
        'recovery_reason','candidate_count','completed_claim_ids','completed_results',
        'last_accessed_at'
    ]) OR NOT (value ?& ARRAY[
        'schema_version','run_id','attempt','checkpoint_version','stage','task_level',
        'query_hash','evaluation_root_id','source_manifest_id','corpus_manifest_id',
        'policy_hash','configuration_hash','compliance_prompt_hash','verifier_prompt_hash',
        'provider_id','model_id','plan_id','plan_hash','evidence','tool_attempts_used',
        'reservation_ids','reconstruction_generations','recovery_attempted',
        'recovery_reason','candidate_count','completed_claim_ids','completed_results',
        'last_accessed_at'
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
        AND specpilot_checkpoint_uuids(value -> 'reservation_ids', 16)
        AND specpilot_checkpoint_generations(value -> 'reconstruction_generations')
        AND jsonb_typeof(value -> 'recovery_attempted') = 'boolean'
        AND (jsonb_typeof(value -> 'recovery_reason') = 'null'
             OR specpilot_trace_reason(value -> 'recovery_reason'))
        AND (value ->> 'recovery_attempted' = 'true'
             OR jsonb_typeof(value -> 'recovery_reason') = 'null')
        AND (stage_value <> 'recovery_completed'
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
