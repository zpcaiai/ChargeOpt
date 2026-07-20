---
name: chargeopt-energy-data
description: Build ChargeOpt's industrial energy historian, telemetry-quality engine, meter reconciliation, aggregation, lineage, retention, and replay platform. Use whenever the user mentions energy data, time series, telemetry, meters, missing data, quality codes, reconciliation, downsampling, billing intervals, data lineage, weather, tariffs, occupancy, or production context.
compatibility: PostgreSQL/Neon, edge ingress, digital twin, forecasting, settlement and M&V
---

# ChargeOpt Energy Data Platform

Preserve raw facts and make every correction or aggregation reproducible.

## Workflow

1. Inspect telemetry, digital-twin measurements, interval meters, ingestion APIs, and retention assumptions.
2. Define canonical timestamps, units, quality flags, source priority, sequence rules, and evidence hashes.
3. Implement immutable raw ingestion with tenant/device/point idempotency.
4. Implement versioned normalization, quality evaluation, correction, resampling, and aggregation as derived datasets.
5. Add meter hierarchy reconciliation and energy-balance residuals.
6. Add query APIs, data export, retention/downsampling jobs, observability, and replay.
7. Test duplicates, out-of-order data, clock skew, resets, rollover, gaps, DST/timezones, and late corrections.

## Data products

- Raw device historian with source and receive timestamps, mapping version, unit, value, quality, sequence, and hash.
- Trusted latest state with freshness and provenance.
- Interval energy for operational, billing, demand-response, and settlement boundaries.
- Weather, tariff, carbon factor, occupancy, vehicle, and production-plan context.
- Asset and organizational rollups for park, building, department, tenant, line, and equipment.
- Feature and label datasets with point-in-time correctness for forecasting and MLOps.

## Quality engine

Detect and label missing, stale, frozen, out-of-range, spike, drift, clock skew, duplicate, reverse flow, multiplier error, meter reset, rollover, impossible balance, and source disagreement. Do not overwrite raw values. Record correction method, model/rule version, confidence, actor, and superseded derived record.

## Reconciliation

- Compare main meter, feeder, tenant, process, generation, storage, and loss terms on aligned intervals.
- Keep sign conventions explicit.
- Distinguish technical losses, unmetered load, data gaps, and suspected meter faults.
- Block financial settlement when required revenue-meter quality is insufficient.

## Acceptance

- Replaying the same stream is idempotent and produces identical derived outputs.
- Historical queries return the topology, mapping, tariff, and quality version valid at that time.
- Cross-tenant and missing-tenant reads fail closed.
- Aggregates reconcile within configured metering uncertainty or expose a named residual.
- Retention and downsampling preserve billing and evidence-grade source data.
