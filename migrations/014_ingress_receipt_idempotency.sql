-- Durable idempotency keys for protocol ingress and edge command receipts.
-- Nullable keeps pre-existing rows valid; new integrations should always send
-- a stable key from the device or gateway.

ALTER TABLE chargeopt.protocol_messages
    ADD COLUMN IF NOT EXISTS idempotency_key text;

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_protocol_message_idempotency
    ON chargeopt.protocol_messages (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE chargeopt.edge_command_receipts
    ADD COLUMN IF NOT EXISTS idempotency_key text;

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_edge_receipt_idempotency
    ON chargeopt.edge_command_receipts (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
