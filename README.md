# ChargeOpt OS

ChargeOpt OS is a runnable Enterprise MVP for ultra-fast charging, PV-storage-charging stations, and VPP aggregation workflows.

The first version intentionally uses only the Python standard library so it can run immediately:

- Multi-tenant station and asset domain model
- Deterministic sample telemetry for charging, storage, tariffs, queueing, alerts, and VPP resources
- Operating cockpit with station economics, demand peaks, storage SOC, and margin
- Station detail, 24-hour load forecast, storage dispatch plan, dynamic pricing hints, and alert triage
- Storage ROI simulator
- VPP resource aggregation and demand response decomposition
- Auditable dispatch recommendation records

## Run

```bash
python3 -m chargeopt.server
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Test

```bash
pytest
```

## API

```text
GET /api/overview
GET /api/stations
GET /api/stations/{station_id}
GET /api/dispatch
GET /api/vpp
GET /api/roi?capacity_kwh=1000&power_kw=500&capex_per_kwh=1150&vpp=true
GET /api/audit
```

## Next Milestones

1. Replace in-memory fixtures with PostgreSQL/TimescaleDB repositories.
2. Add authenticated RBAC and tenant scoping at the API boundary.
3. Implement MQTT/OCPP/Modbus adapters behind the gateway interfaces.
4. Persist forecast, optimization, and dispatch plan versions.
5. Add signed command approval and edge safety checks before real device control.
