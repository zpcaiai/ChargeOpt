-- Shared P0-P3 multi-energy platform: semantic topology, protocol mappings,
-- historian quality, charging/storage/campus operations, optimization, and M&V.

ALTER TABLE chargeopt.devices DROP CONSTRAINT IF EXISTS devices_protocol_check;
ALTER TABLE chargeopt.devices ADD CONSTRAINT devices_protocol_check CHECK (protocol IN (
    'ocpp','ocpp16','ocpp201','ocpp21','iso15118','modbus','modbus_tcp','modbus_rtu','mqtt',
    'bacnet_ip','opc_ua','iec61850','iec104','dlt645','cjt188'
));

CREATE TABLE IF NOT EXISTS chargeopt.energy_topology_versions (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','validated','active','retired','rejected')),
    topology_hash text NOT NULL CHECK (topology_hash ~ '^[0-9a-f]{64}$'),
    validation_report jsonb NOT NULL DEFAULT '{}'::jsonb,
    valid_from timestamptz,
    valid_to timestamptz,
    created_by text NOT NULL,
    activated_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    UNIQUE (tenant_id, version),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_energy_topology_active
    ON chargeopt.energy_topology_versions (tenant_id) WHERE status='active';

CREATE TABLE IF NOT EXISTS chargeopt.energy_assets (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.energy_topology_versions(id) ON DELETE CASCADE,
    asset_key text NOT NULL,
    asset_type text NOT NULL,
    name text NOT NULL,
    energy_carriers text[] NOT NULL DEFAULT '{}',
    organization_path text[] NOT NULL DEFAULT '{}',
    manufacturer text,
    model text,
    serial_number text,
    rated_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    control_capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    maintenance_boundary jsonb NOT NULL DEFAULT '{}'::jsonb,
    warranty_boundary jsonb NOT NULL DEFAULT '{}'::jsonb,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    valid_from timestamptz,
    valid_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (topology_version_id, asset_key),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_energy_assets_lookup
    ON chargeopt.energy_assets (tenant_id, asset_type, asset_key);

CREATE TABLE IF NOT EXISTS chargeopt.energy_relationships (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.energy_topology_versions(id) ON DELETE CASCADE,
    source_asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    target_asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    relationship_type text NOT NULL CHECK (relationship_type IN (
        'contains','feeds','returns_to','meters','controls','communicates_with','measures','serves','converts','backs_up'
    )),
    energy_carrier text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    valid_from timestamptz,
    valid_to timestamptz,
    CHECK (source_asset_id <> target_asset_id),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_energy_relationship_identity
    ON chargeopt.energy_relationships (
        topology_version_id,
        source_asset_id,
        target_asset_id,
        relationship_type,
        COALESCE(energy_carrier, '')
    );

CREATE TABLE IF NOT EXISTS chargeopt.energy_point_definitions (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.energy_topology_versions(id) ON DELETE CASCADE,
    asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    point_code text NOT NULL,
    category text NOT NULL CHECK (category IN ('measurement','command','state','forecast','plan','settlement')),
    quantity_kind text NOT NULL,
    canonical_unit text NOT NULL,
    direction text NOT NULL DEFAULT 'none' CHECK (direction IN ('import','export','bidirectional','none')),
    aggregation text NOT NULL DEFAULT 'last' CHECK (aggregation IN ('last','mean','min','max','sum','delta','integral')),
    writable boolean NOT NULL DEFAULT false,
    range_min numeric,
    range_max numeric,
    precision_digits integer NOT NULL DEFAULT 3 CHECK (precision_digits BETWEEN 0 AND 12),
    quality_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    command_capability text,
    safety_envelope jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (range_max IS NULL OR range_min IS NULL OR range_max >= range_min),
    CHECK (NOT writable OR (command_capability IS NOT NULL AND category='command')),
    UNIQUE (topology_version_id, asset_id, point_code)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_constraints (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.energy_topology_versions(id) ON DELETE CASCADE,
    asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    constraint_type text NOT NULL CHECK (constraint_type IN (
        'electrical','thermal','comfort','process','safety','warranty','maintenance','commercial'
    )),
    priority text NOT NULL CHECK (priority IN ('hard','service','economic')),
    parameters jsonb NOT NULL,
    source text NOT NULL,
    valid_from timestamptz,
    valid_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS chargeopt.device_driver_versions (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    protocol text NOT NULL CHECK (protocol IN (
        'ocpp16','ocpp201','ocpp21','iso15118','modbus_tcp','modbus_rtu','mqtt','bacnet_ip',
        'opc_ua','iec61850','iec104','dlt645','cjt188'
    )),
    version text NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','active','retired','rejected')),
    security_profile jsonb NOT NULL,
    transport_profile jsonb NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    conformance jsonb NOT NULL DEFAULT '{}'::jsonb,
    mapping_hash text NOT NULL CHECK (mapping_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    UNIQUE (tenant_id, name, version)
);

CREATE TABLE IF NOT EXISTS chargeopt.device_bindings (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    driver_version_id text NOT NULL REFERENCES chargeopt.device_driver_versions(id) ON DELETE RESTRICT,
    external_device_id text NOT NULL,
    gateway_id text,
    identity_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    certificate_not_after timestamptz,
    active boolean NOT NULL DEFAULT true,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (tenant_id, external_device_id, valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_active_device_binding
    ON chargeopt.device_bindings (tenant_id, external_device_id) WHERE active;

CREATE TABLE IF NOT EXISTS chargeopt.device_point_mappings (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    driver_version_id text NOT NULL REFERENCES chargeopt.device_driver_versions(id) ON DELETE CASCADE,
    point_definition_id text NOT NULL REFERENCES chargeopt.energy_point_definitions(id) ON DELETE CASCADE,
    external_address text NOT NULL,
    data_type text NOT NULL,
    scale numeric NOT NULL DEFAULT 1,
    offset_value numeric NOT NULL DEFAULT 0,
    byte_order text,
    word_order text,
    writable boolean NOT NULL DEFAULT false,
    command_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (driver_version_id, external_address)
);

CREATE TABLE IF NOT EXISTS chargeopt.edge_gateway_states (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    gateway_id text NOT NULL,
    cluster_id text,
    role text NOT NULL CHECK (role IN ('leader','standby','isolated')),
    mode text NOT NULL CHECK (mode IN ('automatic','manual','local_safe','maintenance')),
    software_version text NOT NULL,
    time_offset_ms numeric(14,3) NOT NULL,
    buffer_usage_bytes bigint NOT NULL CHECK (buffer_usage_bytes >= 0),
    buffer_limit_bytes bigint NOT NULL CHECK (buffer_limit_bytes > 0),
    certificate_not_after timestamptz,
    last_heartbeat_at timestamptz NOT NULL,
    identity_evidence jsonb NOT NULL,
    active_interlocks text[] NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, gateway_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.device_firmware_jobs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    device_binding_id text NOT NULL REFERENCES chargeopt.device_bindings(id) ON DELETE RESTRICT,
    firmware_version text NOT NULL,
    artifact_uri text NOT NULL,
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    signature_evidence jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('requested','approved','downloading','installing','verifying','completed','failed','rolled_back')),
    rollback_version text,
    requested_by text NOT NULL,
    approved_by text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS chargeopt.edge_offline_evidence (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    gateway_id text NOT NULL,
    local_sequence bigint NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    replayed_at timestamptz,
    cloud_receipt_id text,
    UNIQUE (tenant_id, gateway_id, local_sequence),
    UNIQUE (tenant_id, gateway_id, evidence_hash)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_raw_measurements (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE RESTRICT,
    point_definition_id text NOT NULL REFERENCES chargeopt.energy_point_definitions(id) ON DELETE RESTRICT,
    device_binding_id text REFERENCES chargeopt.device_bindings(id) ON DELETE RESTRICT,
    mapping_version_id text REFERENCES chargeopt.device_driver_versions(id) ON DELETE RESTRICT,
    source_timestamp timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    sequence_number bigint,
    raw_value jsonb NOT NULL,
    numeric_value numeric(22,8),
    source_unit text NOT NULL,
    canonical_value numeric(22,8),
    canonical_unit text NOT NULL,
    quality_code text NOT NULL CHECK (quality_code IN ('good','suspect','bad','substituted','estimated')),
    quality_flags text[] NOT NULL DEFAULT '{}',
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    idempotency_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_energy_raw_window
    ON chargeopt.energy_raw_measurements (tenant_id, point_definition_id, source_timestamp DESC);

CREATE TABLE IF NOT EXISTS chargeopt.energy_quality_events (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    point_definition_id text REFERENCES chargeopt.energy_point_definitions(id) ON DELETE CASCADE,
    event_type text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    rule_version text NOT NULL,
    evidence jsonb NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved','suppressed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (window_end > window_start)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_context_series (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    context_type text NOT NULL CHECK (context_type IN ('weather','tariff','carbon','occupancy','production','vehicle')),
    source_timestamp timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL,
    source text NOT NULL,
    version text NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    idempotency_key text NOT NULL,
    UNIQUE (tenant_id, context_type, source, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_interval_aggregates (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    point_definition_id text NOT NULL REFERENCES chargeopt.energy_point_definitions(id) ON DELETE RESTRICT,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('operational','billing','settlement','mv','forecast_feature')),
    value numeric(22,8) NOT NULL,
    unit text NOT NULL,
    quality_code text NOT NULL CHECK (quality_code IN ('good','suspect','bad','estimated')),
    derivation_version text NOT NULL,
    lineage jsonb NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (interval_end > interval_start),
    UNIQUE (tenant_id, asset_id, point_definition_id, interval_start, interval_end, purpose, derivation_version)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_reconciliation_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.energy_topology_versions(id) ON DELETE RESTRICT,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    carrier text NOT NULL,
    status text NOT NULL CHECK (status IN ('balanced','warning','blocked')),
    input_total numeric(22,8) NOT NULL,
    output_total numeric(22,8) NOT NULL,
    technical_loss numeric(22,8) NOT NULL DEFAULT 0,
    residual numeric(22,8) NOT NULL,
    uncertainty numeric(22,8) NOT NULL,
    result jsonb NOT NULL,
    algorithm_version text NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (interval_end > interval_start)
);

CREATE TABLE IF NOT EXISTS chargeopt.charging_sessions (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    connector_asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE RESTRICT,
    vehicle_id text,
    fleet_id text,
    reservation_id text,
    status text NOT NULL CHECK (status IN ('reserved','authorized','charging','suspended','completed','failed','cancelled')),
    direction text NOT NULL DEFAULT 'charge' CHECK (direction IN ('charge','discharge')),
    arrival_at timestamptz NOT NULL,
    departure_deadline timestamptz NOT NULL,
    started_at timestamptz,
    ended_at timestamptz,
    initial_energy_kwh numeric(14,4) NOT NULL DEFAULT 0,
    target_energy_kwh numeric(14,4) NOT NULL,
    delivered_energy_kwh numeric(14,4) NOT NULL DEFAULT 0,
    minimum_departure_energy_kwh numeric(14,4) NOT NULL DEFAULT 0,
    maximum_power_kw numeric(14,3) NOT NULL,
    minimum_service_kw numeric(14,3) NOT NULL DEFAULT 0,
    v2g_opt_in boolean NOT NULL DEFAULT false,
    contract jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (departure_deadline > arrival_at),
    CHECK (target_energy_kwh >= minimum_departure_energy_kwh),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.charging_reservations (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    connector_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE RESTRICT,
    vehicle_id text,
    fleet_id text,
    reserved_from timestamptz NOT NULL,
    reserved_until timestamptz NOT NULL,
    required_energy_kwh numeric(14,4) NOT NULL CHECK (required_energy_kwh >= 0),
    minimum_departure_energy_kwh numeric(14,4) NOT NULL DEFAULT 0,
    priority integer NOT NULL DEFAULT 0,
    status text NOT NULL CHECK (status IN ('held','confirmed','consumed','expired','cancelled')),
    contract jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (reserved_until > reserved_from),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.charging_reliability_events (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    charger_asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    connector_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    session_id text REFERENCES chargeopt.charging_sessions(id) ON DELETE SET NULL,
    event_type text NOT NULL CHECK (event_type IN (
        'unavailable','authorization_failure','start_failure','interruption','meter_mismatch','profile_rejected',
        'communication_loss','firmware_failure','v2g_failure','recovered'
    )),
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    lost_energy_kwh numeric(14,4) NOT NULL DEFAULT 0,
    lost_sessions numeric(12,3) NOT NULL DEFAULT 0,
    lost_margin numeric(16,2) NOT NULL DEFAULT 0,
    root_cause jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence jsonb NOT NULL,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE IF NOT EXISTS chargeopt.storage_state_snapshots (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    storage_asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    observed_at timestamptz NOT NULL,
    soc numeric(8,6) NOT NULL CHECK (soc BETWEEN 0 AND 1),
    soh numeric(8,6) NOT NULL CHECK (soh BETWEEN 0 AND 1.1),
    soe_kwh numeric(16,4) NOT NULL CHECK (soe_kwh >= 0),
    sop_charge_kw numeric(16,3) NOT NULL CHECK (sop_charge_kw >= 0),
    sop_discharge_kw numeric(16,3) NOT NULL CHECK (sop_discharge_kw >= 0),
    usable_capacity_kwh numeric(16,4) NOT NULL CHECK (usable_capacity_kwh >= 0),
    cell_voltage_delta_v numeric(10,6),
    temperature_delta_c numeric(10,4),
    maximum_temperature_c numeric(10,4),
    insulation_kohm numeric(14,3),
    cooling_available boolean NOT NULL,
    fire_system_normal boolean NOT NULL,
    contactors_closed boolean NOT NULL,
    alarm_codes text[] NOT NULL DEFAULT '{}',
    trust_score numeric(8,6) NOT NULL CHECK (trust_score BETWEEN 0 AND 1),
    dynamic_limits jsonb NOT NULL,
    warranty_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_version text NOT NULL,
    evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_storage_state_latest
    ON chargeopt.storage_state_snapshots (tenant_id, storage_asset_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS chargeopt.storage_safety_events (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    storage_asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    observed_at timestamptz NOT NULL,
    event_type text NOT NULL CHECK (event_type IN (
        'cell_imbalance','thermal_imbalance','temperature_trip','insulation_low','cooling_failure',
        'fire_system_alarm','contactor_fault','pcs_fault','bms_fault','warranty_risk','recovered'
    )),
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    protection_action text NOT NULL,
    command_blocked boolean NOT NULL DEFAULT true,
    evidence jsonb NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved')),
    acknowledged_by text,
    resolved_by text,
    acknowledged_at timestamptz,
    resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS chargeopt.campus_service_requirements (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    asset_id text NOT NULL REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    service_type text NOT NULL CHECK (service_type IN (
        'cooling','heating','steam','compressed_air','comfort','production','lighting','backup','water'
    )),
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    minimum_value numeric,
    target_value numeric NOT NULL,
    maximum_value numeric,
    unit text NOT NULL,
    priority text NOT NULL CHECK (priority IN ('hard','service','economic')),
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    source text NOT NULL,
    version text NOT NULL,
    idempotency_key text NOT NULL,
    CHECK (interval_end > interval_start),
    CHECK (minimum_value IS NULL OR minimum_value <= target_value),
    CHECK (maximum_value IS NULL OR maximum_value >= target_value),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_plans (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topology_version_id text NOT NULL REFERENCES chargeopt.energy_topology_versions(id) ON DELETE RESTRICT,
    timescale text NOT NULL CHECK (timescale IN ('day_ahead','intraday','realtime')),
    mode text NOT NULL CHECK (mode IN ('observe','recommend','shadow','approved_control')),
    status text NOT NULL CHECK (status IN ('completed','safe_fallback','blocked','infeasible')),
    horizon_start timestamptz NOT NULL,
    horizon_end timestamptz NOT NULL,
    interval_minutes integer NOT NULL CHECK (interval_minutes BETWEEN 1 AND 1440),
    objective jsonb NOT NULL,
    constraints jsonb NOT NULL,
    result jsonb NOT NULL,
    algorithm_version text NOT NULL,
    model_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed','field_qualified')),
    idempotency_key text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (horizon_end > horizon_start),
    UNIQUE (tenant_id, timescale, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_baselines (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    carrier text NOT NULL,
    name text NOT NULL,
    method text NOT NULL CHECK (method IN ('weather_adjusted_regression','production_normalized','historical_mean','custom')),
    status text NOT NULL CHECK (status IN ('draft','validated','approved','retired','rejected')),
    training_start timestamptz NOT NULL,
    training_end timestamptz NOT NULL,
    covariates text[] NOT NULL DEFAULT '{}',
    coefficients jsonb NOT NULL,
    metrics jsonb NOT NULL,
    applicability jsonb NOT NULL,
    uncertainty jsonb NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    created_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    CHECK (training_end > training_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_energy_baseline_identity
    ON chargeopt.energy_baselines (tenant_id, COALESCE(scope_asset_id, ''), name, version);

CREATE TABLE IF NOT EXISTS chargeopt.energy_enpis (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    baseline_id text REFERENCES chargeopt.energy_baselines(id) ON DELETE RESTRICT,
    scope_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    name text NOT NULL,
    numerator_quantity text NOT NULL,
    denominator_quantity text,
    unit text NOT NULL,
    target jsonb NOT NULL DEFAULT '{}'::jsonb,
    significant_energy_use boolean NOT NULL DEFAULT false,
    owner text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS chargeopt.utility_bills (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    carrier text NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    supplier text NOT NULL,
    contract_capacity_kw numeric(16,3),
    invoiced_amount numeric(18,2) NOT NULL,
    reconstructed_amount numeric(18,2),
    discrepancy_amount numeric(18,2),
    status text NOT NULL DEFAULT 'received' CHECK (status IN ('received','reconstructed','review','approved','disputed','paid')),
    line_items jsonb NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text NOT NULL,
    created_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end > period_start),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_allocation_rules (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    carrier text NOT NULL,
    source_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    method text NOT NULL CHECK (method IN ('direct_meter','submeter_residual','area','headcount','production','fixed_share')),
    recipients jsonb NOT NULL,
    loss_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('draft','approved','active','retired')),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    created_by text NOT NULL,
    approved_by text,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (tenant_id, name, version)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_mv_projects (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope_asset_id text REFERENCES chargeopt.energy_assets(id) ON DELETE CASCADE,
    name text NOT NULL,
    status text NOT NULL CHECK (status IN ('proposed','approved','implementing','commissioning','mv','verified','rejected','closed')),
    baseline_id text REFERENCES chargeopt.energy_baselines(id) ON DELETE RESTRICT,
    objective jsonb NOT NULL,
    action_plan jsonb NOT NULL,
    mv_plan jsonb NOT NULL,
    investment jsonb NOT NULL DEFAULT '{}'::jsonb,
    service_boundaries jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_management_programs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    scope jsonb NOT NULL,
    policy jsonb NOT NULL,
    significant_energy_uses jsonb NOT NULL DEFAULT '[]'::jsonb,
    responsible_roles jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('draft','approved','active','review','retired')),
    review_frequency_months integer NOT NULL CHECK (review_frequency_months BETWEEN 1 AND 36),
    version integer NOT NULL CHECK (version > 0),
    created_by text NOT NULL,
    approved_by text,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (tenant_id, name, version)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_objectives (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    program_id text NOT NULL REFERENCES chargeopt.energy_management_programs(id) ON DELETE CASCADE,
    enpi_id text REFERENCES chargeopt.energy_enpis(id) ON DELETE SET NULL,
    name text NOT NULL,
    baseline_value numeric,
    target_value numeric NOT NULL,
    target_date date NOT NULL,
    owner text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','active','at_risk','achieved','missed','cancelled')),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_action_plans (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    objective_id text NOT NULL REFERENCES chargeopt.energy_objectives(id) ON DELETE CASCADE,
    name text NOT NULL,
    actions jsonb NOT NULL,
    budget numeric(18,2) NOT NULL DEFAULT 0,
    owner text NOT NULL,
    due_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','approved','in_progress','commissioning','completed','blocked','cancelled')),
    verification jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    approved_by text
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_mv_results (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    project_id text NOT NULL REFERENCES chargeopt.energy_mv_projects(id) ON DELETE RESTRICT,
    reporting_start timestamptz NOT NULL,
    reporting_end timestamptz NOT NULL,
    baseline_energy numeric(22,6) NOT NULL,
    actual_energy numeric(22,6) NOT NULL,
    adjusted_savings_energy numeric(22,6) NOT NULL,
    avoided_cost numeric(18,2) NOT NULL,
    avoided_carbon_kg numeric(22,6) NOT NULL,
    uncertainty jsonb NOT NULL,
    exclusions jsonb NOT NULL DEFAULT '[]'::jsonb,
    service_impact jsonb NOT NULL,
    evidence_grade text NOT NULL CHECK (evidence_grade IN ('engineering','observational','controlled','revenue_grade')),
    status text NOT NULL CHECK (status IN ('draft','review','approved','rejected','superseded')),
    algorithm_version text NOT NULL,
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    CHECK (reporting_end > reporting_start)
);

CREATE TABLE IF NOT EXISTS chargeopt.carbon_factors (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    carrier text NOT NULL,
    accounting_method text NOT NULL CHECK (accounting_method IN ('location_based','market_based')),
    geography text NOT NULL,
    factor_kg_per_unit numeric(18,8) NOT NULL CHECK (factor_kg_per_unit >= 0),
    unit text NOT NULL,
    source text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    instruments jsonb NOT NULL DEFAULT '{}'::jsonb,
    version integer NOT NULL CHECK (version > 0),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (tenant_id, carrier, accounting_method, geography, version)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_reports (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    report_type text NOT NULL CHECK (report_type IN ('monthly','management_review','audit_package','carbon','mv','customer_roi')),
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    scope jsonb NOT NULL,
    content jsonb NOT NULL,
    evidence_manifest jsonb NOT NULL,
    evidence_grade text NOT NULL CHECK (evidence_grade IN ('engineering','observational','controlled','revenue_grade')),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('draft','review','approved','exported','superseded')),
    generated_by text NOT NULL,
    approved_by text,
    generated_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    CHECK (period_end > period_start)
);

CREATE TABLE IF NOT EXISTS chargeopt.energy_management_evidence (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    evidence_type text NOT NULL CHECK (evidence_type IN (
        'quality','reconciliation','charging_plan','storage_state','campus_plan','multitimescale_plan',
        'baseline','bill','allocation','mv','carbon','report'
    )),
    scope_id text,
    algorithm_version text NOT NULL,
    evidence_class text NOT NULL CHECK (evidence_class IN ('synthetic','replay','shadow','observed','field_qualified')),
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    idempotency_key text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, evidence_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_energy_management_evidence
    ON chargeopt.energy_management_evidence (tenant_id, evidence_type, created_at DESC);

CREATE OR REPLACE FUNCTION chargeopt.prevent_energy_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'energy evidence rows are immutable';
END $$;

DO $$
DECLARE
    table_name text;
    policy_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'energy_topology_versions','energy_assets','energy_relationships','energy_point_definitions',
        'energy_constraints','device_driver_versions','device_bindings','device_point_mappings',
        'edge_gateway_states','device_firmware_jobs','edge_offline_evidence',
        'energy_raw_measurements','energy_quality_events','energy_context_series','energy_interval_aggregates',
        'energy_reconciliation_runs','charging_sessions','charging_reservations','charging_reliability_events',
        'storage_state_snapshots','storage_safety_events','campus_service_requirements',
        'energy_plans','energy_baselines','energy_enpis','utility_bills','energy_allocation_rules',
        'energy_mv_projects','energy_management_programs','energy_objectives','energy_action_plans',
        'energy_mv_results','carbon_factors','energy_reports','energy_management_evidence'
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

DROP TRIGGER IF EXISTS trg_energy_raw_immutable ON chargeopt.energy_raw_measurements;
CREATE TRIGGER trg_energy_raw_immutable BEFORE UPDATE OR DELETE ON chargeopt.energy_raw_measurements
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_energy_evidence_mutation();

DROP TRIGGER IF EXISTS trg_energy_interval_immutable ON chargeopt.energy_interval_aggregates;
CREATE TRIGGER trg_energy_interval_immutable BEFORE UPDATE OR DELETE ON chargeopt.energy_interval_aggregates
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_energy_evidence_mutation();

DROP TRIGGER IF EXISTS trg_energy_management_evidence_immutable ON chargeopt.energy_management_evidence;
CREATE TRIGGER trg_energy_management_evidence_immutable BEFORE UPDATE OR DELETE ON chargeopt.energy_management_evidence
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_energy_evidence_mutation();

CREATE OR REPLACE FUNCTION chargeopt.protect_edge_offline_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'offline evidence rows cannot be deleted';
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.gateway_id IS DISTINCT FROM OLD.gateway_id
       OR NEW.local_sequence IS DISTINCT FROM OLD.local_sequence
       OR NEW.event_type IS DISTINCT FROM OLD.event_type
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
       OR NEW.evidence_hash IS DISTINCT FROM OLD.evidence_hash THEN
        RAISE EXCEPTION 'offline evidence payload is immutable';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_edge_offline_payload_protected ON chargeopt.edge_offline_evidence;
CREATE TRIGGER trg_edge_offline_payload_protected BEFORE UPDATE OR DELETE ON chargeopt.edge_offline_evidence
FOR EACH ROW EXECUTE FUNCTION chargeopt.protect_edge_offline_payload();

CREATE OR REPLACE FUNCTION chargeopt.protect_energy_report_content()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'energy reports cannot be deleted';
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.report_type IS DISTINCT FROM OLD.report_type
       OR NEW.period_start IS DISTINCT FROM OLD.period_start
       OR NEW.period_end IS DISTINCT FROM OLD.period_end
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.content IS DISTINCT FROM OLD.content
       OR NEW.evidence_manifest IS DISTINCT FROM OLD.evidence_manifest
       OR NEW.evidence_grade IS DISTINCT FROM OLD.evidence_grade
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.generated_by IS DISTINCT FROM OLD.generated_by
       OR NEW.generated_at IS DISTINCT FROM OLD.generated_at THEN
        RAISE EXCEPTION 'energy report content is immutable';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_energy_report_content_protected ON chargeopt.energy_reports;
CREATE TRIGGER trg_energy_report_content_protected BEFORE UPDATE OR DELETE ON chargeopt.energy_reports
FOR EACH ROW EXECUTE FUNCTION chargeopt.protect_energy_report_content();

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA chargeopt TO chargeopt_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA chargeopt TO chargeopt_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA chargeopt TO chargeopt_app;
