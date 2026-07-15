-- Complete settlement approval, dispute, export, payment, and reversal ledger.

ALTER TABLE chargeopt.vpp_settlement_batches
    DROP CONSTRAINT IF EXISTS vpp_settlement_batches_status_check;
ALTER TABLE chargeopt.vpp_settlement_batches
    ADD CONSTRAINT vpp_settlement_batches_status_check
    CHECK (status IN ('calculating', 'review', 'approved', 'exported', 'paid', 'disputed', 'failed', 'reversed'));
ALTER TABLE chargeopt.vpp_settlement_batches
    ADD COLUMN IF NOT EXISTS exported_at timestamptz;
ALTER TABLE chargeopt.vpp_settlement_batches
    ADD COLUMN IF NOT EXISTS paid_at timestamptz;
ALTER TABLE chargeopt.vpp_settlement_batches
    ADD COLUMN IF NOT EXISTS payment_reference text;
ALTER TABLE chargeopt.vpp_settlement_batches
    ADD COLUMN IF NOT EXISTS reversed_at timestamptz;

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlement_events (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    batch_id text NOT NULL REFERENCES chargeopt.vpp_settlement_batches(id) ON DELETE CASCADE,
    sequence_no bigint NOT NULL,
    event_type text NOT NULL,
    from_status text,
    to_status text NOT NULL,
    actor text NOT NULL,
    reason text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    previous_hash text,
    event_hash text NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, sequence_no),
    UNIQUE (batch_id, event_hash)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlement_disputes (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    batch_id text NOT NULL REFERENCES chargeopt.vpp_settlement_batches(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('open', 'resolved', 'rejected')),
    reason text NOT NULL,
    resolution text,
    raised_by text NOT NULL,
    resolved_by text,
    raised_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_open_settlement_dispute
    ON chargeopt.vpp_settlement_disputes (batch_id) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlement_exports (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    batch_id text NOT NULL REFERENCES chargeopt.vpp_settlement_batches(id) ON DELETE CASCADE,
    format text NOT NULL CHECK (format IN ('csv', 'json')),
    destination text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    row_count integer NOT NULL CHECK (row_count >= 0),
    manifest jsonb NOT NULL,
    status text NOT NULL DEFAULT 'generated' CHECK (status IN ('generated', 'delivered', 'failed')),
    generated_by text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    UNIQUE (batch_id, content_hash, destination)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlement_adjustments (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    batch_id text NOT NULL REFERENCES chargeopt.vpp_settlement_batches(id) ON DELETE RESTRICT,
    adjustment_type text NOT NULL CHECK (adjustment_type IN ('reversal', 'correction')),
    amount numeric(16,2) NOT NULL,
    reason text NOT NULL,
    external_reference text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION chargeopt.prevent_settlement_ledger_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'settlement ledger rows are immutable';
END $$;

DROP TRIGGER IF EXISTS trg_settlement_lines_immutable ON chargeopt.vpp_settlement_lines;
CREATE TRIGGER trg_settlement_lines_immutable
BEFORE UPDATE OR DELETE ON chargeopt.vpp_settlement_lines
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_settlement_ledger_mutation();

DROP TRIGGER IF EXISTS trg_settlement_events_immutable ON chargeopt.vpp_settlement_events;
CREATE TRIGGER trg_settlement_events_immutable
BEFORE UPDATE OR DELETE ON chargeopt.vpp_settlement_events
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_settlement_ledger_mutation();

DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'vpp_settlement_events', 'vpp_settlement_disputes',
        'vpp_settlement_exports', 'vpp_settlement_adjustments'
    ]
    LOOP
        policy_name := 'tenant_isolation_' || table_name;
        EXECUTE format('ALTER TABLE chargeopt.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE chargeopt.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS %I ON chargeopt.%I', policy_name, table_name);
        EXECUTE format(
            'CREATE POLICY %I ON chargeopt.%I USING (NULLIF(current_setting(''chargeopt.tenant_id'', true), '''') IS NOT NULL AND (current_setting(''chargeopt.tenant_id'', true) = ''*'' OR tenant_id = current_setting(''chargeopt.tenant_id'', true))) WITH CHECK (NULLIF(current_setting(''chargeopt.tenant_id'', true), '''') IS NOT NULL AND (current_setting(''chargeopt.tenant_id'', true) = ''*'' OR tenant_id = current_setting(''chargeopt.tenant_id'', true)))',
            policy_name,
            table_name
        );
    END LOOP;
END $$;
