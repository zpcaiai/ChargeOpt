---
name: chargeopt-charging-ems
description: Implement ChargeOpt charging-station and fleet energy management from OCPP sessions and queue forecasting through deadline-aware flexibility, dynamic power sharing, PV-storage coordination, charging reliability, tariffs, V2G, and approved control. Use for chargers, EVSE, connectors, charging sessions, fleets, reservations, queues, smart charging, V2G, OCPP 2.x, or charging-station profitability.
compatibility: OCPP adapters, energy domain/data, grid_ems, task and receipt control plane
---

# ChargeOpt Charging EMS

Optimize energy without violating driver commitments, charger limits, grid limits, or operational safety.

## Workflow

1. Model charger, EVSE, connector, meter, session, reservation, vehicle/fleet contract, tariff, and service commitment.
2. Ingest transaction lifecycle, meter values, availability, faults, firmware, and queue evidence.
3. Build session/fleet forecasts and deadline-aware aggregate flexibility envelopes.
4. Implement hierarchical power sharing: site, charger, EVSE, connector, and vehicle constraints.
5. Co-optimize charging with PV, battery, transformer, demand charge, energy price, carbon, and reserve.
6. Deliver profiles through approved OCPP smart-charging commands and verify actual meter response.
7. Diagnose failures and quantify energy, queue, availability, and margin loss.

## Functional batches

- Session truth: authorization, start/stop, meter reconciliation, interruption, restart, reservation, and billing identity.
- Reliability: charger/connector availability, fault taxonomy, remote diagnostics, firmware evidence, and maintenance closure.
- Flexibility: arrival/departure, required energy, minimum service, opt-in, priority, fairness, and infeasible commitment alerts.
- Smart charging: profile stacking, local/cloud arbitration, fallback profile, ramp rate, phase limit, and transformer headroom.
- Fleet: depot waves, route energy, vehicle assignment, departure risk, opportunity charging, and service-level reporting.
- V2G: bidirectional capability, consent, battery/warranty limits, ISO 15118 evidence, reserve sustainment, and settlement.
- Economics: tariff/service fee, idle fee, demand cost, queue loss, conversion loss, and per-session contribution margin.

## Safety rules

- Never curtail below a hard contractual minimum without explicit service-restoration evidence.
- Separate vehicle capability, charger capability, and site authorization.
- Reject stale connector state, conflicting profiles, untrusted meter data, and unsupported V2G.
- Preserve local charger safety and emergency stop authority.

## Acceptance

- Session and meter lifecycles reconcile across retries and reconnects.
- Every vehicle departure has delivered-energy and shortfall evidence.
- Site/phase/charger/connector limits hold under concurrent starts and failures.
- Approved schedules reach equipment and observed power follows within configured tolerance or opens an incident.
- ROI reports distinguish energy optimization from service degradation.
