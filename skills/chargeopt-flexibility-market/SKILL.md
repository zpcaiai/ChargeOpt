---
name: chargeopt-flexibility-market
description: Implement ChargeOpt demand-response, microgrid flexibility, OpenADR, VPP aggregation, market bidding, dispatch allocation, interval-meter evidence, reconciliation, settlement, disputes, and unattended trading controls. Use for demand response, OpenADR, grid programs, flexibility, ancillary services, VPP, market adapters, orders, trades, baselines, settlement, or aggregator automation.
compatibility: existing VPP outbox/workers/settlement, energy optimizer, meter evidence, edge receipts
---

# ChargeOpt Flexibility Market

Reuse the existing VPP control plane and add program-specific adapters and evidence; do not fork transaction logic by market.

## Workflow

1. Model program/market, product, gate times, delivery interval, baseline, telemetry, eligibility, capacity, price, penalties, and settlement rules as versioned configuration.
2. Calculate conservative availability from asset envelopes, uncertainty, reliability, local obligations, and network constraints.
3. Build signed idempotent market/OpenADR requests behind a common adapter contract.
4. Persist order and outbox atomically; reconcile remote state and handle duplicates, partial fills, rejection, cancellation, and timeout.
5. Allocate delivery to sites/assets with local feasibility and create leased edge tasks only after valid trade/event state.
6. Measure baseline and actual delivery, quality, shortfall, rebound, and asset attribution.
7. Calculate settlement, route maker-checker approval/dispute/export/payment/reversal, and retain evidence roots.

## Functional batches

- Program enrollment and eligibility with document expiry and site/asset qualification.
- OpenADR 3.0 event/program/report/resource adapter plus target utility/market adapters.
- Availability, bidding, portfolio risk, limits, VaR/CVaR, collateral/exposure, and kill switches.
- Dispatch decomposition, fairness, local reserve, ramp, network, comfort/process, and customer opt-out.
- Baseline versioning, meter evidence, response verification, rebound, and non-performance classification.
- Order/trade/event state machine, outbox, leases, retries, reconciliation, dead letters, and breaker.
- Settlement calculation, approval separation, dispute evidence, deterministic export, payment, and append-only reversal.

## Fail-closed gates

- Block live submission on missing credentials, eligibility, active risk policy, qualified assets, revenue meters, shadow history, or healthy reconciliation.
- A market acknowledgement is not a trade fill; a gateway receipt is not observed delivery.
- Retries must not duplicate orders, trades, schedules, commands, meter intervals, or settlement.
- Market certificates, participant identity, collateral, and jurisdiction rules are external inputs.

## Acceptance

- End-to-end event and trade simulations cover every legal and illegal state transition.
- Remote reconciliation resolves ambiguous responses without double exposure.
- Failed/rolled-back site commands open the configured breaker and block new risk.
- Settlement reproduces from immutable market, schedule, meter, tariff, and rule versions.
- Live readiness reports every missing external gate explicitly.
