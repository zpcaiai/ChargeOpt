# ChargeOpt OS Enterprise Architecture

## Product Shape

ChargeOpt OS is an AI energy dispatch and revenue optimization platform for ultra-fast charging, PV-storage-charging stations, fleet depots, and VPP aggregators.

The production control plane closes the loop from station economics through market bidding, trade capture, site dispatch, interval metering, imbalance settlement, and auditable revenue proof. Market-specific regulatory certification and credentials remain deployment inputs rather than source-code defaults.

## Layers

1. Data access
   Charging sessions, station telemetry, storage SOC/SOH, tariff periods, weather context, queue data, alarms, and VPP events.

2. Station state
   Current load, connector utilization, storage SOC, queue length, transformer headroom, active alerts, and monthly demand peak.

3. Forecasting
   Calibrated probabilistic load and flexibility forecasts expose P10/P50/P90 bands, data freshness, model version, and calibration score. A seasonal-recency ensemble with conformal residual bounds is the safe cold-start model; station-specific trained models can implement the same evidence contract.

4. Optimization
   Station MPC enforces SOC, transformer, reserve, ramp, degradation, and service constraints. Portfolio bidding uses conservative quantile capacity, reserve margins, expected-shortfall exposure, price floors, daily energy caps, and station reliability allocations. Every run persists its inputs, outputs, model version, and constraint evidence.

5. Market and execution
   A signed, idempotent market gateway adapter manages order submission while an immutable hash-chained event ledger enforces legal order-state transitions. Trade fills create station delivery schedules and leased edge tasks. Equipment control remains gateway-mediated and receipt-driven; failed or rolled-back VPP dispatch opens the tenant circuit breaker.

6. Metering and settlement
   Signed interval evidence records baseline, actual grid power, delivery, quality flags, source, and evidence hashes. Settlement batches calculate committed/delivered energy, performance, gross revenue, imbalance cost, penalties, net revenue, and a deterministic evidence root for finance review and disputes.

7. Autopilot operations
   Vercel Cron triggers an idempotent five-minute control cycle. Active risk policy, fresh telemetry, a closed circuit breaker, approved limits, and configured market credentials are mandatory. Duplicate cycles are rejected by a tenant/cycle unique key; three market failures open the breaker.

8. Revenue proof
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
- Gateway adapters: OCPP, Modbus, MQTT normalized ingress; IEC 104 and OPC UA can follow the same signed protocol contract.
- Market adapters: `sandbox` for certification drills and `signed_rest` for a venue/aggregator gateway. Secrets are resolved by `credential_ref` and never stored in Postgres.
- Optimizers: replace the dependency-free MPC/MILP dynamic program with CVXPY/Pyomo/OR-Tools/commercial solvers when the deployment environment supports them.
- ML forecasts: replace explainable seasonal forecast with trained station-specific models.
- Control plane: users/sessions/RBAC, tenant scoping, RLS policies, approvals, task queue, worker leases, receipts, VPP settlements, revenue-proof snapshots, and audit trail.

## Safety Principles

- Automatic trading is allowed only by an approved active risk-policy version and a closed circuit breaker.
- Every order includes forecast provenance, confidence, risk decision, affected assets, allocation, idempotency key, and immutable event hashes.
- Storage plans preserve emergency SOC and cycle constraints.
- VPP capacity is conservative and discounts unreliable stations.
- Automatic dispatch stays inside pre-approved safety boundaries and requires edge-gateway validation and receipts.
- Async work must be claimed with a bounded lease, completed by the owning worker, and reaped when a lease expires.
- Stale telemetry, missing credentials, invalid signatures, illegal state transitions, and open breakers fail closed.
