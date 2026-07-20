# Grid EMS Algorithm Stack

ChargeOpt uses a hybrid stack. "SOTA" here means current, defensible methods selected for a production constraint set; it does not mean that one model is universally best at every station.

## Implemented decision path

| Layer | Production method | Evidence and safety boundary |
|---|---|---|
| Forecast | Adaptive local ensemble, split conformal P10/P50/P90, correlated block scenarios, optional HTTPS TS foundation model | External TSFM is optional and versioned; missing or partial credentials fail closed |
| Charging flexibility | Per-session cumulative energy polytope with arrival, deadline, delivered energy, efficiency, and connector limits | Infeasible customer commitments are surfaced before optimization |
| Rolling dispatch | Mixed-integer MPC over forecast scenarios with expected cost and CVaR | Battery modes, SOC, reserve duration, grid import, and transformer constraints remain hard |
| Market co-optimization | Energy, demand charge, carbon, upward reserve, and downward reserve in one objective | Reserve is bounded by instantaneous power, grid headroom, and sustained battery energy |
| Grid security | Contingency-derated limits inside dispatch plus interval-by-interval N-1 LinDistFlow screening | Any loss of load or required curtailment fails the certificate; external unbalanced AC/protection study remains required |
| Battery aging | Rainflow cycle extraction plus depth, SOH, temperature, and calendar stress | Planning evidence only; coefficients must be calibrated to vendor/cell field data before warranty use |
| Portfolio | Bounded consensus ADMM | Allocation recommendation only; local stations retain physical limits |
| Learned policy | Conservative fitted-Q with OOD rejection and physical action projection | Shadow challenger only; never authorizes field control |

## Why this stack

Recent primary work supports probabilistic foundation models for low-voltage peak forecasting, distributionally robust MPC for VPP uncertainty, joint energy/reserve scheduling under ambiguous EV and renewable distributions, and physics/safety-aware learned charging policies:

- [Probabilistic low-voltage peak forecasting with time-series foundation models](https://arxiv.org/abs/2607.01966)
- [Distributionally robust model predictive control for flexible VPP operation](https://eprints.ncl.ac.uk/309331)
- [Distributed EV scheduling with reserve participation under ambiguous distributions](https://doi.org/10.1016/j.apenergy.2024.125269)
- [Safety-aware reinforcement learning for charging-station management](https://arxiv.org/abs/2403.13236)
- [Physics-informed reinforcement learning for large-scale smart charging](https://arxiv.org/abs/2510.12335)
- [Battery scheduling with degradation and multi-reserve technical requirements](https://arxiv.org/abs/2406.07301)

ChargeOpt adopts the optimization, uncertainty, flexibility, reserve, and degradation ideas in the auditable production path. It does not promote a learned policy merely because a paper reports benchmark gains. Field autonomy requires tenant-scoped observed evidence, an approved model version, safety projection, market readiness, edge acknowledgement, and 30 consecutive qualified shadow days.

## Feasibility policy

The secure dispatcher first solves with every session energy requirement hard. If that model is infeasible and restoration is explicitly enabled, it may introduce only non-negative customer-energy shortfall with a prohibitive penalty. Transformer, SOC, reserve sustainment, battery power, grid import, and contingency capacity are never softened. If the restored model is still infeasible, the API returns a conflict instead of a dispatch recommendation.

## External commissioning gates

Code completion is not field certification. Customer launch still requires calibrated cell-aging parameters, an accepted unbalanced AC and protection study, market product definitions and reserve duration rules, trading qualification, revenue-meter acceptance, device credentials/register maps, gateway conformance, and 30 elapsed qualified shadow days.
