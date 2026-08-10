-- Rename the attempt columns to say which quantity they hold.
--
-- `transmitted_*` in this system means corpus content counted with repetition:
-- what §3.2's transmitted ledger bounds at four times the unique cap, computed
-- by the enforcer at reserve time. These columns never held that. They hold the
-- measured size of the request that actually went out — prompt, reply contract,
-- attribution, question, labels and quotes together — which no cap reads.
--
-- The two shared a name, and the two writers disagreed about which one they
-- were storing: the answer path recorded the real request size while
-- PolicyBoundTransport recorded the enforcer's content projection into the same
-- column. Rows written before this migration may therefore be either quantity,
-- distinguishable only by which caller produced them.
--
-- A rename, not a rewrite: the values stay, so nothing that was recorded is
-- lost or silently reinterpreted. This touches no policy field, so `policy_hash`
-- is unchanged and no corpus ledger row is invalidated.

ALTER TABLE egress_attempt RENAME COLUMN transmitted_tokens TO request_tokens;
ALTER TABLE egress_attempt RENAME COLUMN transmitted_bytes  TO request_bytes;

ALTER TABLE egress_attempt
    RENAME CONSTRAINT egress_attempt_transmitted_tokens_check
    TO egress_attempt_request_tokens_check;
ALTER TABLE egress_attempt
    RENAME CONSTRAINT egress_attempt_transmitted_bytes_check
    TO egress_attempt_request_bytes_check;
