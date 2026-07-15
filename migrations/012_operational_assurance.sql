-- HA/DR assurance, SLO evidence, shadow qualification, and live market gates.

ALTER TABLE chargeopt.market_connections
    ADD COLUMN IF NOT EXISTS market_certificate_status text NOT NULL DEFAULT 'pending'
        CHECK (market_certificate_status IN ('pending', 'verified', 'revoked', 'expired'));
ALTER TABLE chargeopt.market_connections
    ADD COLUMN IF NOT EXISTS market_certificate_expires_at timestamptz;
ALTER TABLE chargeopt.market_connections
    ADD COLUMN IF NOT EXISTS trading_qualification_status text NOT NULL DEFAULT 'pending'
        CHECK (trading_qualification_status IN ('pending', 'verified', 'rejected', 'expired'));
ALTER TABLE chargeopt.market_connections
    ADD COLUMN IF NOT EXISTS device_credentials_attested_at timestamptz;
ALTER TABLE chargeopt.market_connections
    ADD COLUMN IF NOT EXISTS external_readiness_evidence jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS chargeopt.operational_incidents (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    fingerprint text NOT NULL,
    component text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('warning', 'critical')),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'resolved')),
    summary text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_detected_at timestamptz NOT NULL DEFAULT now(),
    last_detected_at timestamptz NOT NULL DEFAULT now(),
    acknowledged_by text,
    resolved_by text,
    resolved_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_open_incident_fingerprint
    ON chargeopt.operational_incidents (tenant_id, fingerprint) WHERE status IN ('open', 'acknowledged');

CREATE TABLE IF NOT EXISTS chargeopt.slo_measurements (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    metric text NOT NULL,
    value numeric(18,6) NOT NULL,
    target numeric(18,6) NOT NULL,
    compliant boolean NOT NULL,
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (window_end > window_start)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_slo_metric_window
    ON chargeopt.slo_measurements (tenant_id, metric, window_end DESC);

CREATE TABLE IF NOT EXISTS chargeopt.shadow_run_evidence (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    evidence_date date NOT NULL,
    automation_cycles integer NOT NULL CHECK (automation_cycles >= 0),
    completed_cycles integer NOT NULL CHECK (completed_cycles >= 0),
    failed_cycles integer NOT NULL CHECK (failed_cycles >= 0),
    orders_created integer NOT NULL CHECK (orders_created >= 0),
    reconciled_orders integer NOT NULL CHECK (reconciled_orders >= 0),
    reconciliation_mismatches integer NOT NULL CHECK (reconciliation_mismatches >= 0),
    outbox_dead_letters integer NOT NULL CHECK (outbox_dead_letters >= 0),
    dispatch_failures integer NOT NULL CHECK (dispatch_failures >= 0),
    settlement_failures integer NOT NULL CHECK (settlement_failures >= 0),
    critical_incidents integer NOT NULL CHECK (critical_incidents >= 0),
    qualified boolean NOT NULL,
    qualification_reasons jsonb NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, evidence_date)
);

CREATE TABLE IF NOT EXISTS chargeopt.recovery_drills (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    provider text NOT NULL,
    source_timestamp timestamptz NOT NULL,
    restored_branch_id text,
    status text NOT NULL CHECK (status IN ('running', 'passed', 'failed')),
    rpo_seconds integer,
    rto_seconds integer,
    validation jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    initiated_by text NOT NULL
);

CREATE OR REPLACE FUNCTION chargeopt.prevent_shadow_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'shadow evidence is immutable';
END $$;

DROP TRIGGER IF EXISTS trg_shadow_evidence_immutable ON chargeopt.shadow_run_evidence;
CREATE TRIGGER trg_shadow_evidence_immutable
BEFORE UPDATE OR DELETE ON chargeopt.shadow_run_evidence
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_shadow_evidence_mutation();

DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'operational_incidents', 'slo_measurements', 'shadow_run_evidence', 'recovery_drills'
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
