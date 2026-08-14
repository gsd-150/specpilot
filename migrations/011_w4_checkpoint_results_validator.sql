-- Keep the completed-result duplicate check executable under PL/pgSQL's
-- variable-conflict rules.  This is an additive repair for databases that
-- already applied 006.

CREATE OR REPLACE FUNCTION specpilot_checkpoint_results(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE item jsonb; citation jsonb;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 3 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element) LOOP
        IF NOT specpilot_trace_exact_object(
                item, ARRAY['claim_id','verdict','verification_status','citations','reason_code']
            ) OR NOT (item ?& ARRAY[
                'claim_id','verdict','verification_status','citations','reason_code'
            ]) OR NOT specpilot_trace_sha256(item -> 'claim_id')
            OR item ->> 'verdict' NOT IN ('compliant','violating','insufficient_evidence')
            OR item ->> 'verification_status' NOT IN (
                'verified','deterministic_failed','semantic_failed','insufficient'
            ) OR NOT (jsonb_typeof(item -> 'reason_code') = 'null'
                       OR specpilot_trace_identifier(item -> 'reason_code', 128))
            OR jsonb_typeof(item -> 'citations') <> 'array'
        THEN RETURN false; END IF;
        IF item ->> 'verdict' = 'insufficient_evidence'
            AND jsonb_array_length(item -> 'citations') <> 0 THEN RETURN false; END IF;
        IF item ->> 'verdict' <> 'insufficient_evidence'
            AND (item ->> 'verification_status' <> 'verified'
                 OR jsonb_array_length(item -> 'citations') = 0) THEN RETURN false; END IF;
        FOR citation IN SELECT element FROM jsonb_array_elements(item -> 'citations') AS t(element) LOOP
            IF NOT specpilot_trace_exact_object(citation, ARRAY[
                    'clause_id','corpus_manifest_id','document_id','document_version',
                    'section_number','content_hash'
                ]) OR NOT (citation ?& ARRAY[
                    'clause_id','corpus_manifest_id','document_id','document_version',
                    'section_number','content_hash'
                ]) OR NOT specpilot_trace_sha256(citation -> 'clause_id')
                OR NOT specpilot_trace_sha256(citation -> 'corpus_manifest_id')
                OR NOT specpilot_trace_identifier(citation -> 'document_id', 128)
                OR NOT specpilot_trace_identifier(citation -> 'document_version', 128)
                OR NOT (jsonb_typeof(citation -> 'section_number') = 'null'
                        OR specpilot_trace_identifier(citation -> 'section_number', 64))
                OR NOT specpilot_trace_sha256(citation -> 'content_hash')
            THEN RETURN false; END IF;
        END LOOP;
    END LOOP;
    IF (SELECT count(*) <> count(DISTINCT entry.claim_id)
        FROM jsonb_array_elements(value) AS t(element)
        CROSS JOIN LATERAL (SELECT element ->> 'claim_id' AS claim_id) AS entry)
    THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$;
