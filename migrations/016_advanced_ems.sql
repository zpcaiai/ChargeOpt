-- Advanced EMS evidence ledger for probabilistic forecasting, risk-aware MPC,
-- distribution screening, portfolio coordination, and shadow policy evaluation.

CREATE TABLE IF NOT EXISTS chargeopt.ems_evidence_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    evidence_type text NOT NULL CHECK (evidence_type IN (
        'forecast','dispatch','network_projection','portfolio_coordination','offline_policy_evaluation'
    )),
    algorithm_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('completed','rejected')),
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed')),
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    request_payload jsonb NOT NULL,
    result_payload jsonb NOT NULL,
    idempotency_key text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, evidence_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_ems_evidence_latest
    ON chargeopt.ems_evidence_runs (tenant_id, evidence_type, station_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chargeopt_ems_evidence_input
    ON chargeopt.ems_evidence_runs (tenant_id, input_hash);

DROP TRIGGER IF EXISTS trg_immutable_ems_evidence_runs ON chargeopt.ems_evidence_runs;
CREATE TRIGGER trg_immutable_ems_evidence_runs
BEFORE UPDATE OR DELETE ON chargeopt.ems_evidence_runs
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_twin_evidence_mutation();

ALTER TABLE chargeopt.ems_evidence_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.ems_evidence_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_ems_evidence_runs ON chargeopt.ems_evidence_runs;
CREATE POLICY tenant_isolation_ems_evidence_runs ON chargeopt.ems_evidence_runs
USING (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (
        current_setting('chargeopt.tenant_id', true) = '*'
        OR tenant_id = current_setting('chargeopt.tenant_id', true)
    )
)
WITH CHECK (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (
        current_setting('chargeopt.tenant_id', true) = '*'
        OR tenant_id = current_setting('chargeopt.tenant_id', true)
    )
);

GRANT SELECT, INSERT ON chargeopt.ems_evidence_runs TO chargeopt_app;
