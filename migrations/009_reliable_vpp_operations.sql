-- Reliable VPP operations:
-- - transactional outbox leases, retries, and dead-letter state
-- - order reconciliation evidence
-- - scheduler/worker heartbeats
-- - fail-closed tenant RLS when no tenant context is present

ALTER TABLE chargeopt.vpp_outbox
    ADD COLUMN IF NOT EXISTS event_key text;
ALTER TABLE chargeopt.vpp_outbox
    ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 8;
ALTER TABLE chargeopt.vpp_outbox
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
ALTER TABLE chargeopt.vpp_outbox
    ADD COLUMN IF NOT EXISTS locked_by text;
ALTER TABLE chargeopt.vpp_outbox
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

UPDATE chargeopt.vpp_outbox SET event_key = id WHERE event_key IS NULL;
ALTER TABLE chargeopt.vpp_outbox ALTER COLUMN event_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_outbox_event_key
    ON chargeopt.vpp_outbox (tenant_id, event_key);

ALTER TABLE chargeopt.market_orders
    ADD COLUMN IF NOT EXISTS reconciliation_status text NOT NULL DEFAULT 'pending';
ALTER TABLE chargeopt.market_orders
    ADD COLUMN IF NOT EXISTS last_reconciled_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'market_orders_reconciliation_status_guard'
          AND conrelid = 'chargeopt.market_orders'::regclass
    ) THEN
        ALTER TABLE chargeopt.market_orders
            ADD CONSTRAINT market_orders_reconciliation_status_guard
            CHECK (reconciliation_status IN ('pending', 'matched', 'mismatch', 'unknown', 'not_applicable'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS chargeopt.vpp_operational_heartbeats (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    component text NOT NULL,
    instance_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('healthy', 'degraded', 'failed')),
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_vpp_heartbeats_component_time
    ON chargeopt.vpp_operational_heartbeats (tenant_id, component, observed_at DESC);

-- Recover legacy and interrupted work. A running task without a lease can never
-- be reclaimed by the normal lease predicate, so make it explicitly retryable.
UPDATE chargeopt.task_queue
SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
    locked_at = NULL,
    lease_expires_at = NULL,
    locked_by = NULL,
    last_error = COALESCE(last_error, 'recovered running task without lease'),
    scheduled_at = now(),
    completed_at = CASE WHEN attempts >= max_attempts THEN now() ELSE completed_at END,
    updated_at = now()
WHERE status = 'running' AND lease_expires_at IS NULL;

UPDATE chargeopt.vpp_outbox
SET status = CASE WHEN attempts >= max_attempts THEN 'dead_letter' ELSE 'failed' END,
    locked_by = NULL,
    lease_expires_at = NULL,
    last_error = COALESCE(last_error, 'recovered abandoned outbox lease'),
    available_at = now(),
    updated_at = now()
WHERE status = 'publishing'
  AND (lease_expires_at IS NULL OR lease_expires_at < now());

-- Replace permissive policies. Missing/empty tenant context now evaluates to
-- false. Platform-wide access remains explicit through tenant context '*'.
DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'stations', 'vpp_events', 'dispatch_recommendations', 'audit_entries',
        'devices', 'protocol_messages', 'task_queue', 'dispatch_approvals',
        'edge_command_receipts', 'optimization_runs', 'vpp_settlements',
        'revenue_proof_runs', 'market_connections', 'vpp_risk_policies',
        'vpp_forecast_runs', 'market_orders', 'market_order_events',
        'market_trades', 'delivery_schedules', 'vpp_meter_intervals',
        'vpp_settlement_batches', 'vpp_settlement_lines', 'vpp_circuit_breakers',
        'vpp_automation_runs', 'vpp_outbox', 'vpp_operational_heartbeats'
    ]
    LOOP
        policy_name := 'tenant_isolation_' || table_name;
        EXECUTE format('ALTER TABLE chargeopt.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS %I ON chargeopt.%I', policy_name, table_name);
        EXECUTE format(
            'CREATE POLICY %I ON chargeopt.%I USING (NULLIF(current_setting(''chargeopt.tenant_id'', true), '''') IS NOT NULL AND (current_setting(''chargeopt.tenant_id'', true) = ''*'' OR tenant_id = current_setting(''chargeopt.tenant_id'', true))) WITH CHECK (NULLIF(current_setting(''chargeopt.tenant_id'', true), '''') IS NOT NULL AND (current_setting(''chargeopt.tenant_id'', true) = ''*'' OR tenant_id = current_setting(''chargeopt.tenant_id'', true)))',
            policy_name,
            table_name
        );
        EXECUTE format('ALTER TABLE chargeopt.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;
END $$;

DROP POLICY IF EXISTS tenant_isolation_telemetry ON chargeopt.telemetry_points;
CREATE POLICY tenant_isolation_telemetry ON chargeopt.telemetry_points
USING (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (
        current_setting('chargeopt.tenant_id', true) = '*'
        OR EXISTS (
            SELECT 1 FROM chargeopt.stations s
            WHERE s.id = station_id
              AND s.tenant_id = current_setting('chargeopt.tenant_id', true)
        )
    )
);

DROP POLICY IF EXISTS tenant_isolation_alerts ON chargeopt.alerts;
CREATE POLICY tenant_isolation_alerts ON chargeopt.alerts
USING (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (
        current_setting('chargeopt.tenant_id', true) = '*'
        OR EXISTS (
            SELECT 1 FROM chargeopt.stations s
            WHERE s.id = station_id
              AND s.tenant_id = current_setting('chargeopt.tenant_id', true)
        )
    )
);

DROP POLICY IF EXISTS tenant_isolation_roi_simulations ON chargeopt.roi_simulations;
CREATE POLICY tenant_isolation_roi_simulations ON chargeopt.roi_simulations
USING (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (
        current_setting('chargeopt.tenant_id', true) = '*'
        OR EXISTS (
            SELECT 1 FROM chargeopt.stations s
            WHERE s.id = station_id
              AND s.tenant_id = current_setting('chargeopt.tenant_id', true)
        )
    )
);
