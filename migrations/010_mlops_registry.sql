-- Auditable model lifecycle for forecasting and optimization policies.

CREATE TABLE IF NOT EXISTS chargeopt.model_registry (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope text NOT NULL,
    version text NOT NULL,
    algorithm text NOT NULL,
    artifact_uri text NOT NULL,
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    training_data_hash text NOT NULL CHECK (training_data_hash ~ '^[0-9a-f]{64}$'),
    training_window_start timestamptz NOT NULL,
    training_window_end timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'shadow', 'active', 'rejected', 'retired')),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, scope, version),
    CHECK (training_window_end > training_window_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_active_model_scope
    ON chargeopt.model_registry (tenant_id, scope) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS chargeopt.model_evaluations (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    model_id text NOT NULL REFERENCES chargeopt.model_registry(id) ON DELETE CASCADE,
    dataset_hash text NOT NULL CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    sample_count integer NOT NULL CHECK (sample_count > 0),
    metrics jsonb NOT NULL,
    quality_gate jsonb NOT NULL,
    drift_detected boolean NOT NULL DEFAULT false,
    evaluated_by text NOT NULL,
    evaluated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_model_evaluation_latest
    ON chargeopt.model_evaluations (tenant_id, model_id, evaluated_at DESC);

ALTER TABLE chargeopt.model_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.model_registry FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_model_registry ON chargeopt.model_registry;
CREATE POLICY tenant_isolation_model_registry ON chargeopt.model_registry
USING (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (current_setting('chargeopt.tenant_id', true) = '*' OR tenant_id = current_setting('chargeopt.tenant_id', true))
)
WITH CHECK (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (current_setting('chargeopt.tenant_id', true) = '*' OR tenant_id = current_setting('chargeopt.tenant_id', true))
);

ALTER TABLE chargeopt.model_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.model_evaluations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_model_evaluations ON chargeopt.model_evaluations;
CREATE POLICY tenant_isolation_model_evaluations ON chargeopt.model_evaluations
USING (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (current_setting('chargeopt.tenant_id', true) = '*' OR tenant_id = current_setting('chargeopt.tenant_id', true))
)
WITH CHECK (
    NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL
    AND (current_setting('chargeopt.tenant_id', true) = '*' OR tenant_id = current_setting('chargeopt.tenant_id', true))
);
