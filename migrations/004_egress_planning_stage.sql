-- Admit the source-free L1 planning stage to the existing reservation audit.
--
-- The original four stages remain valid and no free-form stage is introduced.
-- Replacing the named constraint inside one transaction is safe for populated
-- 001--003 ledgers: every existing row already satisfied the stricter prior
-- constraint, and the table contents are unchanged.

BEGIN;

ALTER TABLE egress_reservation
    DROP CONSTRAINT egress_reservation_stage_check,
    ADD CONSTRAINT egress_reservation_stage_check
        CHECK (
            stage IN (
                'planning', 'evidence', 'compliance', 'verifier', 'judge'
            )
        );

COMMIT;
