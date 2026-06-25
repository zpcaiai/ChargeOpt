CREATE SCHEMA IF NOT EXISTS chargeopt;

CREATE TABLE IF NOT EXISTS chargeopt.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.tenants (
    id text PRIMARY KEY,
    name text NOT NULL,
    plan text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.regions (
    id text PRIMARY KEY,
    name text NOT NULL,
    grid_operator text NOT NULL
);

CREATE TABLE IF NOT EXISTS chargeopt.tariff_plans (
    id text PRIMARY KEY,
    name text NOT NULL,
    demand_charge_per_kw_month numeric(12, 4) NOT NULL,
    service_fee_per_kwh numeric(12, 4) NOT NULL
);

CREATE TABLE IF NOT EXISTS chargeopt.tariff_periods (
    id bigserial PRIMARY KEY,
    tariff_plan_id text NOT NULL REFERENCES chargeopt.tariff_plans(id) ON DELETE CASCADE,
    name text NOT NULL,
    start_hour integer NOT NULL CHECK (start_hour >= 0 AND start_hour <= 23),
    end_hour integer NOT NULL CHECK (end_hour >= 0 AND end_hour <= 24),
    energy_price_per_kwh numeric(12, 4) NOT NULL
);

CREATE TABLE IF NOT EXISTS chargeopt.stations (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id),
    region_id text NOT NULL REFERENCES chargeopt.regions(id),
    name text NOT NULL,
    station_type text NOT NULL,
    address text NOT NULL,
    latitude numeric(10, 6) NOT NULL,
    longitude numeric(10, 6) NOT NULL,
    transformer_capacity_kw numeric(12, 3) NOT NULL,
    charger_count integer NOT NULL,
    connector_count integer NOT NULL,
    max_connector_power_kw numeric(12, 3) NOT NULL,
    storage_capacity_kwh numeric(12, 3) NOT NULL,
    storage_power_kw numeric(12, 3) NOT NULL,
    pv_capacity_kw numeric(12, 3) NOT NULL,
    tariff_plan_id text NOT NULL REFERENCES chargeopt.tariff_plans(id),
    monthly_opex numeric(14, 2) NOT NULL,
    reliability_score numeric(5, 4) NOT NULL,
    dispatch_mode text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.telemetry_points (
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    timestamp timestamptz NOT NULL,
    load_kw numeric(12, 3) NOT NULL,
    pv_kw numeric(12, 3) NOT NULL,
    grid_kw numeric(12, 3) NOT NULL,
    storage_power_kw numeric(12, 3) NOT NULL,
    storage_soc numeric(6, 5) NOT NULL,
    connector_occupied integer NOT NULL,
    queue_length integer NOT NULL,
    sessions integer NOT NULL,
    energy_kwh numeric(12, 3) NOT NULL,
    revenue numeric(14, 2) NOT NULL,
    alert_count integer NOT NULL,
    PRIMARY KEY (station_id, timestamp)
);

CREATE TABLE IF NOT EXISTS chargeopt.alerts (
    id text PRIMARY KEY,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    timestamp timestamptz NOT NULL,
    priority text NOT NULL,
    title text NOT NULL,
    detail text NOT NULL,
    acknowledged boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_events (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id),
    title text NOT NULL,
    start_at timestamptz NOT NULL,
    duration_minutes integer NOT NULL,
    requested_kw numeric(12, 3) NOT NULL,
    incentive_per_kwh numeric(12, 4) NOT NULL,
    status text NOT NULL
);

CREATE TABLE IF NOT EXISTS chargeopt.dispatch_recommendations (
    id text PRIMARY KEY,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    title text NOT NULL,
    risk text NOT NULL,
    action text NOT NULL,
    value numeric(14, 3) NOT NULL,
    dispatch_window text NOT NULL,
    mode text NOT NULL,
    approval text NOT NULL,
    rationale text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.roi_simulations (
    id bigserial PRIMARY KEY,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE SET NULL,
    capacity_kwh numeric(12, 3) NOT NULL,
    power_kw numeric(12, 3) NOT NULL,
    capex numeric(14, 2) NOT NULL,
    annual_net_benefit numeric(14, 2) NOT NULL,
    payback_years numeric(8, 3) NOT NULL,
    irr_percent numeric(8, 3) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.audit_entries (
    id text PRIMARY KEY,
    timestamp timestamptz NOT NULL,
    actor text NOT NULL,
    action text NOT NULL,
    target text NOT NULL,
    detail text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_telemetry_station_time
    ON chargeopt.telemetry_points (station_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_chargeopt_alerts_station_priority
    ON chargeopt.alerts (station_id, priority, acknowledged);

CREATE INDEX IF NOT EXISTS idx_chargeopt_dispatch_station_generated
    ON chargeopt.dispatch_recommendations (station_id, generated_at DESC);
