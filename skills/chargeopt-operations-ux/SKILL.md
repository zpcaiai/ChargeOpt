---
name: chargeopt-operations-ux
description: Build and verify ChargeOpt's operations-focused web experience for charging, storage, campus energy, digital twins, forecasts, dispatch, alarms, work orders, bills, M&V, VPP, and evidence. Use whenever the user requests dashboards, pages, controls, visualizations, reports, operator workflows, mobile views, accessibility, localization, or frontend integration for ChargeOpt.
compatibility: existing static frontend or current repo frontend framework, FastAPI APIs, Playwright visual checks
---

# ChargeOpt Operations UX

Design for repeated operational work: dense, calm, role-aware, and evidence-linked. Do not turn the product into a marketing landing page.

## Workflow

1. Identify operator roles, decisions, frequency, urgency, and required evidence.
2. Inspect existing navigation, components, styles, localization, and API states.
3. Design one end-to-end workflow including loading, empty, stale, degraded, unauthorized, conflict, and failure states.
4. Implement with existing design patterns and icon library.
5. Add API/component tests, keyboard/accessibility checks, and responsive visual verification.
6. Verify text fit, stable dimensions, non-overlap, real data rendering, and action authorization.

## Required workspaces

- Portfolio command center: energy flow, cost, carbon, flexibility, active incidents, schedules, and site comparison.
- Site cockpit: topology/single-line view, live state, charging, battery, PV, utilities, constraints, and control mode.
- Asset operations: health, alarms, trends, point quality, firmware/mapping, maintenance, and command evidence.
- Forecast and dispatch: P10/P50/P90, scenarios, objective breakdown, margins, service risk, approval, and predicted/realized comparison.
- Campus utilities: electrical/cooling/heating/steam/gas/water balances, significant users, comfort/process state, and plant sequence.
- Energy management: EnPI, baseline, bill, allocation, anomalies, projects, M&V, carbon, and ROI reports.
- VPP/DR: readiness, availability, programs, orders/events, delivery, meter quality, breakers, settlement, and disputes.
- Administration: tenants, users, roles, sites, topology, point maps, drivers, tariffs, models, policies, credentials metadata, and audit.

## Interaction rules

- Use Chinese-first labels with stable backend enum values and proper i18n keys.
- Use tables and compact panels for operational comparison; avoid nested cards and oversized headings.
- Use icons for familiar actions, tooltips for unfamiliar controls, and explicit confirmation for consequential commands.
- Show freshness, quality, source, model/version, confidence, and control authorization near decisions.
- Never hide a fail-closed gate behind a disabled button without an actionable reason.
- Separate recommendation, approved command, equipment acknowledgement, and observed effect visually.

## Acceptance

- Target roles can complete core workflows without direct database or terminal access.
- Read-only roles cannot invoke writes; maker-checker separation is visible and enforced by API.
- Desktop and mobile screenshots show no blank charts, clipping, overlap, or inaccessible controls.
- Stale/poor-quality data and solver/edge failures are prominent and cannot look healthy.
- Exports and reports carry scope, time, units, evidence grade, and generation version.
