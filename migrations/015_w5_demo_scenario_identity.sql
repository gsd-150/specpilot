-- Closed server-owned identity for offline-script reconstruction after restart.
-- This contains no fixture question, reply, or provider transcript.
ALTER TABLE specpilot_run
    ADD COLUMN demo_scenario_id text;

ALTER TABLE specpilot_run
    ADD CONSTRAINT specpilot_run_demo_scenario_id_check
    CHECK (
        demo_scenario_id IS NULL
        OR demo_scenario_id IN (
            'l1_answered',
            'l2_answered',
            'evidence_refused',
            'verifier_recovered'
        )
    );
