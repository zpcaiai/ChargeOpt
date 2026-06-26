-- Seed initial reference data for ChargeOpt OS Enterprise MVP.
-- This migration is idempotent: each INSERT uses ON CONFLICT DO NOTHING.

-- Tenants
INSERT INTO chargeopt.tenants (id, name, plan) VALUES
    ('t-001', '华东超充能源集团', 'Enterprise')
ON CONFLICT (id) DO NOTHING;

-- Regions
INSERT INTO chargeopt.regions (id, name, grid_operator) VALUES
    ('r-sh', '上海城市群', '华东电网'),
    ('r-js', '苏南物流走廊', '华东电网')
ON CONFLICT (id) DO NOTHING;

-- Tariff plans
INSERT INTO chargeopt.tariff_plans (id, name, demand_charge_per_kw_month, service_fee_per_kwh) VALUES
    ('tariff-sh-industrial', '上海工商业尖峰平谷', 42.0, 0.58),
    ('tariff-js-logistics',  '江苏物流园区两部制', 38.0, 0.52)
ON CONFLICT (id) DO NOTHING;

-- Tariff periods – Shanghai
INSERT INTO chargeopt.tariff_periods (tariff_plan_id, name, start_hour, end_hour, energy_price_per_kwh) VALUES
    ('tariff-sh-industrial', 'valley',  0,  7, 0.39),
    ('tariff-sh-industrial', 'flat',    7, 10, 0.82),
    ('tariff-sh-industrial', 'peak',   10, 15, 1.27),
    ('tariff-sh-industrial', 'flat',   15, 18, 0.86),
    ('tariff-sh-industrial', 'peak',   18, 22, 1.34),
    ('tariff-sh-industrial', 'valley', 22, 24, 0.43)
ON CONFLICT DO NOTHING;

-- Tariff periods – Jiangsu
INSERT INTO chargeopt.tariff_periods (tariff_plan_id, name, start_hour, end_hour, energy_price_per_kwh) VALUES
    ('tariff-js-logistics', 'valley',  0,  8, 0.36),
    ('tariff-js-logistics', 'flat',    8, 11, 0.78),
    ('tariff-js-logistics', 'peak',   11, 14, 1.18),
    ('tariff-js-logistics', 'flat',   14, 17, 0.78),
    ('tariff-js-logistics', 'peak',   17, 21, 1.22),
    ('tariff-js-logistics', 'valley', 21, 24, 0.41)
ON CONFLICT DO NOTHING;

-- Stations
INSERT INTO chargeopt.stations (
    id, tenant_id, region_id, name, station_type, address,
    latitude, longitude, transformer_capacity_kw,
    charger_count, connector_count, max_connector_power_kw,
    storage_capacity_kwh, storage_power_kw, pv_capacity_kw,
    tariff_plan_id, monthly_opex, reliability_score, dispatch_mode
) VALUES
    (
        'st-hq-hongqiao', 't-001', 'r-sh', '虹桥枢纽超充站', 'urban_ultrafast',
        '上海市闵行区申虹路', 31.194, 121.318,
        2600, 18, 36, 480, 1200, 600, 180,
        'tariff-sh-industrial', 168000, 0.96, 'recommend'
    ),
    (
        'st-wg-waigaoqiao', 't-001', 'r-sh', '外高桥物流重卡站', 'heavy_truck_depot',
        '上海市浦东新区港城路', 31.343, 121.600,
        4200, 24, 48, 600, 2200, 1000, 320,
        'tariff-sh-industrial', 236000, 0.92, 'semi_auto'
    ),
    (
        'st-sz-industrial', 't-001', 'r-js', '苏州园区光储充站', 'pv_storage_charging',
        '苏州市工业园区星湖街', 31.299, 120.706,
        1800, 12, 24, 360, 900, 450, 520,
        'tariff-js-logistics', 112000, 0.98, 'recommend'
    )
ON CONFLICT (id) DO NOTHING;

-- VPP events
INSERT INTO chargeopt.vpp_events (id, tenant_id, title, start_at, duration_minutes, requested_kw, incentive_per_kwh, status) VALUES
    (
        'vpp-20260625-01', 't-001', '华东晚高峰削减响应',
        (now() AT TIME ZONE 'Asia/Shanghai')::date + interval '18 hours',
        90, 2200, 2.35, 'pending_approval'
    )
ON CONFLICT (id) DO NOTHING;

-- Audit entries
INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail) VALUES
    ('au-001', now() - interval '5 hours', 'system',      'forecast.generated', 'tenant:t-001',       '24h load forecast generated for 3 stations.'),
    ('au-002', now() - interval '3 hours', 'operator.li', 'dispatch.reviewed',  'st-wg-waigaoqiao',   'Approved storage reserve floor at 28% for evening VPP event.'),
    ('au-003', now() - interval '1 hour',  'system',      'roi.simulated',       'st-hq-hongqiao',    'Simulated 1.2MWh storage case with VPP revenue enabled.')
ON CONFLICT (id) DO NOTHING;

-- Alerts
INSERT INTO chargeopt.alerts (id, station_id, timestamp, priority, title, detail, acknowledged) VALUES
    ('al-001', 'st-hq-hongqiao',   now() - interval '2 hours', 'high',   '需量峰值接近阈值',  '18:00-19:00 grid load reached 91% of transformer capacity.', false),
    ('al-002', 'st-wg-waigaoqiao', now() - interval '4 hours', 'medium', '重卡排队时间偏高',  'Queue is forecast to exceed target service level during evening peak.', false),
    ('al-003', 'st-sz-industrial', now() - interval '8 hours', 'low',    '光伏预测偏差',      'PV output was 12% below forecast because of cloud cover.', true)
ON CONFLICT (id) DO NOTHING;
