"""PostgreSQL persistence for the shared P0-P3 energy platform."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg.types.json import Json

from .db import get_connection
from .energy_platform import canonical_hash, validate_energy_topology


def _tenant_context(conn: Any, tenant_id: str) -> None:
    conn.execute("SELECT set_config('chargeopt.tenant_id', %s, true)", (tenant_id,))


def _row(cursor: Any, value: Any) -> dict[str, Any]:
    return dict(zip([item.name for item in cursor.description], value, strict=True))


def _audit(conn: Any, tenant_id: str, actor: str, action: str, target: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO chargeopt.audit_entries (id,tenant_id,timestamp,actor,action,target,detail)
           VALUES (%s,%s,now(),%s,%s,%s,%s)""",
        (f"au-{uuid4().hex}", tenant_id, actor, action, target, detail),
    )


def create_energy_topology(tenant_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    validation = validate_energy_topology(payload)
    if not validation["valid"]:
        raise ValueError("invalid energy topology: " + ";".join(validation["errors"]))
    topology_id = f"etv-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        version = int(
            conn.execute(
                "SELECT COALESCE(max(version),0)+1 FROM chargeopt.energy_topology_versions WHERE tenant_id=%s",
                (tenant_id,),
            ).fetchone()[0]
        )
        conn.execute(
            """INSERT INTO chargeopt.energy_topology_versions
               (id,tenant_id,version,name,status,topology_hash,validation_report,created_by)
               VALUES (%s,%s,%s,%s,'validated',%s,%s,%s)""",
            (
                topology_id,
                tenant_id,
                version,
                payload.get("name") or f"Energy topology v{version}",
                validation["topology_hash"],
                Json(validation),
                actor,
            ),
        )
        asset_ids: dict[str, str] = {}
        for asset in payload["assets"]:
            asset_id = f"ena-{uuid4().hex}"
            asset_ids[asset["asset_key"]] = asset_id
            conn.execute(
                """INSERT INTO chargeopt.energy_assets
                   (id,tenant_id,topology_version_id,asset_key,asset_type,name,energy_carriers,
                    organization_path,manufacturer,model,serial_number,rated_parameters,control_capabilities,
                    maintenance_boundary,warranty_boundary,attributes,valid_from,valid_to)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    asset_id,
                    tenant_id,
                    topology_id,
                    asset["asset_key"],
                    asset["asset_type"],
                    asset["name"],
                    list(asset.get("energy_carriers") or []),
                    list(asset.get("organization_path") or []),
                    asset.get("manufacturer"),
                    asset.get("model"),
                    asset.get("serial_number"),
                    Json(asset.get("rated_parameters") or {}),
                    Json(asset.get("control_capabilities") or {}),
                    Json(asset.get("maintenance_boundary") or {}),
                    Json(asset.get("warranty_boundary") or {}),
                    Json(asset.get("attributes") or {}),
                    asset.get("valid_from"),
                    asset.get("valid_to"),
                ),
            )
        for relationship in payload.get("relationships") or []:
            conn.execute(
                """INSERT INTO chargeopt.energy_relationships
                   (id,tenant_id,topology_version_id,source_asset_id,target_asset_id,relationship_type,
                    energy_carrier,attributes,valid_from,valid_to)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"enr-{uuid4().hex}",
                    tenant_id,
                    topology_id,
                    asset_ids[relationship["source_asset_key"]],
                    asset_ids[relationship["target_asset_key"]],
                    relationship["relationship_type"],
                    relationship.get("energy_carrier"),
                    Json(relationship.get("attributes") or {}),
                    relationship.get("valid_from"),
                    relationship.get("valid_to"),
                ),
            )
        for point in payload.get("points") or []:
            conn.execute(
                """INSERT INTO chargeopt.energy_point_definitions
                   (id,tenant_id,topology_version_id,asset_id,point_code,category,quantity_kind,
                    canonical_unit,direction,aggregation,writable,range_min,range_max,precision_digits,
                    quality_rules,command_capability,safety_envelope)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"enp-{uuid4().hex}",
                    tenant_id,
                    topology_id,
                    asset_ids[point["asset_key"]],
                    point["point_code"],
                    point["category"],
                    point["quantity_kind"],
                    point["canonical_unit"],
                    point.get("direction", "none"),
                    point.get("aggregation", "last"),
                    bool(point.get("writable")),
                    point.get("range_min"),
                    point.get("range_max"),
                    int(point.get("precision_digits", 3)),
                    Json(point.get("quality_rules") or {}),
                    point.get("command_capability"),
                    Json(point.get("safety_envelope") or {}),
                ),
            )
        for constraint in payload.get("constraints") or []:
            conn.execute(
                """INSERT INTO chargeopt.energy_constraints
                   (id,tenant_id,topology_version_id,asset_id,constraint_type,priority,parameters,source,
                    valid_from,valid_to) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"enc-{uuid4().hex}",
                    tenant_id,
                    topology_id,
                    asset_ids.get(constraint.get("asset_key")),
                    constraint["constraint_type"],
                    constraint["priority"],
                    Json(constraint["parameters"]),
                    constraint["source"],
                    constraint.get("valid_from"),
                    constraint.get("valid_to"),
                ),
            )
        _audit(conn, tenant_id, actor, "energy.topology_created", topology_id, validation["topology_hash"])
    return get_energy_topology(tenant_id, topology_id)


def activate_energy_topology(tenant_id: str, topology_id: str, actor: str) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        row = conn.execute(
            "SELECT status FROM chargeopt.energy_topology_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
            (topology_id, tenant_id),
        ).fetchone()
        if row is None:
            raise KeyError(topology_id)
        if row[0] != "validated":
            raise ValueError("only validated energy topology versions can be activated")
        conn.execute(
            """UPDATE chargeopt.energy_topology_versions
               SET status='retired',valid_to=now() WHERE tenant_id=%s AND status='active'""",
            (tenant_id,),
        )
        conn.execute(
            """UPDATE chargeopt.energy_topology_versions
               SET status='active',valid_from=COALESCE(valid_from,now()),activated_at=now(),activated_by=%s
               WHERE id=%s AND tenant_id=%s""",
            (actor, topology_id, tenant_id),
        )
        _audit(conn, tenant_id, actor, "energy.topology_activated", topology_id, "active")
    return get_energy_topology(tenant_id, topology_id)


def get_energy_topology(tenant_id: str, topology_id: str | None = None) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        if topology_id:
            cursor = conn.execute(
                """SELECT id,tenant_id,version,name,status,topology_hash,validation_report,valid_from,
                          valid_to,created_by,activated_by,created_at,activated_at
                   FROM chargeopt.energy_topology_versions WHERE tenant_id=%s AND id=%s""",
                (tenant_id, topology_id),
            )
        else:
            cursor = conn.execute(
                """SELECT id,tenant_id,version,name,status,topology_hash,validation_report,valid_from,
                          valid_to,created_by,activated_by,created_at,activated_at
                   FROM chargeopt.energy_topology_versions WHERE tenant_id=%s
                   ORDER BY (status='active') DESC,version DESC LIMIT 1""",
                (tenant_id,),
            )
        value = cursor.fetchone()
        if value is None:
            raise KeyError(topology_id or "active")
        topology = _row(cursor, value)
        asset_cursor = conn.execute(
            """SELECT id,asset_key,asset_type,name,energy_carriers,organization_path,manufacturer,model,
                      serial_number,rated_parameters,control_capabilities,maintenance_boundary,
                      warranty_boundary,attributes,valid_from,valid_to
               FROM chargeopt.energy_assets WHERE tenant_id=%s AND topology_version_id=%s ORDER BY asset_key""",
            (tenant_id, topology["id"]),
        )
        assets = [_row(asset_cursor, row) for row in asset_cursor.fetchall()]
        relationship_cursor = conn.execute(
            """SELECT r.id,s.asset_key source_asset_key,t.asset_key target_asset_key,r.relationship_type,
                      r.energy_carrier,r.attributes,r.valid_from,r.valid_to
               FROM chargeopt.energy_relationships r
               JOIN chargeopt.energy_assets s ON s.id=r.source_asset_id
               JOIN chargeopt.energy_assets t ON t.id=r.target_asset_id
               WHERE r.tenant_id=%s AND r.topology_version_id=%s ORDER BY r.id""",
            (tenant_id, topology["id"]),
        )
        relationships = [_row(relationship_cursor, row) for row in relationship_cursor.fetchall()]
        point_cursor = conn.execute(
            """SELECT p.id,a.asset_key,p.point_code,p.category,p.quantity_kind,p.canonical_unit,p.direction,
                      p.aggregation,p.writable,p.range_min,p.range_max,p.precision_digits,p.quality_rules,
                      p.command_capability,p.safety_envelope
               FROM chargeopt.energy_point_definitions p JOIN chargeopt.energy_assets a ON a.id=p.asset_id
               WHERE p.tenant_id=%s AND p.topology_version_id=%s ORDER BY a.asset_key,p.point_code""",
            (tenant_id, topology["id"]),
        )
        points = [_row(point_cursor, row) for row in point_cursor.fetchall()]
    return topology | {"assets": assets, "relationships": relationships, "points": points}


def persist_energy_evidence(
    tenant_id: str,
    evidence_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    actor: str,
    *,
    scope_id: str | None = None,
    evidence_class: str = "observed",
) -> dict[str, Any]:
    evidence_id = f"eme-{uuid4().hex}"
    algorithm = str(payload.get("algorithm") or "manual-evidence-v1")
    input_hash = str(payload.get("input_hash") or canonical_hash(payload))
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        cursor = conn.execute(
            """INSERT INTO chargeopt.energy_management_evidence
               (id,tenant_id,evidence_type,scope_id,algorithm_version,evidence_class,input_hash,payload,
                idempotency_key,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id,evidence_type,idempotency_key) DO NOTHING
               RETURNING id,created_at""",
            (
                evidence_id,
                tenant_id,
                evidence_type,
                scope_id,
                algorithm,
                evidence_class,
                input_hash,
                Json(payload),
                idempotency_key,
                actor,
            ),
        )
        inserted = cursor.fetchone()
        replayed = inserted is None
        if replayed:
            cursor = conn.execute(
                """SELECT id,created_at,payload FROM chargeopt.energy_management_evidence
                   WHERE tenant_id=%s AND evidence_type=%s AND idempotency_key=%s""",
                (tenant_id, evidence_type, idempotency_key),
            )
            stored = cursor.fetchone()
            evidence_id = str(stored[0])
            created_at = stored[1]
            payload = stored[2]
        else:
            created_at = inserted[1]
            _audit(conn, tenant_id, actor, f"energy.{evidence_type}_recorded", evidence_id, input_hash)
    return {"id": evidence_id, "created_at": created_at, "replayed": replayed, "payload": payload}


def energy_management_dashboard(tenant_id: str) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        topology = conn.execute(
            """SELECT id,name,version,status,topology_hash FROM chargeopt.energy_topology_versions
               WHERE tenant_id=%s ORDER BY (status='active') DESC,version DESC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
        assets = conn.execute(
            "SELECT asset_type,count(*) FROM chargeopt.energy_assets WHERE tenant_id=%s GROUP BY asset_type",
            (tenant_id,),
        ).fetchall()
        evidence = conn.execute(
            """SELECT evidence_type,count(*),max(created_at) FROM chargeopt.energy_management_evidence
               WHERE tenant_id=%s GROUP BY evidence_type""",
            (tenant_id,),
        ).fetchall()
        quality = conn.execute(
            """SELECT severity,count(*) FROM chargeopt.energy_quality_events
               WHERE tenant_id=%s AND status IN ('open','acknowledged') GROUP BY severity""",
            (tenant_id,),
        ).fetchall()
        bills = conn.execute(
            """SELECT count(*),COALESCE(sum(discrepancy_amount),0) FROM chargeopt.utility_bills
               WHERE tenant_id=%s AND status IN ('review','disputed')""",
            (tenant_id,),
        ).fetchone()
        projects = conn.execute(
            """SELECT status,count(*) FROM chargeopt.energy_mv_projects WHERE tenant_id=%s GROUP BY status""",
            (tenant_id,),
        ).fetchall()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "topology": {
            "id": str(topology[0]),
            "name": str(topology[1]),
            "version": int(topology[2]),
            "status": str(topology[3]),
            "hash": str(topology[4]),
        }
        if topology
        else None,
        "assets": {str(row[0]): int(row[1]) for row in assets},
        "evidence": {str(row[0]): {"count": int(row[1]), "latest_at": row[2]} for row in evidence},
        "open_quality_events": {str(row[0]): int(row[1]) for row in quality},
        "bill_exceptions": {"count": int(bills[0]), "amount": float(bills[1])},
        "projects": {str(row[0]): int(row[1]) for row in projects},
    }


def json_payload(value: Any) -> str:
    """Stable JSON helper used by exports and tests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
