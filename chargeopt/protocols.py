"""Protocol normalization for edge/device ingress.

This module intentionally keeps vendor connectivity at the boundary. The API
receives already-authenticated gateway messages and normalizes OCPP, Modbus,
and MQTT payloads into ChargeOpt telemetry/task semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def normalize_protocol_message(protocol: str, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if protocol == "ocpp":
        return _normalize_ocpp(message_type, payload)
    if protocol == "modbus":
        return _normalize_modbus(message_type, payload)
    if protocol == "mqtt":
        return _normalize_mqtt(message_type, payload)
    raise ValueError(f"Unsupported protocol: {protocol}")


def _normalize_ocpp(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if message_type not in {"MeterValues", "StatusNotification", "BootNotification"}:
        return {"kind": "event", "payload": payload}
    if message_type == "MeterValues":
        meter = payload.get("meterValue", [{}])[-1] if isinstance(payload.get("meterValue"), list) else payload
        sampled = meter.get("sampledValue", []) if isinstance(meter, dict) else []
        values = {
            item.get("measurand", "energy"): float(item.get("value", 0)) for item in sampled if isinstance(item, dict)
        }
        return {
            "kind": "telemetry",
            "timestamp": meter.get("timestamp") or payload.get("timestamp") or datetime.now(UTC).isoformat(),
            "load_kw": values.get("Power.Active.Import", values.get("power", 0)) / 1000,
            "energy_kwh": values.get("Energy.Active.Import.Register", values.get("energy", 0)) / 1000,
            "status": "accepted",
        }
    status = str(payload.get("status", "online")).lower()
    return {"kind": "status", "status": status, "payload": payload}


def _normalize_modbus(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    registers = payload.get("registers", payload)
    if not isinstance(registers, dict):
        registers = {}
    scale = float(payload.get("scale", 1))
    return {
        "kind": "telemetry",
        "timestamp": payload.get("timestamp") or datetime.now(UTC).isoformat(),
        "load_kw": float(registers.get("load_kw", registers.get("40001", 0))) * scale,
        "pv_kw": float(registers.get("pv_kw", registers.get("40002", 0))) * scale,
        "storage_soc": float(registers.get("storage_soc", registers.get("40003", 0.5))),
        "status": "accepted",
    }


def _normalize_mqtt(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if message_type.startswith("telemetry") or "load_kw" in payload:
        return {
            "kind": "telemetry",
            "timestamp": payload.get("timestamp") or datetime.now(UTC).isoformat(),
            "load_kw": float(payload.get("load_kw", 0)),
            "pv_kw": float(payload.get("pv_kw", 0)),
            "grid_kw": float(payload.get("grid_kw", payload.get("load_kw", 0))),
            "storage_soc": float(payload.get("storage_soc", 0.5)),
            "status": "accepted",
        }
    return {"kind": "event", "payload": payload}
