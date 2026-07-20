-- Extend the immutable EMS ledger for grid-aware flexibility, secure dispatch,
-- N-1 screening, and battery lifetime evidence.

ALTER TABLE chargeopt.ems_evidence_runs
    DROP CONSTRAINT IF EXISTS ems_evidence_runs_evidence_type_check;

ALTER TABLE chargeopt.ems_evidence_runs
    ADD CONSTRAINT ems_evidence_runs_evidence_type_check CHECK (evidence_type IN (
        'forecast',
        'dispatch',
        'network_projection',
        'portfolio_coordination',
        'offline_policy_evaluation',
        'flexibility_envelope',
        'security_constrained_dispatch',
        'network_security_assessment',
        'battery_degradation_assessment'
    ));

-- Reassert the fail-closed controls so drift in a manually provisioned database
-- cannot silently weaken the new evidence types.
ALTER TABLE chargeopt.ems_evidence_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE chargeopt.ems_evidence_runs FORCE ROW LEVEL SECURITY;

DROP TRIGGER IF EXISTS trg_immutable_ems_evidence_runs ON chargeopt.ems_evidence_runs;
CREATE TRIGGER trg_immutable_ems_evidence_runs
BEFORE UPDATE OR DELETE ON chargeopt.ems_evidence_runs
FOR EACH ROW EXECUTE FUNCTION chargeopt.prevent_twin_evidence_mutation();

GRANT SELECT, INSERT ON chargeopt.ems_evidence_runs TO chargeopt_app;
