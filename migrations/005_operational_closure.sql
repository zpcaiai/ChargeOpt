-- Operational closure hardening:
-- - durable revenue-proof evidence snapshots
-- - task leases, retry limits, worker ownership, and timeout diagnostics

ALTER TABLE chargeopt.task_queue
    ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 3;

ALTER TABLE chargeopt.task_queue
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;

ALTER TABLE chargeopt.task_queue
    ADD COLUMN IF NOT EXISTS locked_by text;

ALTER TABLE chargeopt.task_queue
    ADD COLUMN IF NOT EXISTS last_error text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'task_queue_attempts_guard'
          AND conrelid = 'chargeopt.task_queue'::regclass
    ) THEN
        ALTER TABLE chargeopt.task_queue
            ADD CONSTRAINT task_queue_attempts_guard CHECK (attempts >= 0 AND max_attempts >= 1);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_chargeopt_tasks_claim
    ON chargeopt.task_queue (tenant_id, status, scheduled_at, priority, created_at)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS chargeopt.revenue_proof_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE SET NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    algorithm text NOT NULL,
    monthly_net_impact numeric(14, 2) NOT NULL,
    p90_low numeric(14, 2) NOT NULL,
    p90_high numeric(14, 2) NOT NULL,
    evidence_window_hours integer NOT NULL,
    payload jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_revenue_proof_tenant_time
    ON chargeopt.revenue_proof_runs (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chargeopt_revenue_proof_station_time
    ON chargeopt.revenue_proof_runs (station_id, created_at DESC);

ALTER TABLE chargeopt.revenue_proof_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'chargeopt'
          AND tablename = 'revenue_proof_runs'
          AND policyname = 'tenant_isolation_revenue_proof_runs'
    ) THEN
        CREATE POLICY tenant_isolation_revenue_proof_runs ON chargeopt.revenue_proof_runs
            USING (
                COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*')
                OR tenant_id = current_setting('chargeopt.tenant_id', true)
            );
    END IF;
END $$;
