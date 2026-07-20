"""Protocol-neutral local safety, HA, offline evidence, and command verification."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .energy_platform import canonical_hash


class OfflineEvidenceBuffer:
    """Bounded durable site buffer with deterministic replay ordering."""

    def __init__(self, path: str | Path, *, maximum_rows: int = 100_000):
        if maximum_rows < 100:
            raise ValueError("maximum_rows must be at least 100")
        self.path = str(path)
        self.maximum_rows = maximum_rows
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS evidence (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL UNIQUE,
                    replayed_at TEXT
                )"""
            )

    def append(self, event_type: str, payload: dict[str, Any], occurred_at: datetime | None = None) -> dict[str, Any]:
        timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC)
        material = {"event_type": event_type, "occurred_at": timestamp.isoformat(), "payload": payload}
        evidence_hash = canonical_hash(material)
        with self._lock, self._connect() as conn:
            count = int(conn.execute("SELECT count(*) FROM evidence WHERE replayed_at IS NULL").fetchone()[0])
            if count >= self.maximum_rows:
                raise BufferError("offline evidence buffer is full; gateway must enter local_safe mode")
            conn.execute(
                """INSERT OR IGNORE INTO evidence (event_type,occurred_at,payload,evidence_hash)
                   VALUES (?,?,?,?)""",
                (
                    event_type,
                    timestamp.isoformat(),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    evidence_hash,
                ),
            )
            row = conn.execute(
                "SELECT sequence,event_type,occurred_at,payload,evidence_hash,replayed_at FROM evidence WHERE evidence_hash=?",
                (evidence_hash,),
            ).fetchone()
        return _buffer_row(row)

    def pending(self, limit: int = 1000) -> list[dict[str, Any]]:
        if not 1 <= limit <= 10_000:
            raise ValueError("replay limit must be in [1, 10000]")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT sequence,event_type,occurred_at,payload,evidence_hash,replayed_at
                   FROM evidence WHERE replayed_at IS NULL ORDER BY sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [_buffer_row(row) for row in rows]

    def mark_replayed(self, sequence: int, evidence_hash: str) -> None:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE evidence SET replayed_at=?
                   WHERE sequence=? AND evidence_hash=? AND replayed_at IS NULL""",
                (datetime.now(UTC).isoformat(), sequence, evidence_hash),
            )
            if cursor.rowcount != 1:
                raise ValueError("offline evidence acknowledgement does not match pending record")

    def purge_replayed(self, older_than: datetime) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM evidence WHERE replayed_at IS NOT NULL AND replayed_at < ?", (older_than.isoformat(),)
            )
            return int(cursor.rowcount)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)


def evaluate_local_command(
    command: dict[str, Any], state: dict[str, Any], capability: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    now = _as_utc(command.get("evaluated_at") or datetime.now(UTC))
    issued_at = _as_utc(command["issued_at"])
    expires_at = _as_utc(command["expires_at"])
    if not issued_at <= now <= expires_at:
        reasons.append("command_expired_or_not_yet_valid")
    if state.get("mode") not in {"automatic", "manual_authorized"}:
        reasons.append("gateway_not_in_authorized_mode")
    if state.get("role") != "leader":
        reasons.append("gateway_not_ha_leader")
    if state.get("active_interlocks"):
        reasons.append("local_interlock_active")
    if float(state.get("telemetry_age_seconds", 10**9)) > float(capability.get("maximum_telemetry_age_seconds", 30)):
        reasons.append("telemetry_stale")
    certificate_not_after = state.get("certificate_not_after")
    if not certificate_not_after or _as_utc(certificate_not_after) <= now:
        reasons.append("device_identity_expired")
    if command.get("mapping_version") != capability.get("mapping_version"):
        reasons.append("mapping_version_mismatch")
    command_name = str(command.get("command"))
    if command_name not in set(capability.get("command_allowlist") or []):
        reasons.append("command_not_allowed")
    value = command.get("value")
    envelope = capability.get("safety_envelope") or {}
    if value is not None:
        if envelope.get("minimum") is not None and float(value) < float(envelope["minimum"]):
            reasons.append("command_below_safety_envelope")
        if envelope.get("maximum") is not None and float(value) > float(envelope["maximum"]):
            reasons.append("command_above_safety_envelope")
    return {
        "accepted": not reasons,
        "reasons": sorted(set(reasons)),
        "command_hash": canonical_hash(command),
        "evaluated_at": now.isoformat(),
        "rollback_required_on_timeout": True,
    }


def encode_protocol_command(protocol: str, command: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    address = mapping.get("external_address")
    if not address:
        raise ValueError("external_address is required")
    value = command.get("value")
    if protocol in {"modbus_tcp", "modbus_rtu"}:
        if mapping.get("function_code") not in {5, 6, 15, 16}:
            raise ValueError("Modbus write function is not allowlisted")
        return {
            "function_code": mapping["function_code"],
            "address": int(address),
            "value": value,
            "verify_readback": True,
        }
    if protocol == "bacnet_ip":
        priority = int(mapping.get("write_priority", 16))
        if not 1 <= priority <= 16:
            raise ValueError("BACnet write priority must be in [1,16]")
        return {
            "service": "WriteProperty",
            "object_identifier": address,
            "property": "presentValue",
            "value": value,
            "priority": priority,
        }
    if protocol == "opc_ua":
        return {"operation": "write", "node_id": address, "value": value, "verify_status_code": True}
    if protocol in {"iec61850", "iec104"}:
        return {
            "operation": "select_before_operate",
            "address": address,
            "value": value,
            "select_timeout_seconds": int(mapping.get("select_timeout_seconds", 5)),
        }
    if protocol in {"ocpp201", "ocpp21"}:
        return {
            "action": "SetChargingProfile",
            "evse_id": mapping.get("evse_id", 0),
            "charging_limit_kw": value,
            "request_id": command.get("task_id"),
        }
    raise ValueError(f"protocol {protocol} does not expose an approved command encoder")


def verify_observed_effect(command: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    target = float(command["value"])
    tolerance = abs(float(command.get("effect_tolerance", 0.05)))
    timeout_seconds = float(command.get("effect_timeout_seconds", 30))
    issued_at = _as_utc(command["issued_at"])
    eligible = [
        item
        for item in observations
        if issued_at <= _as_utc(item["source_timestamp"]) <= issued_at + timedelta(seconds=timeout_seconds)
        and item.get("quality_code", "good") == "good"
    ]
    matched = [item for item in eligible if abs(float(item["value"]) - target) <= max(1e-9, abs(target) * tolerance)]
    return {
        "verified": bool(matched),
        "target": target,
        "eligible_observation_count": len(eligible),
        "matched_observation_count": len(matched),
        "rollback_required": not matched,
        "incident_required": not matched,
        "evidence_hash": canonical_hash({"command": command, "observations": eligible}),
    }


def _buffer_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "sequence": int(row[0]),
        "event_type": str(row[1]),
        "occurred_at": str(row[2]),
        "payload": json.loads(row[3]),
        "evidence_hash": str(row[4]),
        "replayed_at": row[5],
    }


def _as_utc(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)
