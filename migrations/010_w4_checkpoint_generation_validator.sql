-- Avoid a PL/pgSQL loop-variable/column-alias collision in the W4 generation
-- validator.  The 007 replacement retained the same final-query ambiguity.

CREATE OR REPLACE FUNCTION specpilot_checkpoint_generations(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE item jsonb;
BEGIN
    IF jsonb_typeof(value) <> 'array' OR jsonb_array_length(value) > 8 THEN
        RETURN false;
    END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) AS t(element) LOOP
        IF NOT specpilot_trace_exact_object(
            item, ARRAY['stage','claim_id','recovery','generation']
        ) OR NOT (item ?& ARRAY['stage','claim_id','recovery','generation'])
            OR item ->> 'stage' NOT IN ('planning','compliance','verifier')
            OR NOT (jsonb_typeof(item -> 'claim_id') = 'null'
                    OR specpilot_trace_sha256(item -> 'claim_id'))
            OR jsonb_typeof(item -> 'recovery') <> 'boolean'
            OR NOT specpilot_trace_integer(item -> 'generation', 0, 2147483647)
            OR ((item ->> 'stage' = 'planning') <>
                (jsonb_typeof(item -> 'claim_id') = 'null'))
        THEN RETURN false; END IF;
    END LOOP;
    IF (SELECT count(*) <> count(DISTINCT
            COALESCE(entry.claim_id, '') || ':' || entry.stage || ':' ||
            entry.recovery || ':' || entry.generation)
        FROM jsonb_array_elements(value) AS t(element)
        CROSS JOIN LATERAL (
            SELECT element ->> 'claim_id' AS claim_id,
                   element ->> 'stage' AS stage,
                   element ->> 'recovery' AS recovery,
                   element ->> 'generation' AS generation
        ) AS entry)
    THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$;
