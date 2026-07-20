---
name: chargeopt-energy-mv
description: Implement ChargeOpt energy management, ISO 50001 workflows, energy baselines and EnPIs, tariff and bill validation, tenant/cost allocation, measurement and verification, carbon accounting, savings projects, and auditable ROI proof. Use for energy audits, KPIs, baselines, bills, demand charges, allocation, M&V, carbon, savings verification, ROI, customer reports, or ISO 50001 readiness.
compatibility: energy historian, tariff engine, revenue intelligence, causal studies, settlement evidence
---

# ChargeOpt Energy Management and M&V

Turn optimization results into auditable operational and financial proof.

## Workflow

1. Define organizational and facility boundary, energy carriers, significant energy uses, meters, reporting periods, and responsible roles.
2. Build versioned tariffs, contracts, taxes, demand rules, carbon factors, and allocation policies.
3. Establish energy baselines and EnPIs adjusted for weather, occupancy, production, operating hours, and asset changes.
4. Detect bill/meter anomalies and identify savings opportunities with cost, confidence, and operational impact.
5. Manage savings projects from proposal and approval through implementation, commissioning, M&V, and persistence review.
6. Produce monthly customer reports with measured, estimated, excluded, and uncertain values separated.
7. Feed verified outcomes into optimization policy review and model calibration.

## Functional scope

- ISO 50001-style policy, scope, significant energy use, baseline, EnPI, objectives, action plans, review, and continual-improvement evidence.
- Electricity, gas, water, heat, cooling, and service tariffs with effective versions and bill reconstruction.
- Demand charge, power factor, penalties, capacity contract, export, subsidies, and internal transfer prices.
- Park/building/department/tenant/process allocation using direct meters, submeter hierarchy, and explicit allocation rules.
- Baseline methods with applicability, training window, covariates, uncertainty, change-point handling, and non-routine adjustments.
- M&V plans, reporting periods, meter quality, exclusions, uncertainty, approval, and immutable result versions.
- Location- and market-based carbon factors with factor source, geography, validity, renewable instruments, and avoided-emissions boundaries.
- ROI, NPV, IRR, payback, degradation, maintenance, service impact, and confidence intervals.

## Evidence rules

- Do not call engineering counterfactuals measured savings.
- Require revenue-grade meter quality for financial settlement and identify estimated intervals.
- Separate correlation, observational causal evidence, and randomized/controlled evidence.
- Preserve revisions as append-only adjustments and record approver and reason.

## Acceptance

- Bills reconstruct from interval data and versioned tariff rules with named discrepancies.
- Baselines pass holdout, residual, overlap, and change-detection gates appropriate to the method.
- Savings reports reconcile energy, cost, carbon, and service impacts without double counting.
- Poor data or unsupported causal identification cannot produce a high-grade ROI claim.
- Reports support customer review, auditor traceability, and machine-readable export.
