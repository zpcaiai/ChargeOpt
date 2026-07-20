# ChargeOpt Implementation Skills

This directory is the version-controlled source for the ChargeOpt energy-platform skill suite. Use the orchestrator first, then load the specialist skill for the earliest incomplete workstream.

| Order | Skill | Purpose |
|---:|---|---|
| 0 | `chargeopt-energy-platform` | Program orchestration, dependency gates, release evidence |
| 1 | `chargeopt-energy-domain` | Multi-energy asset graph, point catalog, constraints, tenancy |
| 2 | `chargeopt-edge-interoperability` | OCPP, Modbus, MQTT, BACnet, OPC UA, IEC and meter adapters |
| 3 | `chargeopt-energy-data` | Historian, quality, reconciliation, lineage, retention |
| 4 | `chargeopt-charging-ems` | Charging sessions, fleet deadlines, power sharing, OCPP 2.x |
| 5 | `chargeopt-storage-ems` | BMS/PCS, SOC/SOH/SOP, safety, degradation, warranty |
| 6 | `chargeopt-campus-ems` | Electricity-cooling-heating and flexible facility loads |
| 7 | `chargeopt-energy-optimization` | Forecasting, stochastic MPC/MILP, OPF, safe fallback |
| 8 | `chargeopt-energy-mv` | ISO 50001, baselines, EnPI, billing, M&V, carbon, ROI |
| 9 | `chargeopt-flexibility-market` | Demand response, OpenADR, VPP, market settlement |
| 10 | `chargeopt-operations-ux` | Operator cockpit, alarms, workflows, reports, accessibility |
| 11 | `chargeopt-industrial-assurance` | OT security, HA/DR, commissioning, shadow qualification |

The existing global `chargeopt-digital-twin` skill remains authoritative for topology-aware state estimation, simulation, diagnostics, causal proof, and twin qualification. The orchestrator calls it after the shared energy-domain and historian contracts are stable.

## Shared completion rule

A workstream is not complete because a pure algorithm exists. Complete it only when applicable migrations, forced tenant RLS, repository functions, RBAC APIs, idempotency, audit evidence, operational UX, deterministic tests, failure-path tests, documentation, and deployment checks all pass. Field qualification, vendor conformance, market credentials, and elapsed shadow evidence cannot be manufactured by source code.
