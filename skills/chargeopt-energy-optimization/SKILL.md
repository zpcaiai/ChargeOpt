---
name: chargeopt-energy-optimization
description: Implement and validate ChargeOpt forecasting and multi-timescale energy optimization across charging, storage, campus loads, networks, carbon, demand response, and markets. Use for load/PV/price forecasting, probabilistic scenarios, MILP, MPC, DRO, CVaR, OPF, ADMM, scheduling, feasibility restoration, safe fallback, solver performance, or optimization MLOps.
compatibility: scipy HiGHS MILP, advanced_ems, grid_ems, MLOps registry, digital twin
---

# ChargeOpt Energy Optimization

Use a model hierarchy: forecast uncertainty, physical constraints, exact/convex optimization, deterministic safety projection, then learned policies only as shadow challengers.

## Workflow

1. Define decision horizon, interval, assets, state, exogenous forecasts, scenarios, objective units, hard constraints, soft service terms, and execution boundary.
2. Create calibrated hierarchical forecasts for load, PV, charging, cooling/heating, price, carbon, occupancy, and production.
3. Formulate pure replayable models and validate units, bounds, sparsity, scaling, and scenario dimensions.
4. Solve day-ahead, intraday, and real-time layers with consistent state handoff.
5. Add infeasibility diagnosis, service-only restoration, deterministic safe fallback, timeout, and stale-input behavior.
6. Persist solver/model/input/topology versions, objective breakdown, margins, risk, plan, and provenance.
7. Backtest, shadow-test, compare predicted/realized outcomes, detect drift, and promote through MLOps approval.

## Algorithm stack

- Forecast: robust local baselines, conformal intervals, hierarchical reconciliation, correlated scenarios, optional versioned foundation models.
- Day-ahead: stochastic or distributionally robust MILP with commitment, demand, carbon, reserve, degradation, comfort, and process constraints.
- Intraday: receding-horizon stochastic MPC with updated state and scenarios.
- Network: phase-aware LinDistFlow screening plus external unbalanced AC/protection certification; security-constrained N-1 cases.
- Portfolio: bounded ADMM or decomposition with local feasibility and privacy-preserving boundary exchange.
- Real-time: physical action projection, ramp limiting, local fallback, and breaker gates.
- Learned control: offline/safe/physics-informed policy only after OOD, counterfactual, safety-filter, and shadow evidence.

## Hard boundaries

- Never hide constraint violations in an objective penalty.
- Restoration may relax documented service quantities only; electrical, thermal, SOC, protection, comfort safety, and process safety remain hard.
- Fail closed on invalid forecast quality, topology, state trust, model approval, solver result, or external market readiness.
- Benchmark against deterministic baseline and existing production policy; report uncertainty and computation time.

## Acceptance

- Deterministic inputs reproduce plans and evidence hashes.
- Property tests show all hard limits hold within numerical tolerances.
- Infeasible, timeout, stale, missing, and adversarial cases return explicit safe outcomes.
- Representative horizon/asset/scenario load tests meet deployment-specific SLOs.
- Shadow results show service, cost, carbon, and degradation tradeoffs without unsupported superiority claims.
