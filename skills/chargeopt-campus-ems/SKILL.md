---
name: chargeopt-campus-ems
description: Implement ChargeOpt campus and industrial-park multi-energy EMS across electricity, cooling, heating, steam, gas, water, compressed air, buildings, HVAC, chillers, heat pumps, boilers, lighting, production lines, thermal storage, comfort, and process constraints. Use for parks, campuses, buildings, factories, BMS, HVAC, central plants, integrated energy, microgrids, utility allocation, or facility optimization.
compatibility: energy domain/data, BACnet/OPC UA/Modbus, multi-energy optimizer, operations UX
---

# ChargeOpt Campus Multi-Energy EMS

Coordinate facilities around production and occupant needs; energy savings cannot silently degrade process output or comfort.

## Workflow

1. Model park/building/zone/process hierarchy, utility networks, plant equipment, meters, control points, and service constraints.
2. Ingest BMS, industrial, meter, weather, occupancy, schedule, and production-plan context.
3. Establish electrical, cooling, heating, steam, gas, water, and compressed-air balances.
4. Forecast service demand and equipment availability at aligned horizons.
5. Implement equipment and system models, then multi-energy rolling optimization.
6. Deliver setpoints through supervisory controls with local loops and operator override preserved.
7. Verify comfort, process output, utility balance, savings, and rebound effects.

## Asset packs

- Central cooling: chiller staging, chilled/condenser water temperatures, pumps, cooling towers, thermal storage, and minimum run times.
- Heating/steam: boilers, heat pumps, CHP, heat exchangers, thermal storage, pressure/temperature, and fuel curves.
- HVAC: AHU, VAV, fan, valve, zone temperature/humidity/CO2, occupancy, start-stop, and pre-cooling/heating.
- Compressed air: compressor staging, pressure band, dryers, storage, leaks, and production demand.
- Lighting and flexible loads: schedules, daylight, occupancy, criticality, rebound, and maximum interruption.
- Production: line schedules, batch constraints, demand windows, product/energy intensity, and no-interruption processes.
- Microgrid: PV, storage, generators, switch state, grid connection, islanding, load shedding, and restoration priorities.

## Optimization constraints

- Comfort and indoor-air-quality bands with explicit override and exception evidence.
- Production quantity, quality, sequence, restart, and critical-load constraints.
- Equipment capacity, efficiency maps, minimum load, ramp, start cost, minimum up/down time, maintenance, and redundancy.
- Hydraulic/thermal balance, energy conversion, storage dynamics, grid import/export, demand, tariff, carbon, and reserve.

## Acceptance

- Utility balances reconcile to meters and expose losses/residuals.
- Equipment models are calibrated against observed data and versioned.
- Optimization meets comfort/process service levels across normal and failure scenarios.
- Supervisory commands cannot bypass local safety/control loops.
- Savings are verified against a weather/occupancy/production-adjusted baseline.
