---
name: chargeopt-storage-ems
description: Implement ChargeOpt battery-storage EMS capabilities including BMS/PCS integration, SOC/SOH/SOE/SOP estimation, electrothermal and cell-imbalance safety, degradation and warranty constraints, arbitrage, demand management, backup, reserve, commissioning, and receipt-verified control. Use for batteries, BESS, BMS, PCS, racks, cells, SOC, SOH, degradation, thermal risk, warranty, backup power, or ancillary services.
compatibility: energy domain/data, edge Modbus/MQTT/IEC adapters, digital twin, grid EMS optimizer
---

# ChargeOpt Storage EMS

Treat the battery as a safety- and warranty-constrained asset, not an ideal energy bucket.

## Workflow

1. Model battery system, rack, pack/cell aggregates, BMS, PCS, HVAC, fire system, contactors, meters, and protection boundaries.
2. Normalize BMS/PCS states, alarms, limits, temperatures, voltages, currents, insulation, and availability.
3. Estimate SOC, SOH, SOE, SOP, usable capacity, efficiency, sensor bias, and confidence.
4. Derive dynamic charge/discharge limits from temperature, imbalance, SOH, alarms, warranty, and grid mode.
5. Optimize energy, demand, PV, reserve, backup, and degradation with hard safety constraints.
6. Execute mode transitions through state machines and verify PCS/BMS/contactor and meter response.
7. Compare predicted and realized efficiency, thermal state, degradation, and availability.

## Functional batches

- Telemetry and alarms: vendor maps, state normalization, severity, deduplication, acknowledgement, and escalation.
- State estimation: coulomb/energy balance, open-circuit or vendor correction, confidence, drift, and calibration.
- Safety envelope: cell voltage, pack voltage/current, temperature, delta temperature, insulation, contactor, fire, and cooling state.
- Degradation: rainflow cycles, calendar aging, temperature/SOC stress, throughput, replacement cost, and vendor-calibrated coefficients.
- Warranty: cycle/throughput/temperature/energy limits, reserved capacity, and breach forecast.
- Services: peak shaving, arbitrage, PV firming, demand response, reserve, backup, islanding support, and black-start eligibility.
- Maintenance: imbalance, cooling degradation, sensor fault, contactor wear, PCS derating, and corrective-action evidence.

## Safety rules

- BMS and protection limits always outrank economic schedules.
- Block commands on stale state, unresolved critical alarms, untrusted SOC, unavailable cooling, or invalid mode transition.
- Never describe an engineering degradation model as warranty certification.
- Preserve backup reserve and emergency operating policy through optimizer constraints.

## Acceptance

- State estimates have residuals, confidence, model version, and bounded replay error.
- Dynamic limits are reproducible from observed inputs and protection policy.
- Simultaneous charge/discharge and illegal mode transitions are impossible.
- Every command has precondition, acknowledgement, observed effect, and rollback/incident evidence.
- Degradation and warranty impacts appear in dispatch economics and customer reports.
