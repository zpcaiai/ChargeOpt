-- Unattended VPP trading control plane.
-- Secrets never live in these tables: credential_ref points to an environment
-- variable or external secret-manager key resolved by the runtime.

CREATE TABLE IF NOT EXISTS chargeopt.market_connections (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    market_code text NOT NULL,
    participant_id text NOT NULL,
    adapter text NOT NULL CHECK (adapter IN ('sandbox', 'signed_rest')),
    base_url text,
    credential_ref text,
    mode text NOT NULL DEFAULT 'sandbox' CHECK (mode IN ('disabled', 'sandbox', 'live')),
    enabled boolean NOT NULL DEFAULT false,
    gate_closure_minutes integer NOT NULL DEFAULT 30 CHECK (gate_closure_minutes BETWEEN 1 AND 1440),
    timezone text NOT NULL DEFAULT 'Asia/Shanghai',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, market_code, participant_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_risk_policies (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    max_order_kw numeric(14,3) NOT NULL CHECK (max_order_kw > 0),
    max_daily_energy_kwh numeric(14,3) NOT NULL CHECK (max_daily_energy_kwh > 0),
    max_open_orders integer NOT NULL DEFAULT 20 CHECK (max_open_orders > 0),
    min_confidence numeric(8,5) NOT NULL DEFAULT 0.90 CHECK (min_confidence BETWEEN 0 AND 1),
    reserve_margin numeric(8,5) NOT NULL DEFAULT 0.20 CHECK (reserve_margin BETWEEN 0 AND 0.8),
    min_price_per_kwh numeric(12,5) NOT NULL DEFAULT 0,
    max_price_per_kwh numeric(12,5) NOT NULL DEFAULT 10,
    max_telemetry_age_seconds integer NOT NULL DEFAULT 300 CHECK (max_telemetry_age_seconds > 0),
    max_failure_rate numeric(8,5) NOT NULL DEFAULT 0.05 CHECK (max_failure_rate BETWEEN 0 AND 1),
    auto_trade_enabled boolean NOT NULL DEFAULT false,
    auto_dispatch_enabled boolean NOT NULL DEFAULT false,
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chargeopt_active_risk_policy
    ON chargeopt.vpp_risk_policies (tenant_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS chargeopt.vpp_forecast_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    algorithm text NOT NULL,
    horizon_start timestamptz NOT NULL,
    horizon_end timestamptz NOT NULL,
    interval_minutes integer NOT NULL CHECK (interval_minutes IN (5, 15, 30, 60)),
    training_window_hours integer NOT NULL CHECK (training_window_hours > 0),
    data_freshness_seconds integer NOT NULL CHECK (data_freshness_seconds >= 0),
    calibration_score numeric(8,5) NOT NULL CHECK (calibration_score BETWEEN 0 AND 1),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.market_orders (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    connection_id text NOT NULL REFERENCES chargeopt.market_connections(id) ON DELETE RESTRICT,
    forecast_run_id text REFERENCES chargeopt.vpp_forecast_runs(id) ON DELETE SET NULL,
    client_order_id text NOT NULL,
    market_order_id text,
    market_code text NOT NULL,
    product text NOT NULL,
    side text NOT NULL CHECK (side IN ('sell', 'buy')),
    delivery_start timestamptz NOT NULL,
    delivery_end timestamptz NOT NULL,
    quantity_kw numeric(14,3) NOT NULL CHECK (quantity_kw > 0),
    limit_price_per_kwh numeric(12,5) NOT NULL CHECK (limit_price_per_kwh >= 0),
    filled_quantity_kw numeric(14,3) NOT NULL DEFAULT 0 CHECK (filled_quantity_kw >= 0),
    average_fill_price numeric(12,5),
    status text NOT NULL CHECK (status IN (
        'draft', 'risk_rejected', 'ready', 'submitting', 'submitted', 'partially_filled',
        'filled', 'cancel_pending', 'cancelled', 'rejected', 'expired', 'failed'
    )),
    risk_decision jsonb NOT NULL,
    allocation jsonb NOT NULL,
    idempotency_key text NOT NULL,
    last_error text,
    submitted_at timestamptz,
    terminal_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (delivery_end > delivery_start),
    CHECK (filled_quantity_kw <= quantity_kw),
    UNIQUE (tenant_id, client_order_id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.market_order_events (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    order_id text NOT NULL REFERENCES chargeopt.market_orders(id) ON DELETE CASCADE,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    event_type text NOT NULL,
    from_status text,
    to_status text NOT NULL,
    actor text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    previous_hash text,
    event_hash text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (order_id, sequence_no),
    UNIQUE (order_id, event_hash)
);

CREATE TABLE IF NOT EXISTS chargeopt.market_trades (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    order_id text NOT NULL REFERENCES chargeopt.market_orders(id) ON DELETE RESTRICT,
    market_trade_id text NOT NULL,
    quantity_kw numeric(14,3) NOT NULL CHECK (quantity_kw > 0),
    price_per_kwh numeric(12,5) NOT NULL CHECK (price_per_kwh >= 0),
    traded_at timestamptz NOT NULL,
    delivery_start timestamptz NOT NULL,
    delivery_end timestamptz NOT NULL,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, market_trade_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.delivery_schedules (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    trade_id text NOT NULL REFERENCES chargeopt.market_trades(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE RESTRICT,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    baseline_kw numeric(14,3) NOT NULL,
    target_adjustment_kw numeric(14,3) NOT NULL,
    target_grid_kw numeric(14,3) NOT NULL,
    status text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'dispatched', 'delivering', 'delivered', 'failed', 'cancelled')),
    task_id text REFERENCES chargeopt.task_queue(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (interval_end > interval_start),
    UNIQUE (trade_id, station_id, interval_start)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_meter_intervals (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE RESTRICT,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    baseline_kw numeric(14,3) NOT NULL,
    actual_grid_kw numeric(14,3) NOT NULL,
    delivered_kw numeric(14,3) NOT NULL,
    quality text NOT NULL CHECK (quality IN ('measured', 'estimated', 'substituted', 'invalid')),
    source text NOT NULL,
    evidence_hash text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT now(),
    CHECK (interval_end > interval_start),
    UNIQUE (tenant_id, station_id, interval_start, source)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlement_batches (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    market_code text NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('calculating', 'review', 'approved', 'exported', 'paid', 'disputed', 'failed')),
    gross_revenue numeric(16,2) NOT NULL DEFAULT 0,
    imbalance_cost numeric(16,2) NOT NULL DEFAULT 0,
    penalties numeric(16,2) NOT NULL DEFAULT 0,
    net_revenue numeric(16,2) NOT NULL DEFAULT 0,
    evidence_root_hash text NOT NULL,
    created_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    CHECK (period_end > period_start),
    UNIQUE (tenant_id, market_code, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlement_lines (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    batch_id text NOT NULL REFERENCES chargeopt.vpp_settlement_batches(id) ON DELETE CASCADE,
    trade_id text NOT NULL REFERENCES chargeopt.market_trades(id) ON DELETE RESTRICT,
    committed_kwh numeric(14,3) NOT NULL,
    delivered_kwh numeric(14,3) NOT NULL,
    performance_score numeric(8,5) NOT NULL CHECK (performance_score BETWEEN 0 AND 1.5),
    gross_revenue numeric(14,2) NOT NULL,
    imbalance_cost numeric(14,2) NOT NULL,
    penalty numeric(14,2) NOT NULL,
    net_revenue numeric(14,2) NOT NULL,
    evidence jsonb NOT NULL,
    UNIQUE (batch_id, trade_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_circuit_breakers (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope text NOT NULL,
    state text NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    reason text,
    failure_count integer NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    opened_at timestamptz,
    reset_after timestamptz,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, scope)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_automation_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    cycle_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'degraded', 'failed', 'skipped')),
    trigger_source text NOT NULL,
    forecast_run_id text REFERENCES chargeopt.vpp_forecast_runs(id) ON DELETE SET NULL,
    orders_created integer NOT NULL DEFAULT 0,
    tasks_created integer NOT NULL DEFAULT 0,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (tenant_id, cycle_key)
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_outbox (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    topic text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'publishing', 'published', 'failed', 'dead_letter')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_market_orders_active
    ON chargeopt.market_orders (tenant_id, delivery_start, status)
    WHERE status NOT IN ('cancelled', 'rejected', 'expired', 'failed', 'filled');
CREATE INDEX IF NOT EXISTS idx_chargeopt_market_trades_delivery
    ON chargeopt.market_trades (tenant_id, delivery_start, delivery_end);
CREATE INDEX IF NOT EXISTS idx_chargeopt_delivery_schedules_due
    ON chargeopt.delivery_schedules (tenant_id, interval_start, status);
CREATE INDEX IF NOT EXISTS idx_chargeopt_meter_intervals_period
    ON chargeopt.vpp_meter_intervals (tenant_id, interval_start, interval_end);
CREATE INDEX IF NOT EXISTS idx_chargeopt_outbox_publish
    ON chargeopt.vpp_outbox (status, available_at, created_at) WHERE status IN ('pending', 'failed');

INSERT INTO chargeopt.market_connections (
    id, tenant_id, market_code, participant_id, adapter, mode, enabled, metadata
)
VALUES (
    'mc-t001-sandbox', 't-001', 'CN-DR-SANDBOX', 'chargeopt-t001', 'sandbox', 'sandbox', true,
    '{"products":["demand_response","regulation_up","peak_shaving"]}'::jsonb
)
ON CONFLICT (tenant_id, market_code, participant_id) DO NOTHING;

INSERT INTO chargeopt.vpp_risk_policies (
    id, tenant_id, name, status, version, max_order_kw, max_daily_energy_kwh,
    max_open_orders, min_confidence, reserve_margin, min_price_per_kwh,
    max_price_per_kwh, max_telemetry_age_seconds, max_failure_rate,
    auto_trade_enabled, auto_dispatch_enabled, approved_by, approved_at
)
VALUES (
    'risk-t001-v1', 't-001', 'Default production guardrails', 'active', 1,
    5000, 30000, 20, 0.90, 0.20, 0.10, 8.00, 900, 0.05,
    true, true, 'migration-007', now()
)
ON CONFLICT DO NOTHING;

INSERT INTO chargeopt.vpp_circuit_breakers (id, tenant_id, scope, state, updated_by)
VALUES ('cb-t001-global', 't-001', 'global', 'closed', 'migration-007')
ON CONFLICT (tenant_id, scope) DO NOTHING;

-- Order event history is immutable; corrections are represented by a new event.
CREATE OR REPLACE FUNCTION chargeopt.prevent_order_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'market_order_events is append-only';
END $$;

DROP TRIGGER IF EXISTS trg_market_order_events_immutable ON chargeopt.market_order_events;
CREATE TRIGGER trg_market_order_events_immutable
BEFORE UPDATE OR DELETE ON chargeopt.market_order_events
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_order_event_mutation();

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'market_connections', 'vpp_risk_policies', 'vpp_forecast_runs',
        'market_orders', 'market_order_events', 'market_trades',
        'delivery_schedules', 'vpp_meter_intervals', 'vpp_settlement_batches',
        'vpp_settlement_lines', 'vpp_circuit_breakers', 'vpp_automation_runs',
        'vpp_outbox'
    ]
    LOOP
        EXECUTE format('ALTER TABLE chargeopt.%I ENABLE ROW LEVEL SECURITY', table_name);
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'chargeopt'
              AND tablename = table_name
              AND policyname = 'tenant_isolation_' || table_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON chargeopt.%I USING (COALESCE(current_setting(''chargeopt.tenant_id'', true), ''*'') IN ('''', ''*'') OR tenant_id = current_setting(''chargeopt.tenant_id'', true)) WITH CHECK (COALESCE(current_setting(''chargeopt.tenant_id'', true), ''*'') IN ('''', ''*'') OR tenant_id = current_setting(''chargeopt.tenant_id'', true))',
                'tenant_isolation_' || table_name,
                table_name
            );
        END IF;
        EXECUTE format('ALTER TABLE chargeopt.%I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;
END $$;
