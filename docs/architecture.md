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

4. Optimization and MLOps
   HiGHS mixed-integer rolling MPC enforces binary charge/discharge exclusivity, SOC dynamics, transformer capacity, reserve, ramp, degradation, demand peak, and service constraints. Portfolio bidding uses conservative quantile capacity and risk limits. Model artifacts and training data are hash-addressed; quantile backtests, calibration, drift, shadow state, and maker-checker promotion are persisted.

5. Market and execution
   A signed, idempotent market gateway adapter manages order submission while an immutable hash-chained event ledger enforces legal order-state transitions. Trade fills create station delivery schedules and leased edge tasks. Equipment control remains gateway-mediated and receipt-driven; protocol ingress and edge receipts carry durable tenant-scoped idempotency keys, and failed or rolled-back VPP dispatch opens the tenant circuit breaker.

6. Metering and settlement
   Signed interval evidence records baseline, actual grid power, delivery, quality flags, source, and evidence hashes. Settlement batches calculate committed/delivered energy, performance, gross revenue, imbalance cost, penalties, and net revenue. Immutable lines and hash-chained events support maker-checker approval, disputes, deterministic exports, payment references, and append-only reversal adjustments.

7. Autopilot operations and assurance
   Two or more workers safely compete for leased tasks and outbox records; tenant/cycle keys make automation retries idempotent. GitHub Actions remains a fallback trigger, and migrations serialize through a PostgreSQL advisory lock. Heartbeats, SLO measurements, incidents, dead letters, reconciliation mismatches, immutable daily shadow evidence, and Neon point-in-time restore drills form the operational evidence layer.

8. Revenue proof
   Charging revenue, energy purchase cost, demand charge exposure, storage arbitrage, demand charge savings, VPP revenue, battery degradation, payback, NPV, IRR, and monthly counterfactual profit lift. This layer answers the commercial question: "same site, with ChargeOpt vs. without ChargeOpt, how much more did the operator earn or avoid losing this month?" Proof runs can be persisted as tenant-scoped evidence snapshots for monthly business reviews.

## Revenue Proof Algorithm

`chargeopt.revenue_intelligence` implements the proof engine behind `/api/v1/revenue-diagnostics`.

- Counterfactual baseline: estimates a no-EMS/no-storage-dispatch/no-VPP scenario from the same station telemetry, tariff, PV, queue, and transformer context.
- Attribution: decomposes monthly uplift into tariff arbitrage, demand-charge reduction, throughput uplift, queue-loss avoidance, VPP revenue, and battery degradation.
- Confidence: computes p90 bands from hourly residual volatility, so claims are expressed as bounded evidence rather than single-point marketing numbers.
- Moat scorecard: combines operating data hours, adapter protocols, ROI case count, and monthly profit proof into a defensibility signal.
- Evidence persistence: `revenue_proof_runs` stores the full proof payload, confidence bounds, monthly impact, algorithm version, station scope, creator, and tenant context.

The cold-start revenue model remains deterministic until enough customer history exists. Trained causal models must enter through the same registry, evaluation, shadow, drift, and approval contract; the platform does not label an unvalidated model as production-ready.

## Extension Points

- Repository layer: PostgreSQL-backed tenant-scoped reads with in-memory local fallback.
- Gateway adapters: mTLS OCPP 1.6 central system, TLS MQTT QoS ingestion/commands, and Modbus TCP polling/writes inside an attested network boundary; IEC 104 and OPC UA can follow the same contract.
- Market adapters: `sandbox` for certification drills and `signed_rest` for a venue/aggregator gateway. Secrets are resolved by `credential_ref` and never stored in Postgres.
- Optimizers: the production default is SciPy/HiGHS MILP; customer-specific commercial solvers can implement the same evidence contract.
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
- Runtime database access always assumes the non-owner `chargeopt_app` role. Missing tenant context returns no tenant rows, while platform-wide `*` access is explicit and auditable.
- Live market submission requires verified external certificate/qualification/device attestations plus 30 consecutive qualified shadow days; these facts cannot be created by source code.
