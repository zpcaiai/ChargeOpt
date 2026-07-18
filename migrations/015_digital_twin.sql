-- Charging-station digital twin: topology, historian, derived state, simulation,
-- diagnostics, causal evidence, and field qualification.

CREATE TABLE IF NOT EXISTS chargeopt.twin_topology_versions (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'active', 'retired', 'rejected')),
    topology_hash text NOT NULL CHECK (topology_hash ~ '^[0-9a-f]{64}$'),
    validation_report jsonb NOT NULL DEFAULT '{}'::jsonb,
    valid_from timestamptz,
    valid_to timestamptz,
    created_by text NOT NULL,
    activated_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    UNIQUE (tenant_id, station_id, version),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_twin_active_topology
    ON chargeopt.twin_topology_versions (tenant_id, station_id)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS chargeopt.twin_assets (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.twin_topology_versions(id) ON DELETE CASCADE,
    asset_key text NOT NULL,
    asset_type text NOT NULL CHECK (asset_type IN (
        'station','transformer','bus','meter','charger','connector','pcs','battery_system',
        'battery_rack','battery_pack','pv_inverter','sensor','gateway'
    )),
    name text NOT NULL,
    manufacturer text,
    model text,
    serial_number text,
    rated_power_kw numeric(14,3) CHECK (rated_power_kw IS NULL OR rated_power_kw >= 0),
    rated_energy_kwh numeric(14,3) CHECK (rated_energy_kwh IS NULL OR rated_energy_kwh >= 0),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (topology_version_id, asset_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.twin_asset_relationships (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.twin_topology_versions(id) ON DELETE CASCADE,
    source_asset_id text NOT NULL REFERENCES chargeopt.twin_assets(id) ON DELETE CASCADE,
    target_asset_id text NOT NULL REFERENCES chargeopt.twin_assets(id) ON DELETE CASCADE,
    relationship_type text NOT NULL CHECK (relationship_type IN (
        'contains','feeds','meters','controls','communicates_with','measures'
    )),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (source_asset_id <> target_asset_id),
    UNIQUE (topology_version_id, source_asset_id, target_asset_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS chargeopt.twin_measurements (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    asset_id text REFERENCES chargeopt.twin_assets(id) ON DELETE SET NULL,
    point_code text NOT NULL,
    numeric_value numeric(20,8) NOT NULL,
    unit text NOT NULL,
    source_timestamp timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    sequence_number bigint,
    source text NOT NULL,
    quality_code text NOT NULL CHECK (quality_code IN ('good','suspect','bad','substituted','estimated')),
    quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    idempotency_key text NOT NULL,
    UNIQUE (tenant_id, source, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_twin_measurement_window
    ON chargeopt.twin_measurements (tenant_id, station_id, point_code, source_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_chargeopt_twin_measurement_asset_window
    ON chargeopt.twin_measurements (tenant_id, asset_id, source_timestamp DESC);

CREATE TABLE IF NOT EXISTS chargeopt.twin_historian_policies (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    raw_retention_days integer NOT NULL CHECK (raw_retention_days >= 30),
    minute_retention_days integer NOT NULL CHECK (minute_retention_days >= raw_retention_days),
    hourly_retention_days integer NOT NULL CHECK (hourly_retention_days >= minute_retention_days),
    late_arrival_window_seconds integer NOT NULL CHECK (late_arrival_window_seconds BETWEEN 30 AND 86400),
    clock_skew_tolerance_seconds integer NOT NULL CHECK (clock_skew_tolerance_seconds BETWEEN 1 AND 3600),
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, station_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.twin_state_estimates (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    asset_id text REFERENCES chargeopt.twin_assets(id) ON DELETE SET NULL,
    topology_version_id text REFERENCES chargeopt.twin_topology_versions(id) ON DELETE SET NULL,
    state_code text NOT NULL,
    estimated_value numeric(20,8) NOT NULL,
    unit text NOT NULL,
    confidence_low numeric(20,8) NOT NULL,
    confidence_high numeric(20,8) NOT NULL,
    trust_score numeric(8,6) NOT NULL CHECK (trust_score BETWEEN 0 AND 1),
    residual numeric(20,8),
    algorithm text NOT NULL,
    model_version text NOT NULL,
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed','field_qualified')),
    quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    estimated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_twin_state_latest
    ON chargeopt.twin_state_estimates (tenant_id, station_id, state_code, estimated_at DESC);

CREATE TABLE IF NOT EXISTS chargeopt.twin_model_calibrations (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    topology_version_id text REFERENCES chargeopt.twin_topology_versions(id) ON DELETE SET NULL,
    model_scope text NOT NULL,
    model_version text NOT NULL,
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed','field_qualified')),
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    sample_count integer NOT NULL CHECK (sample_count > 0),
    parameters jsonb NOT NULL,
    metrics jsonb NOT NULL,
    quality_gate jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('passed','failed','insufficient_evidence')),
    calibrated_by text NOT NULL,
    calibrated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_twin_calibration_latest
    ON chargeopt.twin_model_calibrations (tenant_id, station_id, model_scope, calibrated_at DESC);

CREATE TABLE IF NOT EXISTS chargeopt.twin_simulation_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    topology_version_id text REFERENCES chargeopt.twin_topology_versions(id) ON DELETE SET NULL,
    scenario_type text NOT NULL CHECK (scenario_type IN ('replay','what_if','shadow','commissioning')),
    status text NOT NULL CHECK (status IN ('running','completed','failed','rejected')),
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed','field_qualified')),
    algorithm_version text NOT NULL,
    random_seed bigint NOT NULL,
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    configuration jsonb NOT NULL,
    inputs jsonb NOT NULL,
    outputs jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text NOT NULL,
    initiated_by text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.twin_diagnostics (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    asset_id text REFERENCES chargeopt.twin_assets(id) ON DELETE SET NULL,
    fingerprint text NOT NULL,
    diagnostic_type text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info','warning','high','critical')),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved','false_positive')),
    confidence numeric(8,6) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    summary text NOT NULL,
    likely_causes jsonb NOT NULL,
    evidence jsonb NOT NULL,
    algorithm_version text NOT NULL,
    first_detected_at timestamptz NOT NULL,
    last_detected_at timestamptz NOT NULL,
    acknowledged_by text,
    resolved_by text,
    resolution jsonb,
    UNIQUE (tenant_id, station_id, fingerprint, first_detected_at)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_twin_open_diagnostic
    ON chargeopt.twin_diagnostics (tenant_id, station_id, severity, last_detected_at DESC)
    WHERE status IN ('open','acknowledged');

CREATE TABLE IF NOT EXISTS chargeopt.twin_maintenance_actions (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    diagnostic_id text REFERENCES chargeopt.twin_diagnostics(id) ON DELETE SET NULL,
    asset_id text REFERENCES chargeopt.twin_assets(id) ON DELETE SET NULL,
    source_fingerprint text NOT NULL,
    action_type text NOT NULL,
    priority text NOT NULL CHECK (priority IN ('low','medium','high','critical')),
    status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_progress','completed','cancelled')),
    recommendation text NOT NULL,
    due_at timestamptz,
    assigned_to text,
    created_by text NOT NULL,
    completed_by text,
    outcome jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_twin_maintenance_queue
    ON chargeopt.twin_maintenance_actions (tenant_id, station_id, status, priority, due_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_twin_open_maintenance_source
    ON chargeopt.twin_maintenance_actions (tenant_id, station_id, source_fingerprint, action_type)
    WHERE status IN ('planned','in_progress');

CREATE TABLE IF NOT EXISTS chargeopt.twin_causal_studies (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('completed','insufficient_evidence','rejected')),
    estimand text NOT NULL,
    algorithm_version text NOT NULL,
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed','field_qualified')),
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    evidence_window_start timestamptz,
    evidence_window_end timestamptz,
    sample_count integer NOT NULL CHECK (sample_count >= 0),
    result jsonb NOT NULL,
    assumptions jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.twin_qualification_evidence (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    evidence_date date NOT NULL,
    category text NOT NULL CHECK (category IN (
        'topology','device_attestation','calibration','shadow_day','slo','fault_injection','recovery_drill','approval'
    )),
    qualified boolean NOT NULL,
    evidence jsonb NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, station_id, evidence_date, category)
);

-- Raw and derived evidence are append-only. Corrections create new records.
CREATE OR REPLACE FUNCTION chargeopt.prevent_twin_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'digital twin evidence is append-only';
END $$;

DO $$
DECLARE
    table_name text;
    trigger_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'twin_measurements','twin_state_estimates','twin_model_calibrations','twin_simulation_runs',
        'twin_causal_studies','twin_qualification_evidence'
    ]
    LOOP
        trigger_name := 'trg_immutable_' || table_name;
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON chargeopt.%I', trigger_name, table_name);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON chargeopt.%I FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_twin_evidence_mutation()',
            trigger_name, table_name
        );
    END LOOP;
END $$;

DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'twin_topology_versions','twin_assets','twin_asset_relationships','twin_measurements',
        'twin_historian_policies','twin_state_estimates','twin_model_calibrations',
        'twin_simulation_runs','twin_diagnostics','twin_maintenance_actions',
        'twin_causal_studies','twin_qualification_evidence'
    ]
    LOOP
        policy_name := 'tenant_isolation_' || table_name;
        EXECUTE format('ALTER TABLE chargeopt.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE chargeopt.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS %I ON chargeopt.%I', policy_name, table_name);
        EXECUTE format(
            'CREATE POLICY %I ON chargeopt.%I USING (NULLIF(current_setting(''chargeopt.tenant_id'', true), '''') IS NOT NULL AND (current_setting(''chargeopt.tenant_id'', true) = ''*'' OR tenant_id = current_setting(''chargeopt.tenant_id'', true))) WITH CHECK (NULLIF(current_setting(''chargeopt.tenant_id'', true), '''') IS NOT NULL AND (current_setting(''chargeopt.tenant_id'', true) = ''*'' OR tenant_id = current_setting(''chargeopt.tenant_id'', true)))',
            policy_name, table_name
        );
    END LOOP;
END $$;

INSERT INTO chargeopt.twin_historian_policies (
    id,tenant_id,station_id,raw_retention_days,minute_retention_days,hourly_retention_days,
    late_arrival_window_seconds,clock_skew_tolerance_seconds,updated_by
)
SELECT
    'hist-' || station.id,station.tenant_id,station.id,400,1095,3650,3600,120,'migration-015'
FROM chargeopt.stations station
ON CONFLICT (tenant_id,station_id) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA chargeopt TO chargeopt_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA chargeopt TO chargeopt_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA chargeopt TO chargeopt_app;
