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
   Storage charge/discharge plan, demand peak control, dynamic pricing suggestions, station-level dispatch recommendations, ROI simulation, and VPP capacity calculation.

5. Execution
   Observation and recommendation mode only. The domain model already records approval mode, audit entries, rollback notes, risk level, and execution targets.

6. Revenue
   Charging revenue, energy purchase cost, demand charge exposure, storage arbitrage, demand charge savings, VPP revenue, battery degradation, payback, NPV, and IRR estimate.

## Extension Points

- Repository layer: swap deterministic fixtures for PostgreSQL/TimescaleDB.
- Gateway adapters: OCPP, Modbus, MQTT, IEC 104, OPC UA.
- Optimizers: replace rule-based engine with MILP/MPC using CVXPY or Pyomo.
- ML forecasts: replace explainable seasonal forecast with trained station-specific models.
- Control plane: add signed dispatch command approval, edge validation, local rollback, and execution receipts.

## Safety Principles

- Recommendations do not automatically control equipment.
- Every plan includes rationale, confidence, risk, affected assets, and audit metadata.
- Storage plans preserve emergency SOC and cycle constraints.
- VPP capacity is conservative and discounts unreliable stations.
- Enterprise automatic control must remain behind approval, safety boundaries, and edge gateway validation.
