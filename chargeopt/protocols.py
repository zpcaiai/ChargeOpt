"""Protocol normalization for edge/device ingress.

This module intentionally keeps vendor connectivity at the boundary. The API
receives already-authenticated gateway messages and normalizes charging,
building, industrial, grid, and utility-meter payloads into shared semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def normalize_protocol_message(protocol: str, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if protocol in {"ocpp", "ocpp16", "ocpp201", "ocpp21", "iso15118"}:
        return _normalize_ocpp(message_type, payload)
    if protocol in {"modbus", "modbus_tcp", "modbus_rtu"}:
        return _normalize_modbus(message_type, payload)
    if protocol == "mqtt":
        return _normalize_mqtt(message_type, payload)
    if protocol == "bacnet_ip":
        return _normalize_bacnet(message_type, payload)
    if protocol == "opc_ua":
        return _normalize_opcua(message_type, payload)
    if protocol in {"iec61850", "iec104"}:
        return _normalize_iec(protocol, message_type, payload)
    if protocol in {"dlt645", "cjt188"}:
        return _normalize_utility_meter(protocol, payload)
    raise ValueError(f"Unsupported protocol: {protocol}")


def _normalize_ocpp(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if message_type in {"TransactionEvent", "MeterValues"}:
        meter_values = payload.get("meterValue") or payload.get("meter_value") or []
        meter = meter_values[-1] if isinstance(meter_values, list) and meter_values else payload
        sampled = meter.get("sampledValue", meter.get("sampled_value", [])) if isinstance(meter, dict) else []
        values = {
            item.get("measurand", "energy"): float(item.get("value", 0)) for item in sampled if isinstance(item, dict)
        }
        transaction = payload.get("transactionInfo") or payload.get("transaction_info") or {}
        evse = payload.get("evse") or {}
        return {
            "kind": "telemetry",
            "timestamp": meter.get("timestamp") or payload.get("timestamp") or datetime.now(UTC).isoformat(),
            "load_kw": values.get("Power.Active.Import", values.get("Power.Active.Import.Register", 0)) / 1000,
            "export_kw": values.get("Power.Active.Export", 0) / 1000,
            "energy_kwh": values.get("Energy.Active.Import.Register", values.get("energy", 0)) / 1000,
            "export_energy_kwh": values.get("Energy.Active.Export.Register", 0) / 1000,
            "transaction_id": transaction.get("transactionId") or transaction.get("transaction_id"),
            "evse_id": evse.get("id"),
            "connector_id": evse.get("connectorId") or evse.get("connector_id"),
            "charging_state": payload.get("chargingState") or payload.get("charging_state"),
            "status": "accepted",
        }
    if message_type in {"NotifyReport", "GetReport"}:
        return {"kind": "device_model", "components": payload.get("reportData") or [], "payload": payload}
    if message_type in {"NotifyEVChargingNeeds", "NotifyEVChargingSchedule"}:
        return {"kind": "smart_charging", "payload": payload}
    if message_type in {"CertificateSigned", "SignCertificate", "Get15118EVCertificate"}:
        return {"kind": "certificate", "payload": payload}
    if message_type in {"FirmwareStatusNotification", "DiagnosticsStatusNotification", "LogStatusNotification"}:
        return {"kind": "maintenance", "status": payload.get("status"), "payload": payload}
    if message_type not in {"StatusNotification", "BootNotification"}:
        return {"kind": "event", "payload": payload}
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


def _normalize_bacnet(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    objects = payload.get("objects") or [payload]
    points = []
    for item in objects:
        if "object_identifier" not in item or "present_value" not in item:
            continue
        points.append(
            {
                "external_address": str(item["object_identifier"]),
                "value": item["present_value"],
                "unit": item.get("units"),
                "quality": "good" if not item.get("fault") and not item.get("out_of_service") else "bad",
                "priority_array": item.get("priority_array"),
                "relinquish_default": item.get("relinquish_default"),
            }
        )
    return {
        "kind": "telemetry" if message_type.lower() in {"cov", "readpropertymultiple", "telemetry"} else "event",
        "timestamp": payload.get("timestamp") or datetime.now(UTC).isoformat(),
        "points": points,
        "status": "accepted",
    }


def _normalize_opcua(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values") or []
    points = [
        {
            "external_address": str(item["node_id"]),
            "browse_name": item.get("browse_name"),
            "value": item.get("value"),
            "unit": item.get("engineering_unit"),
            "quality": "good" if str(item.get("status_code", "Good")).lower().startswith("good") else "bad",
            "source_timestamp": item.get("source_timestamp"),
            "server_timestamp": item.get("server_timestamp"),
            "semantic": item.get("ecm_semantic"),
        }
        for item in values
        if item.get("node_id")
    ]
    return {
        "kind": "telemetry" if message_type.lower() in {"datachange", "read", "telemetry"} else "event",
        "timestamp": payload.get("timestamp") or datetime.now(UTC).isoformat(),
        "points": points,
        "status": "accepted",
    }


def _normalize_iec(protocol: str, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get("quality") or {}
    valid = not any(bool(quality.get(flag)) for flag in ("invalid", "blocked", "substituted", "not_topical"))
    return {
        "kind": "telemetry" if message_type.lower() in {"report", "spontaneous", "cyclic", "telemetry"} else "event",
        "timestamp": payload.get("timestamp") or datetime.now(UTC).isoformat(),
        "external_address": payload.get("data_object") or payload.get("information_object_address"),
        "common_address": payload.get("common_address"),
        "value": payload.get("value"),
        "quality": "good" if valid else "bad",
        "cause_of_transmission": payload.get("cause_of_transmission"),
        "select_before_operate": bool(payload.get("select_before_operate")),
        "protocol": protocol,
        "status": "accepted" if valid else "rejected",
    }


def _normalize_utility_meter(protocol: str, payload: dict[str, Any]) -> dict[str, Any]:
    checksum_valid = bool(payload.get("checksum_valid", False))
    if not checksum_valid:
        return {"kind": "telemetry", "status": "rejected", "quality": "bad", "reason": "checksum_invalid"}
    value = float(payload.get("value", 0)) * float(payload.get("multiplier", 1))
    rollover = payload.get("rollover_value")
    return {
        "kind": "telemetry",
        "timestamp": payload.get("freeze_timestamp") or payload.get("timestamp") or datetime.now(UTC).isoformat(),
        "external_address": payload.get("meter_address"),
        "data_identifier": payload.get("data_identifier"),
        "value": value,
        "unit": payload.get("unit"),
        "quality": "good",
        "freeze_value": bool(payload.get("freeze_timestamp")),
        "rollover_value": rollover,
        "protocol": protocol,
        "status": "accepted",
    }


def protocol_capability_matrix() -> dict[str, dict[str, Any]]:
    return {
        "ocpp16": {"telemetry": True, "commands": ["SetChargingProfile"], "field_conformance_required": True},
        "ocpp201": {
            "telemetry": True,
            "device_model": True,
            "smart_charging": True,
            "certificates": True,
            "iso15118": True,
            "field_conformance_required": True,
        },
        "ocpp21": {"telemetry": True, "bidirectional_charging": True, "field_conformance_required": True},
        "bacnet_ip": {"cov": True, "priority_array": True, "field_conformance_required": True},
        "opc_ua": {"subscriptions": True, "ecm_semantics": True, "field_conformance_required": True},
        "iec61850": {"reports": True, "select_before_operate": True, "field_conformance_required": True},
        "iec104": {"spontaneous": True, "select_before_operate": True, "field_conformance_required": True},
        "dlt645": {"checksum": True, "freeze_values": True, "field_conformance_required": True},
        "cjt188": {"checksum": True, "freeze_values": True, "field_conformance_required": True},
    }
