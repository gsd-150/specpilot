-- Repair the W4 checkpoint validator for PostgreSQL's PL/pgSQL name resolver.
--
-- The original function declared ``item`` for its loop and reused that name as
-- a set-returning-function column alias in the final duplicate check.  Fresh
-- databases therefore rejected even an empty checkpoint evidence array before
-- the first L2 provider reservation.  Existing deployments retain all rows;
-- only the immutable validator body is replaced.

CREATE OR REPLACE FUNCTION specpilot_checkpoint_evidence(value jsonb)
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
    IF (SELECT count(*) <> count(DISTINCT entry.evidence_id)
        FROM jsonb_array_elements(value) AS t(element)
        CROSS JOIN LATERAL (SELECT element ->> 'evidence_id' AS evidence_id) AS entry)
    THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$;
