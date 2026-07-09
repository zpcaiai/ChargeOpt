-- Production control-plane foundations:
-- - deterministic seed telemetry so DB-backed analytics are usable
-- - dispatch approval state
-- - ROI simulation explainability fields
-- - stronger indexes for operational reads

ALTER TABLE chargeopt.dispatch_recommendations
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS reviewed_by text,
    ADD COLUMN IF NOT EXISTS reviewed_at timestamptz,
    ADD COLUMN IF NOT EXISTS review_reason text,
    ADD COLUMN IF NOT EXISTS command_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE chargeopt.roi_simulations
    ADD COLUMN IF NOT EXISTS annual_demand_savings numeric(14, 2),
    ADD COLUMN IF NOT EXISTS annual_arbitrage numeric(14, 2),
    ADD COLUMN IF NOT EXISTS annual_vpp_revenue numeric(14, 2),
    ADD COLUMN IF NOT EXISTS annual_degradation_cost numeric(14, 2),
    ADD COLUMN IF NOT EXISTS annual_maintenance numeric(14, 2),
    ADD COLUMN IF NOT EXISTS npv_10y numeric(14, 2),
    ADD COLUMN IF NOT EXISTS recommendation text,
    ADD COLUMN IF NOT EXISTS inputs jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS chargeopt.telemetry_ingest_log (
    idempotency_key text PRIMARY KEY,
    station_id text NOT NULL REFERENCES chargeopt.stations(id) ON DELETE CASCADE,
    telemetry_timestamp timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_chargeopt_dispatch_status_generated
    ON chargeopt.dispatch_recommendations (status, generated_at DESC);

CREATE INDEX IF NOT EXISTS idx_chargeopt_ingest_station_time
    ON chargeopt.telemetry_ingest_log (station_id, telemetry_timestamp DESC);

-- Seed 24h telemetry for all seeded stations. Values are operationally plausible
-- and idempotent by (station_id, timestamp).
WITH hours AS (
    SELECT generate_series(
        date_trunc('hour', now()) - interval '23 hours',
        date_trunc('hour', now()),
        interval '1 hour'
    ) AS ts
),
station_hours AS (
    SELECT
        s.id AS station_id,
        s.transformer_capacity_kw,
        s.storage_capacity_kwh,
        s.storage_power_kw,
        s.pv_capacity_kw,
        h.ts,
        extract(hour from h.ts)::int AS hour,
        CASE
            WHEN s.station_type = 'heavy_truck_depot' THEN
                0.28
                + 0.32 * exp(-power((extract(hour from h.ts)::int - 5)::numeric, 2) / 12)
                + 0.34 * exp(-power((extract(hour from h.ts)::int - 21)::numeric, 2) / 12)
            WHEN s.station_type = 'pv_storage_charging' THEN
                0.24
                + 0.24 * exp(-power((extract(hour from h.ts)::int - 10)::numeric, 2) / 18)
                + 0.26 * exp(-power((extract(hour from h.ts)::int - 17)::numeric, 2) / 18)
            ELSE
                0.26
                + 0.40 * exp(-power((extract(hour from h.ts)::int - 19)::numeric, 2) / 18)
                + 0.16 * exp(-power((extract(hour from h.ts)::int - 12)::numeric, 2) / 10)
        END AS load_factor
    FROM chargeopt.stations s
    CROSS JOIN hours h
),
computed AS (
    SELECT
        station_id,
        ts,
        greatest(transformer_capacity_kw * 0.12, transformer_capacity_kw * least(load_factor, 0.94)) AS load_kw,
        greatest(0, pv_capacity_kw * sin(greatest(0, least(12, hour - 6))::numeric / 12 * pi())) AS pv_kw,
        CASE
            WHEN hour BETWEEN 0 AND 6 THEN least(storage_power_kw, transformer_capacity_kw * 0.10)
            WHEN hour BETWEEN 18 AND 21 THEN -least(storage_power_kw, transformer_capacity_kw * 0.14)
            ELSE 0
        END AS storage_power_kw,
        greatest(0.20, least(0.90, 0.52 + (hour::numeric / 24) * 0.18)) AS storage_soc,
        greatest(1, round(least(0.96, load_factor + 0.12) * 24))::int AS connector_occupied,
        greatest(0, round((load_factor - 0.72) * 20))::int AS queue_length,
        greatest(1, round(least(0.96, load_factor + 0.12) * 14))::int AS sessions
    FROM station_hours
)
INSERT INTO chargeopt.telemetry_points (
    station_id, timestamp, load_kw, pv_kw, grid_kw, storage_power_kw, storage_soc,
    connector_occupied, queue_length, sessions, energy_kwh, revenue, alert_count
)
SELECT
    station_id,
    ts,
    round(load_kw::numeric, 3),
    round(pv_kw::numeric, 3),
    round(greatest(0, load_kw + greatest(storage_power_kw, 0) + least(storage_power_kw, 0) - pv_kw)::numeric, 3),
    round(storage_power_kw::numeric, 3),
    round(storage_soc::numeric, 5),
    connector_occupied,
    queue_length,
    sessions,
    round(load_kw * 0.91, 3),
    round(load_kw * 0.91 * 1.25, 2),
    CASE WHEN queue_length >= 8 THEN 1 ELSE 0 END
FROM computed
ON CONFLICT (station_id, timestamp) DO NOTHING;
