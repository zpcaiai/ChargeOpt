# ChargeOpt OS Enterprise Architecture

## Product Shape

ChargeOpt OS is an AI energy dispatch and revenue optimization platform for ultra-fast charging, PV-storage-charging stations, fleet depots, and VPP aggregators.

The implemented MVP focuses on the safest commercial entry point: make station economics, demand peaks, storage ROI, dispatch recommendations, and VPP capacity measurable before any real device control.

## Layers

1. Data access
   Charging sessions, station telemetry, storage SOC/SOH, tariff periods, weather context, queue data, alarms, and VPP events.

2. Station state
   Current load, connector utilization, storage SOC, queue length, transformer headroom, active alerts, and monthly demand peak.

3. Forecasting
   Deterministic 24-hour load, queue, and price forecasts. This is intentionally explainable and can be replaced by LightGBM/XGBoost/TFT later.

4. Optimization
   Storage charge/discharge plan, demand peak control, dynamic pricing suggestions, station-level dispatch recommendations, ROI simulation, VPP capacity calculation, and a dependency-free rolling MPC/MILP dynamic-programming optimizer that persists optimization evidence.

5. Execution
   Dispatch recommendations flow through approval records, async task queue entries, worker leases, retry limits, timeout reaping, and edge command receipts. Equipment control remains gateway-mediated; direct field execution must provide worker completion evidence and/or an edge receipt before the task is considered closed.

6. Revenue proof
   Charging revenue, energy purchase cost, demand charge exposure, storage arbitrage, demand charge savings, VPP revenue, battery degradation, payback, NPV, IRR, and monthly counterfactual profit lift. This layer answers the commercial question: "same site, with ChargeOpt vs. without ChargeOpt, how much more did the operator earn or avoid losing this month?" Proof runs can be persisted as tenant-scoped evidence snapshots for monthly business reviews.

## Revenue Proof Algorithm

`chargeopt.revenue_intelligence` implements the proof engine behind `/api/v1/revenue-diagnostics`.

- Counterfactual baseline: estimates a no-EMS/no-storage-dispatch/no-VPP scenario from the same station telemetry, tariff, PV, queue, and transformer context.
- Attribution: decomposes monthly uplift into tariff arbitrage, demand-charge reduction, throughput uplift, queue-loss avoidance, VPP revenue, and battery degradation.
- Confidence: computes p90 bands from hourly residual volatility, so claims are expressed as bounded evidence rather than single-point marketing numbers.
- Moat scorecard: combines operating data hours, adapter protocols, ROI case count, and monthly profit proof into a defensibility signal.
- Evidence persistence: `revenue_proof_runs` stores the full proof payload, confidence bounds, monthly impact, algorithm version, station scope, creator, and tenant context.

The current implementation is deterministic and serverless-safe. In larger deployments, the same API contract can be backed by full synthetic-control models, causal-impact/BSTS models, Pyomo/CVXPY/OR-Tools solvers, or commercial MILP solvers.

## Extension Points

- Repository layer: PostgreSQL-backed tenant-scoped reads with in-memory local fallback.
- Gateway adapters: OCPP, Modbus, MQTT normalized ingress; IEC 104 and OPC UA can follow the same protocol message contract.
- Optimizers: replace the dependency-free MPC/MILP dynamic program with CVXPY/Pyomo/OR-Tools/commercial solvers when the deployment environment supports them.
- ML forecasts: replace explainable seasonal forecast with trained station-specific models.
- Control plane: users/sessions/RBAC, tenant scoping, RLS policies, approvals, task queue, worker leases, receipts, VPP settlements, revenue-proof snapshots, and audit trail.

## Safety Principles

- Recommendations do not automatically control equipment.
- Every plan includes rationale, confidence, risk, affected assets, and audit metadata.
- Storage plans preserve emergency SOC and cycle constraints.
- VPP capacity is conservative and discounts unreliable stations.
- Enterprise automatic control must remain behind approval, safety boundaries, and edge gateway validation.
- Async work must be claimed with a bounded lease, completed by the owning worker, and reaped when a lease expires.
