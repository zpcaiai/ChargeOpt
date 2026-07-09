-- Industrial-grade control plane:
-- - login/RBAC/tenant isolation primitives
-- - protocol device registry and ingest ledger
-- - async task queue, dispatch approval, edge receipts
-- - optimization run evidence and VPP settlement ledger
-- - tenant-scoped RLS policies for operational tables

CREATE TABLE IF NOT EXISTS chargeopt.users (
    id text PRIMARY KEY,
    tenant_id text REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    email text NOT NULL UNIQUE,
    display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('platform_admin', 'tenant_admin', 'operator', 'analyst', 'edge_gateway', 'auditor')),
    password_salt text NOT NULL,
    password_hash text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

CREATE TABLE IF NOT EXISTS chargeopt.sessions (
    token_hash text PRIMARY KEY,
    user_id text NOT NULL REFERENCES chargeopt.users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);

CREATE TABLE IF NOT EXISTS chargeopt.devices (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    protocol text NOT NULL CHECK (protocol IN ('ocpp', 'modbus', 'mqtt')),
    external_id text NOT NULL,
    status text NOT NULL DEFAULT 'provisioned' CHECK (status IN ('provisioned', 'online', 'offline', 'faulted', 'disabled')),
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (protocol, external_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.protocol_messages (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    device_id text REFERENCES chargeopt.devices(id) ON DELETE SET NULL,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE SET NULL,
    protocol text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'accepted',
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.task_queue (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE SET NULL,
    device_id text REFERENCES chargeopt.devices(id) ON DELETE SET NULL,
    task_type text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    priority integer NOT NULL DEFAULT 100,
    idempotency_key text UNIQUE,
    payload jsonb NOT NULL,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts integer NOT NULL DEFAULT 0,
    scheduled_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.dispatch_approvals (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    recommendation_id text NOT NULL REFERENCES chargeopt.dispatch_recommendations(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    requested_by text NOT NULL,
    reviewed_by text,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    UNIQUE (recommendation_id)
);

CREATE TABLE IF NOT EXISTS chargeopt.edge_command_receipts (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    task_id text NOT NULL REFERENCES chargeopt.task_queue(id) ON DELETE CASCADE,
    station_id text REFERENCES chargeopt.stations(id) ON DELETE SET NULL,
    device_id text REFERENCES chargeopt.devices(id) ON DELETE SET NULL,
    status text NOT NULL CHECK (status IN ('accepted', 'executing', 'succeeded', 'failed', 'rolled_back')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.optimization_runs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    scope text NOT NULL,
    objective text NOT NULL,
    horizon_hours integer NOT NULL,
    solver text NOT NULL,
    status text NOT NULL DEFAULT 'completed',
    objective_value numeric(14, 3) NOT NULL DEFAULT 0,
    inputs jsonb NOT NULL,
    outputs jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chargeopt.vpp_settlements (
    id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES chargeopt.tenants(id) ON DELETE CASCADE,
    event_id text NOT NULL REFERENCES chargeopt.vpp_events(id) ON DELETE CASCADE,
    baseline_kw numeric(12, 3) NOT NULL,
    delivered_kw numeric(12, 3) NOT NULL,
    performance_score numeric(8, 5) NOT NULL,
    gross_revenue numeric(14, 2) NOT NULL,
    penalty numeric(14, 2) NOT NULL,
    net_revenue numeric(14, 2) NOT NULL,
    status text NOT NULL DEFAULT 'settled',
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    settled_by text NOT NULL,
    settled_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE chargeopt.dispatch_recommendations
    ADD COLUMN IF NOT EXISTS tenant_id text REFERENCES chargeopt.tenants(id) ON DELETE CASCADE;

ALTER TABLE chargeopt.audit_entries
    ADD COLUMN IF NOT EXISTS tenant_id text REFERENCES chargeopt.tenants(id) ON DELETE CASCADE;

UPDATE chargeopt.dispatch_recommendations dr
SET tenant_id = s.tenant_id
FROM chargeopt.stations s
WHERE dr.station_id = s.id AND dr.tenant_id IS NULL;

UPDATE chargeopt.audit_entries
SET tenant_id = 't-001'
WHERE tenant_id IS NULL;

ALTER TABLE chargeopt.dispatch_recommendations
    ALTER COLUMN tenant_id SET DEFAULT 't-001';

ALTER TABLE chargeopt.audit_entries
    ALTER COLUMN tenant_id SET DEFAULT 't-001';

CREATE INDEX IF NOT EXISTS idx_chargeopt_users_tenant_role ON chargeopt.users (tenant_id, role);
CREATE INDEX IF NOT EXISTS idx_chargeopt_sessions_user_expires ON chargeopt.sessions (user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_chargeopt_devices_station_protocol ON chargeopt.devices (station_id, protocol);
CREATE INDEX IF NOT EXISTS idx_chargeopt_tasks_status_schedule ON chargeopt.task_queue (status, scheduled_at, priority);
CREATE INDEX IF NOT EXISTS idx_chargeopt_protocol_messages_device_time ON chargeopt.protocol_messages (device_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_chargeopt_receipts_task_time ON chargeopt.edge_command_receipts (task_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_chargeopt_vpp_settlements_event ON chargeopt.vpp_settlements (event_id, settled_at DESC);

INSERT INTO chargeopt.users (
    id, tenant_id, email, display_name, role, password_salt, password_hash, active
)
VALUES (
    'usr-bootstrap-admin',
    't-001',
    'operator@chargeopt.local',
    'Bootstrap Operator',
    'tenant_admin',
    'chargeopt-demo-salt-v1',
    '355c2c0e60f794ed2ef3c826351db835cd1035c0a8b679a4b738cd45e902f477',
    true
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO chargeopt.devices (id, tenant_id, station_id, protocol, external_id, status, capabilities)
SELECT
    'dev-' || s.id || '-ocpp',
    s.tenant_id,
    s.id,
    'ocpp',
    s.id || ':ocpp-cp-001',
    'provisioned',
    '{"profiles":["Core","SmartCharging"],"max_current_a":500}'::jsonb
FROM chargeopt.stations s
ON CONFLICT (protocol, external_id) DO NOTHING;

-- RLS tenant guard. Platform-admin connections may SET LOCAL chargeopt.tenant_id = '*'.
ALTER TABLE chargeopt.stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.telemetry_points ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.vpp_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.dispatch_recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.roi_simulations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.audit_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.protocol_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.task_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.dispatch_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.edge_command_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.optimization_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.vpp_settlements ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'chargeopt' AND tablename = 'stations' AND policyname = 'tenant_isolation_stations') THEN
        CREATE POLICY tenant_isolation_stations ON chargeopt.stations
            USING (COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*') OR tenant_id = current_setting('chargeopt.tenant_id', true));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'chargeopt' AND tablename = 'telemetry_points' AND policyname = 'tenant_isolation_telemetry') THEN
        CREATE POLICY tenant_isolation_telemetry ON chargeopt.telemetry_points
            USING (
                COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*')
                OR EXISTS (SELECT 1 FROM chargeopt.stations s WHERE s.id = station_id AND s.tenant_id = current_setting('chargeopt.tenant_id', true))
            );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'chargeopt' AND tablename = 'alerts' AND policyname = 'tenant_isolation_alerts') THEN
        CREATE POLICY tenant_isolation_alerts ON chargeopt.alerts
            USING (
                COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*')
                OR EXISTS (SELECT 1 FROM chargeopt.stations s WHERE s.id = station_id AND s.tenant_id = current_setting('chargeopt.tenant_id', true))
            );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'chargeopt' AND tablename = 'vpp_events' AND policyname = 'tenant_isolation_vpp_events') THEN
        CREATE POLICY tenant_isolation_vpp_events ON chargeopt.vpp_events
            USING (COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*') OR tenant_id = current_setting('chargeopt.tenant_id', true));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'chargeopt' AND tablename = 'dispatch_recommendations' AND policyname = 'tenant_isolation_dispatch_recommendations') THEN
        CREATE POLICY tenant_isolation_dispatch_recommendations ON chargeopt.dispatch_recommendations
            USING (COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*') OR tenant_id = current_setting('chargeopt.tenant_id', true));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'chargeopt' AND tablename = 'roi_simulations' AND policyname = 'tenant_isolation_roi_simulations') THEN
        CREATE POLICY tenant_isolation_roi_simulations ON chargeopt.roi_simulations
            USING (
                COALESCE(current_setting('chargeopt.tenant_id', true), '*') IN ('', '*')
                OR station_id IS NULL
                OR EXISTS (SELECT 1 FROM chargeopt.stations s WHERE s.id = station_id AND s.tenant_id = current_setting('chargeopt.tenant_id', true))
            );
    END IF;
END $$;

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'audit_entries',
        'devices',
        'protocol_messages',
        'task_queue',
        'dispatch_approvals',
        'edge_command_receipts',
        'optimization_runs',
        'vpp_settlements'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'chargeopt'
              AND tablename = table_name
              AND policyname = 'tenant_isolation_' || table_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON chargeopt.%I USING (COALESCE(current_setting(''chargeopt.tenant_id'', true), ''*'') IN ('''', ''*'') OR tenant_id = current_setting(''chargeopt.tenant_id'', true))',
                'tenant_isolation_' || table_name,
                table_name
            );
        END IF;
    END LOOP;
END $$;
