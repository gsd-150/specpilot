-- Introduce immutable corpus-ledger policy epochs without losing the audit
-- rows held by the original one-row-per-corpus schema.

BEGIN;

ALTER TABLE egress_corpus_ledger ADD COLUMN corpus_ledger_id uuid;
UPDATE egress_corpus_ledger
SET corpus_ledger_id = md5(corpus_manifest_id)::uuid;
ALTER TABLE egress_corpus_ledger
    ALTER COLUMN corpus_ledger_id SET NOT NULL,
    ADD COLUMN predecessor_ledger_id uuid;

ALTER TABLE egress_reservation ADD COLUMN corpus_ledger_id uuid;
ALTER TABLE egress_evaluation_root ADD COLUMN corpus_ledger_id uuid;

UPDATE egress_reservation AS reservation
SET corpus_ledger_id = ledger.corpus_ledger_id
FROM egress_corpus_ledger AS ledger
WHERE ledger.corpus_manifest_id = reservation.corpus_manifest_id;

UPDATE egress_evaluation_root AS root
SET corpus_ledger_id = ledger.corpus_ledger_id
FROM egress_corpus_ledger AS ledger
WHERE ledger.corpus_manifest_id = root.corpus_manifest_id;

ALTER TABLE egress_route_disclosure
    DROP CONSTRAINT egress_route_disclosure_corpus_manifest_id_fkey;
ALTER TABLE egress_reservation
    DROP CONSTRAINT egress_reservation_corpus_manifest_id_fkey;
ALTER TABLE egress_corpus_ledger
    DROP CONSTRAINT egress_corpus_ledger_pkey;

ALTER TABLE egress_corpus_ledger
    ADD CONSTRAINT egress_corpus_ledger_pkey PRIMARY KEY (corpus_ledger_id),
    ADD CONSTRAINT egress_corpus_ledger_corpus_policy_key
        UNIQUE (corpus_manifest_id, policy_hash),
    ADD CONSTRAINT egress_corpus_ledger_corpus_epoch_key
        UNIQUE (corpus_manifest_id, corpus_ledger_id),
    ADD CONSTRAINT egress_corpus_ledger_predecessor_key
        UNIQUE (predecessor_ledger_id),
    ADD CONSTRAINT egress_corpus_ledger_predecessor_fkey
        FOREIGN KEY (predecessor_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_ledger_id);

CREATE TABLE egress_corpus_ledger_head (
    corpus_manifest_id text PRIMARY KEY
        CHECK (corpus_manifest_id ~ '^[0-9a-f]{64}$'),
    corpus_ledger_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT egress_corpus_ledger_head_epoch_fkey
        FOREIGN KEY (corpus_manifest_id, corpus_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_manifest_id, corpus_ledger_id)
);

INSERT INTO egress_corpus_ledger_head (corpus_manifest_id, corpus_ledger_id)
SELECT corpus_manifest_id, corpus_ledger_id FROM egress_corpus_ledger;

ALTER TABLE egress_reservation
    ALTER COLUMN corpus_ledger_id SET NOT NULL,
    ADD CONSTRAINT egress_reservation_corpus_epoch_fkey
        FOREIGN KEY (corpus_manifest_id, corpus_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_manifest_id, corpus_ledger_id);
ALTER TABLE egress_evaluation_root
    ALTER COLUMN corpus_ledger_id SET NOT NULL,
    ADD CONSTRAINT egress_evaluation_root_corpus_epoch_fkey
        FOREIGN KEY (corpus_manifest_id, corpus_ledger_id)
        REFERENCES egress_corpus_ledger (corpus_manifest_id, corpus_ledger_id);
ALTER TABLE egress_route_disclosure
    ADD CONSTRAINT egress_route_disclosure_corpus_manifest_id_fkey
        FOREIGN KEY (corpus_manifest_id)
        REFERENCES egress_corpus_ledger_head (corpus_manifest_id);

CREATE INDEX egress_corpus_ledger_manifest_idx
    ON egress_corpus_ledger (corpus_manifest_id);
CREATE INDEX egress_reservation_corpus_ledger_idx
    ON egress_reservation (corpus_ledger_id);
CREATE INDEX egress_evaluation_root_corpus_ledger_idx
    ON egress_evaluation_root (corpus_ledger_id);

COMMIT;
