# ChargeOpt Energy Platform Program

## Definition of done

Every code batch includes applicable domain types, idempotent SQL migration, indexes, `ENABLE` and `FORCE ROW LEVEL SECURITY`, tenant policy, `chargeopt_app` grants, repository methods, RBAC API, RFC 7807 errors, immutable audit/evidence, deterministic tests, failure tests, documentation, and deployment compatibility. Device and market batches also require simulator/conformance fixtures, retries, timeouts, receipts, and circuit-breaker behavior.

## Dependency graph

```text
Energy domain -> Edge interoperability -> Energy data
Energy data -> Charging EMS / Storage EMS / Campus EMS
Asset EMS packs -> Energy optimization -> Digital twin
Energy data + Optimization -> Energy M&V
Charging + Storage + Optimization + M&V -> Flexibility market
All operator workflows -> Operations UX
All workstreams -> Industrial assurance -> field qualification
```

## Phase 0: Baseline and contracts

- Inventory current station, storage, PV, VPP, digital-twin, evidence, auth, edge, task, and settlement capabilities.
- Define capability status and evidence vocabulary.
- Freeze compatibility requirements for current APIs and migrations.
- Accept when the matrix is reproducible from code and no claim exceeds its evidence.

## Phase 1: Shared energy foundation

Use `chargeopt-energy-domain`, then `chargeopt-edge-interoperability`, then `chargeopt-energy-data`.

- Add park/building/electrical/thermal hierarchy, typed assets, points, units, constraints, and version validity.
- Add protocol-neutral driver contracts and building/industrial/meter adapters.
- Add immutable historian, quality engine, reconciliation, lineage, aggregation, and retention.
- Accept when a mixed charging-storage-campus topology can ingest and replay normalized data with tenant isolation.

## Phase 2: Asset packs

Use `chargeopt-charging-ems`, `chargeopt-storage-ems`, and `chargeopt-campus-ems`. These may proceed in parallel only after shared contracts pass.

- Close charging-session, fleet, power-sharing, and OCPP device-management workflows.
- Close battery state, safety, degradation, warranty, and service-mode workflows.
- Close HVAC, cooling, heating, compressed-air, lighting, process, comfort, and production workflows.
- Accept when each asset pack can run in observe, recommend, shadow, and approved-control modes.

## Phase 3: Intelligence and proof

Use `chargeopt-energy-optimization`, the existing `chargeopt-digital-twin`, and `chargeopt-energy-mv`.

- Add hierarchical probabilistic forecasts and multi-timescale secure optimization.
- Add state estimation, replay, simulation, diagnostics, calibration, and predicted-versus-realized comparison.
- Add baselines, EnPI, bills, allocation, M&V, carbon, savings projects, and causal ROI evidence.
- Accept when plans are reproducible, constraint margins are visible, and claimed savings carry uncertainty and evidence grade.

## Phase 4: Grid and commercial aggregation

Use `chargeopt-flexibility-market`.

- Add demand-response program definitions, baselines, availability, dispatch, meter evidence, settlement, and disputes.
- Add OpenADR and target-market adapters behind one signed idempotent contract.
- Reuse VPP outbox, leases, breaker, trade, and settlement controls.
- Accept when retries cannot duplicate orders or delivery and incomplete market eligibility blocks live submission.

## Phase 5: Product operations

Use `chargeopt-operations-ux` throughout, completing it after all workflows stabilize.

- Build a dense Chinese-first operations cockpit for energy flow, assets, forecasts, schedules, alarms, work orders, bills, M&V, market, and evidence.
- Add accessible responsive behavior, export, saved filters, and role-aware actions.
- Accept with API/UI tests and desktop/mobile visual verification.

## Phase 6: Industrial qualification

Use `chargeopt-industrial-assurance`.

- Complete threat model, OT segmentation, certificate lifecycle, secrets, SBOM, SLOs, HA/DR, backups, restore drills, observability, runbooks, and load/fault tests.
- Execute vendor protocol conformance, meter acceptance, protection study, commissioning, and consecutive shadow operation.
- Accept code readiness separately from field readiness. Only signed external evidence can close the latter.

## Release gates

1. `git diff --check`, formatter, linter, full tests, coverage threshold.
2. Fresh-schema and upgrade migration tests with fail-closed RLS.
3. API contract and authorization matrix tests.
4. Edge loss, stale telemetry, duplicate message, timeout, rollback, and receipt tests.
5. Solver infeasibility, timeout, unsafe input, and deterministic fallback tests.
6. Load and soak targets defined per deployment, with evidence retained.
7. Clean intended diff, remote SHA verification, and CI/deployment status.
