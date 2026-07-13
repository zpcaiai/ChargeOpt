-- Force tenant RLS even for the table owner used by managed Postgres runtimes.
-- Platform-admin application paths explicitly set chargeopt.tenant_id='*'.

ALTER TABLE IF EXISTS chargeopt.stations FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.telemetry_points FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.alerts FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.vpp_events FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.dispatch_recommendations FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.roi_simulations FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.audit_entries FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.devices FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.protocol_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.task_queue FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.dispatch_approvals FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.edge_command_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.optimization_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.vpp_settlements FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chargeopt.revenue_proof_runs FORCE ROW LEVEL SECURITY;
