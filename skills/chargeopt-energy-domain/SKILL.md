---
name: chargeopt-energy-domain
description: Implement ChargeOpt's shared multi-energy domain model for parks, buildings, charging stations, batteries, electrical networks, thermal systems, meters, points, constraints, and versioned topology. Use whenever work introduces a new asset class, point code, campus hierarchy, energy carrier, topology relation, equipment constraint, or tenant-scoped energy API.
compatibility: ChargeOpt domain dataclasses, Pydantic schemas, PostgreSQL/Neon migrations, digital-twin topology
---

# ChargeOpt Energy Domain

Create one canonical semantic model before adding protocol-specific or optimization-specific fields.

## Workflow

1. Inspect `chargeopt/domain.py`, digital-twin topology, station tables, schemas, repository queries, and migrations.
2. Define stable identifiers, ownership, validity windows, units, point semantics, and relationships.
3. Add the migration and forced tenant RLS before persistence code.
4. Add pure validators for topology, units, cycles, capacities, phase connectivity, and constraint consistency.
5. Add repositories and RBAC APIs for version creation, validation, activation, historical lookup, and device binding.
6. Add migration, authorization, topology, compatibility, and replay tests.

## Canonical hierarchy

- tenant, portfolio, park, site, building, floor, zone, process area
- substation, transformer, switchgear, bus, feeder, line, meter
- charging station, charger, EVSE, connector, vehicle/fleet session
- PCS, battery system, rack, pack, BMS, thermal-management system
- PV, wind, generator, CHP, heat pump, boiler, chiller, cooling tower
- pump, fan, air compressor, lighting circuit, production line, flexible load
- electricity, cooling, heating, steam, gas, water, and compressed-air networks

## Required contracts

- Versioned asset facts and typed relationships with effective time ranges.
- Protocol-neutral point catalog: code, quantity kind, unit, direction, aggregation, writable state, range, precision, quality rules.
- Electrical, thermal, comfort, process, safety, warranty, and commercial constraints with source and priority.
- Explicit measurement, command, derived-state, forecast, plan, and settlement point categories.
- Device binding separate from asset identity so hardware replacement preserves history.
- Topology activation workflow; draft topology cannot drive control.

## Safety and compatibility

- Never overload a field with different units or meanings.
- Reject cycles, duplicate parents, disconnected controlled assets, overlapping active versions, invalid phase paths, and contradictory bounds.
- Preserve existing station IDs and API payloads through adapters or additive fields.
- Require tenant context for every graph and point query.
- Store raw device facts separately from derived state.

## Acceptance

- A tenant can create, validate, activate, and query a mixed park topology at any historical time.
- Cross-tenant IDs and device bindings fail closed.
- Every writable point maps to one approved command capability and safety envelope.
- Optimizers and digital twins consume the same topology version and point definitions.
- Fresh and upgrade migrations pass with RLS, indexes, grants, and deterministic fixtures.
