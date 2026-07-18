"""PostgreSQL persistence for versioned digital-twin evidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from .db import get_connection
from .digital_twin import canonical_hash, validate_topology
from .repository import _ensure_tenant_allowed, _set_tenant_context


def create_topology_version(
    tenant_id: str,
    station_id: str,
    topology: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    validation = validate_topology(topology)
    if not validation["valid"]:
        raise ValueError("Invalid topology: " + ", ".join(validation["errors"]))
    topology_id = f"top-{uuid4().hex}"
    topology_hash = canonical_hash(
        {
            "station_id": station_id,
            "assets": topology["assets"],
            "relationships": topology["relationships"],
        }
    )
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        _ensure_station(conn, tenant_id, station_id)
        version = conn.execute(
            "SELECT COALESCE(max(version),0)+1 FROM chargeopt.twin_topology_versions WHERE tenant_id=%s AND station_id=%s",
            (tenant_id, station_id),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO chargeopt.twin_topology_versions (
                id,tenant_id,station_id,version,status,topology_hash,validation_report,created_by
            ) VALUES (%s,%s,%s,%s,'validated',%s,%s,%s)
            """,
            (topology_id, tenant_id, station_id, version, topology_hash, Json(validation), actor),
        )
        asset_ids: dict[str, str] = {}
        for asset in topology["assets"]:
            asset_id = f"ast-{uuid4().hex}"
            asset_ids[asset["asset_key"]] = asset_id
            conn.execute(
                """
                INSERT INTO chargeopt.twin_assets (
                    id,tenant_id,station_id,topology_version_id,asset_key,asset_type,name,
                    manufacturer,model,serial_number,rated_power_kw,rated_energy_kwh,attributes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    asset_id,
                    tenant_id,
                    station_id,
                    topology_id,
                    asset["asset_key"],
                    asset["asset_type"],
                    asset["name"],
                    asset.get("manufacturer"),
                    asset.get("model"),
                    asset.get("serial_number"),
                    asset.get("rated_power_kw"),
                    asset.get("rated_energy_kwh"),
                    Json(asset.get("attributes", {})),
                ),
            )
        for relationship in topology["relationships"]:
            conn.execute(
                """
                INSERT INTO chargeopt.twin_asset_relationships (
                    id,tenant_id,station_id,topology_version_id,source_asset_id,target_asset_id,
                    relationship_type,attributes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    f"rel-{uuid4().hex}",
                    tenant_id,
                    station_id,
                    topology_id,
                    asset_ids[relationship["source_asset_key"]],
                    asset_ids[relationship["target_asset_key"]],
                    relationship["relationship_type"],
                    Json(relationship.get("attributes", {})),
                ),
            )
        _audit(conn, tenant_id, actor, "twin.topology.created", topology_id, f"station={station_id};version={version}")
    return {
        "id": topology_id,
        "tenant_id": tenant_id,
        "station_id": station_id,
        "version": int(version),
        "status": "validated",
        "topology_hash": topology_hash,
        "validation": validation,
    }


def activate_topology_version(
    tenant_id: str,
    topology_id: str,
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        row = conn.execute(
            """
            SELECT station_id,version,status,validation_report,topology_hash
            FROM chargeopt.twin_topology_versions
            WHERE id=%s AND tenant_id=%s FOR UPDATE
            """,
            (topology_id, tenant_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown topology_id: {topology_id}")
        if row[2] not in {"validated", "active"} or not bool(row[3].get("valid")):
            raise ValueError("Only a validated topology can be activated.")
        conn.execute(
            """
            UPDATE chargeopt.twin_topology_versions
            SET status='retired',valid_to=now()
            WHERE tenant_id=%s AND station_id=%s AND status='active' AND id<>%s
            """,
            (tenant_id, row[0], topology_id),
        )
        activated = conn.execute(
            """
            UPDATE chargeopt.twin_topology_versions
            SET status='active',valid_from=COALESCE(valid_from,now()),valid_to=NULL,
                activated_by=%s,activated_at=COALESCE(activated_at,now())
            WHERE id=%s
            RETURNING id,station_id,version,status,topology_hash,valid_from,activated_at
            """,
            (actor, topology_id),
        ).fetchone()
        _audit(conn, tenant_id, actor, "twin.topology.activated", topology_id, f"station={row[0]};version={row[1]}")
    return dict(
        zip(
            ("id", "station_id", "version", "status", "topology_hash", "valid_from", "activated_at"),
            activated,
            strict=True,
        )
    )


def get_topology(
    tenant_id: str,
    station_id: str,
    *,
    topology_id: str | None = None,
    at: datetime | None = None,
    scope_tenant_id: str | None = None,
) -> dict[str, Any] | None:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn:
        _set_tenant_context(conn, tenant_id)
        if topology_id:
            row = conn.execute(
                """SELECT id,version,status,topology_hash,validation_report,valid_from,valid_to
                   FROM chargeopt.twin_topology_versions
                   WHERE id=%s AND tenant_id=%s AND station_id=%s""",
                (topology_id, tenant_id, station_id),
            ).fetchone()
        elif at:
            row = conn.execute(
                """SELECT id,version,status,topology_hash,validation_report,valid_from,valid_to
                   FROM chargeopt.twin_topology_versions
                   WHERE tenant_id=%s AND station_id=%s AND valid_from<=%s
                     AND (valid_to IS NULL OR valid_to>%s)
                   ORDER BY version DESC LIMIT 1""",
                (tenant_id, station_id, at, at),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id,version,status,topology_hash,validation_report,valid_from,valid_to
                   FROM chargeopt.twin_topology_versions
                   WHERE tenant_id=%s AND station_id=%s
                   ORDER BY (status='active') DESC,version DESC LIMIT 1""",
                (tenant_id, station_id),
            ).fetchone()
        if row is None:
            return None
        assets = conn.execute(
            """SELECT id,asset_key,asset_type,name,manufacturer,model,serial_number,
                      rated_power_kw,rated_energy_kwh,attributes
               FROM chargeopt.twin_assets WHERE topology_version_id=%s ORDER BY asset_key""",
            (row[0],),
        ).fetchall()
        relationships = conn.execute(
            """SELECT source.asset_key,target.asset_key,rel.relationship_type,rel.attributes
               FROM chargeopt.twin_asset_relationships rel
               JOIN chargeopt.twin_assets source ON source.id=rel.source_asset_id
               JOIN chargeopt.twin_assets target ON target.id=rel.target_asset_id
               WHERE rel.topology_version_id=%s ORDER BY source.asset_key,target.asset_key""",
            (row[0],),
        ).fetchall()
    return {
        "id": row[0],
        "station_id": station_id,
        "version": row[1],
        "status": row[2],
        "topology_hash": row[3],
        "validation": row[4],
        "valid_from": row[5],
        "valid_to": row[6],
        "assets": [
            dict(
                zip(
                    (
                        "id",
                        "asset_key",
                        "asset_type",
                        "name",
                        "manufacturer",
                        "model",
                        "serial_number",
                        "rated_power_kw",
                        "rated_energy_kwh",
                        "attributes",
                    ),
                    item,
                    strict=True,
                )
            )
            for item in assets
        ],
        "relationships": [
            {
                "source_asset_key": item[0],
                "target_asset_key": item[1],
                "relationship_type": item[2],
                "attributes": item[3],
            }
            for item in relationships
        ],
    }


def persist_measurements(
    tenant_id: str,
    station_id: str,
    measurements: list[dict[str, Any]],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    created = 0
    duplicates = 0
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        _ensure_station(conn, tenant_id, station_id)
        topology = conn.execute(
            "SELECT id FROM chargeopt.twin_topology_versions WHERE tenant_id=%s AND station_id=%s AND status='active'",
            (tenant_id, station_id),
        ).fetchone()
        asset_map: dict[str, str] = {}
        if topology:
            asset_map = dict(
                conn.execute(
                    "SELECT asset_key,id FROM chargeopt.twin_assets WHERE topology_version_id=%s",
                    (topology[0],),
                ).fetchall()
            )
        for item in measurements:
            result = conn.execute(
                """
                INSERT INTO chargeopt.twin_measurements (
                    id,tenant_id,station_id,asset_id,point_code,numeric_value,unit,source_timestamp,
                    received_at,sequence_number,source,quality_code,quality_flags,raw_payload,
                    evidence_hash,idempotency_key
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,source,idempotency_key) DO NOTHING RETURNING id
                """,
                (
                    f"mea-{uuid4().hex}",
                    tenant_id,
                    station_id,
                    asset_map.get(item.get("asset_key")),
                    item["point_code"],
                    item["value"],
                    item["unit"],
                    item["source_timestamp"],
                    item["received_at"],
                    item.get("sequence_number"),
                    item["source"],
                    item["quality_code"],
                    Json(item.get("quality_flags", [])),
                    Json(item.get("raw_payload", {})),
                    item["evidence_hash"],
                    item["idempotency_key"],
                ),
            ).fetchone()
            if result:
                created += 1
            else:
                duplicates += 1
        _audit(
            conn,
            tenant_id,
            actor,
            "twin.measurements.ingested",
            f"station:{station_id}",
            f"created={created};duplicates={duplicates}",
        )
    return {"station_id": station_id, "created": created, "duplicates": duplicates}


def load_measurements(
    tenant_id: str,
    station_id: str,
    *,
    point_codes: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 5000,
    scope_tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    clauses = ["tenant_id=%s", "station_id=%s"]
    params: list[Any] = [tenant_id, station_id]
    if point_codes:
        clauses.append("point_code = ANY(%s)")
        params.append(point_codes)
    if start:
        clauses.append("source_timestamp >= %s")
        params.append(start)
    if end:
        clauses.append("source_timestamp < %s")
        params.append(end)
    params.append(limit)
    with get_connection() as conn:
        _set_tenant_context(conn, tenant_id)
        rows = conn.execute(
            f"""SELECT id,asset_id,point_code,numeric_value,unit,source_timestamp,received_at,
                       sequence_number,source,quality_code,quality_flags,evidence_hash,idempotency_key
                FROM chargeopt.twin_measurements WHERE {" AND ".join(clauses)}
                ORDER BY source_timestamp DESC LIMIT %s""",  # noqa: S608 -- clauses are fixed literals
            tuple(params),
        ).fetchall()
    keys = (
        "id",
        "asset_id",
        "point_code",
        "value",
        "unit",
        "source_timestamp",
        "received_at",
        "sequence_number",
        "source",
        "quality_code",
        "quality_flags",
        "evidence_hash",
        "idempotency_key",
    )
    return [dict(zip(keys, row, strict=True)) for row in reversed(rows)]


def persist_state_estimate(
    tenant_id: str,
    station_id: str,
    snapshot: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    contract = snapshot["contract"]
    created = []
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        _ensure_station(conn, tenant_id, station_id)
        topology = conn.execute(
            "SELECT id FROM chargeopt.twin_topology_versions WHERE tenant_id=%s AND station_id=%s AND status='active'",
            (tenant_id, station_id),
        ).fetchone()
        for state in snapshot["states"]:
            state_id = f"ste-{uuid4().hex}"
            conn.execute(
                """
                INSERT INTO chargeopt.twin_state_estimates (
                    id,tenant_id,station_id,topology_version_id,state_code,estimated_value,unit,
                    confidence_low,confidence_high,trust_score,residual,algorithm,model_version,input_hash,
                    evidence_class,quality_flags,estimated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    state_id,
                    tenant_id,
                    station_id,
                    topology[0] if topology else None,
                    state["state_code"],
                    state["value"],
                    state["unit"],
                    state["confidence_low"],
                    state["confidence_high"],
                    state["trust_score"],
                    state.get("residual"),
                    contract["algorithm_version"],
                    contract["model_version"],
                    contract["input_hash"],
                    contract["evidence_class"],
                    Json(contract.get("quality_flags", [])),
                    snapshot["estimated_at"],
                ),
            )
            created.append(state_id)
        _audit(conn, tenant_id, actor, "twin.state.estimated", f"station:{station_id}", f"states={len(created)}")
    return {"station_id": station_id, "state_ids": created, "trust_score": snapshot["trust_score"]}


def persist_simulation(
    tenant_id: str,
    station_id: str,
    simulation: dict[str, Any],
    request_payload: dict[str, Any],
    scenario_type: str,
    idempotency_key: str,
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    run_id = f"sim-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        _ensure_station(conn, tenant_id, station_id)
        topology = conn.execute(
            "SELECT id FROM chargeopt.twin_topology_versions WHERE tenant_id=%s AND station_id=%s AND status='active'",
            (tenant_id, station_id),
        ).fetchone()
        row = conn.execute(
            """
            INSERT INTO chargeopt.twin_simulation_runs (
                id,tenant_id,station_id,topology_version_id,scenario_type,status,evidence_class,
                algorithm_version,random_seed,input_hash,configuration,inputs,outputs,metrics,
                idempotency_key,initiated_by,completed_at
            ) VALUES (%s,%s,%s,%s,%s,'completed',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (tenant_id,idempotency_key) DO NOTHING RETURNING id
            """,
            (
                run_id,
                tenant_id,
                station_id,
                topology[0] if topology else None,
                scenario_type,
                simulation["contract"]["evidence_class"],
                simulation["contract"]["algorithm_version"],
                simulation["random_seed"],
                simulation["contract"]["input_hash"],
                Json({"interval_minutes": simulation["interval_minutes"]}),
                Json(request_payload),
                Json({"trajectory": simulation["trajectory"]}),
                Json(simulation["metrics"]),
                idempotency_key,
                actor,
            ),
        ).fetchone()
        duplicate = row is None
        if duplicate:
            run_id = conn.execute(
                "SELECT id FROM chargeopt.twin_simulation_runs WHERE tenant_id=%s AND idempotency_key=%s",
                (tenant_id, idempotency_key),
            ).fetchone()[0]
        else:
            _audit(conn, tenant_id, actor, "twin.simulation.completed", run_id, f"station={station_id}")
    return {"id": run_id, "duplicate": duplicate, **simulation}


def persist_diagnostics(
    tenant_id: str,
    station_id: str,
    diagnosis: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    created = 0
    refreshed = 0
    maintenance_created = 0
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        _ensure_station(conn, tenant_id, station_id)
        for item in diagnosis["diagnostics"]:
            existing = conn.execute(
                """SELECT id FROM chargeopt.twin_diagnostics
                   WHERE tenant_id=%s AND station_id=%s AND fingerprint=%s AND status IN ('open','acknowledged')
                   ORDER BY last_detected_at DESC LIMIT 1 FOR UPDATE""",
                (tenant_id, station_id, item["fingerprint"]),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE chargeopt.twin_diagnostics
                       SET last_detected_at=%s,confidence=%s,severity=%s,evidence=%s,likely_causes=%s
                       WHERE id=%s""",
                    (
                        item["last_detected_at"],
                        item["confidence"],
                        item["severity"],
                        Json(item["evidence"]),
                        Json(item["likely_causes"]),
                        existing[0],
                    ),
                )
                refreshed += 1
            else:
                conn.execute(
                    """
                    INSERT INTO chargeopt.twin_diagnostics (
                        id,tenant_id,station_id,fingerprint,diagnostic_type,severity,confidence,summary,
                        likely_causes,evidence,algorithm_version,first_detected_at,last_detected_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        f"dia-{uuid4().hex}",
                        tenant_id,
                        station_id,
                        item["fingerprint"],
                        item["diagnostic_type"],
                        item["severity"],
                        item["confidence"],
                        item["summary"],
                        Json(item["likely_causes"]),
                        Json(item["evidence"]),
                        item["algorithm_version"],
                        item["first_detected_at"],
                        item["last_detected_at"],
                    ),
                )
                created += 1
        for recommendation in diagnosis.get("maintenance_recommendations", []):
            fingerprint = recommendation.get("diagnostic_fingerprint") or canonical_hash(
                {"station_id": station_id, "action_type": recommendation["action_type"]}
            )
            priority = recommendation["priority"]
            due_hours = {"critical": 4, "high": 24, "medium": 168, "low": 720}[priority]
            diagnostic = conn.execute(
                """SELECT id FROM chargeopt.twin_diagnostics
                   WHERE tenant_id=%s AND station_id=%s AND fingerprint=%s
                   ORDER BY last_detected_at DESC LIMIT 1""",
                (tenant_id, station_id, fingerprint),
            ).fetchone()
            row = conn.execute(
                """
                INSERT INTO chargeopt.twin_maintenance_actions (
                    id,tenant_id,station_id,diagnostic_id,source_fingerprint,action_type,priority,
                    recommendation,due_at,created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()+(%s || ' hours')::interval,%s)
                ON CONFLICT DO NOTHING RETURNING id
                """,
                (
                    f"mnt-{uuid4().hex}",
                    tenant_id,
                    station_id,
                    diagnostic[0] if diagnostic else None,
                    fingerprint,
                    recommendation["action_type"],
                    priority,
                    recommendation["recommendation"],
                    due_hours,
                    actor,
                ),
            ).fetchone()
            maintenance_created += int(row is not None)
        if created or refreshed:
            _audit(
                conn,
                tenant_id,
                actor,
                "twin.diagnostics.updated",
                f"station:{station_id}",
                f"created={created};refreshed={refreshed}",
            )
    return {
        "station_id": station_id,
        "created": created,
        "refreshed": refreshed,
        "maintenance_created": maintenance_created,
    }


def persist_calibration(
    tenant_id: str,
    station_id: str,
    model_version: str,
    result: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    calibration_id = f"cal-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        _ensure_station(conn, tenant_id, station_id)
        topology = conn.execute(
            "SELECT id FROM chargeopt.twin_topology_versions WHERE tenant_id=%s AND station_id=%s AND status='active'",
            (tenant_id, station_id),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO chargeopt.twin_model_calibrations (
                id,tenant_id,station_id,topology_version_id,model_scope,model_version,evidence_class,
                input_hash,sample_count,parameters,metrics,quality_gate,status,calibrated_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                calibration_id,
                tenant_id,
                station_id,
                topology[0] if topology else None,
                result["model_scope"],
                model_version,
                result["evidence_class"],
                result["input_hash"],
                result["sample_count"],
                Json(result["parameters"]),
                Json(result["metrics"]),
                Json(result["quality_gate"]),
                result["status"],
                actor,
            ),
        )
        _audit(conn, tenant_id, actor, "twin.model.calibrated", calibration_id, f"status={result['status']}")
    return {"id": calibration_id, **result}


def list_maintenance_actions(
    tenant_id: str,
    station_id: str,
    scope_tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn:
        _set_tenant_context(conn, tenant_id)
        rows = conn.execute(
            """SELECT id,diagnostic_id,source_fingerprint,action_type,priority,status,recommendation,
                      due_at,assigned_to,created_by,completed_by,outcome,created_at,completed_at
               FROM chargeopt.twin_maintenance_actions
               WHERE tenant_id=%s AND station_id=%s ORDER BY (status IN ('planned','in_progress')) DESC,due_at""",
            (tenant_id, station_id),
        ).fetchall()
    keys = (
        "id",
        "diagnostic_id",
        "source_fingerprint",
        "action_type",
        "priority",
        "status",
        "recommendation",
        "due_at",
        "assigned_to",
        "created_by",
        "completed_by",
        "outcome",
        "created_at",
        "completed_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def transition_maintenance_action(
    tenant_id: str,
    action_id: str,
    target_status: str,
    actor: str,
    *,
    assigned_to: str | None = None,
    outcome: dict[str, Any] | None = None,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    transitions = {
        "planned": {"in_progress", "cancelled"},
        "in_progress": {"completed", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        current = conn.execute(
            "SELECT status FROM chargeopt.twin_maintenance_actions WHERE id=%s AND tenant_id=%s FOR UPDATE",
            (action_id, tenant_id),
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown maintenance action: {action_id}")
        if target_status not in transitions[current[0]]:
            raise ValueError(f"Illegal maintenance transition: {current[0]} -> {target_status}")
        row = conn.execute(
            """UPDATE chargeopt.twin_maintenance_actions
               SET status=%s,assigned_to=COALESCE(%s,assigned_to),
                   completed_by=CASE WHEN %s='completed' THEN %s ELSE completed_by END,
                   outcome=CASE WHEN %s='completed' THEN %s ELSE outcome END,
                   completed_at=CASE WHEN %s='completed' THEN now() ELSE completed_at END
               WHERE id=%s
               RETURNING id,station_id,status,assigned_to,completed_by,outcome,completed_at""",
            (
                target_status,
                assigned_to,
                target_status,
                actor,
                target_status,
                Json(outcome or {}),
                target_status,
                action_id,
            ),
        ).fetchone()
        _audit(conn, tenant_id, actor, "twin.maintenance.transitioned", action_id, f"{current[0]}->{target_status}")
    return dict(
        zip(("id", "station_id", "status", "assigned_to", "completed_by", "outcome", "completed_at"), row, strict=True)
    )


def persist_causal_study(
    tenant_id: str,
    station_id: str | None,
    result: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    study_id = f"cau-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        if station_id:
            _ensure_station(conn, tenant_id, station_id)
        conn.execute(
            """
            INSERT INTO chargeopt.twin_causal_studies (
                id,tenant_id,station_id,status,estimand,algorithm_version,evidence_class,input_hash,
                sample_count,result,assumptions,created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                study_id,
                tenant_id,
                station_id,
                result["status"],
                result["estimand"],
                result["algorithm_version"],
                result["evidence_class"],
                result["input_hash"],
                result["sample_count"],
                Json(result),
                Json(result.get("assumptions", [])),
                actor,
            ),
        )
        _audit(
            conn, tenant_id, actor, "twin.causal_study.created", study_id, f"auditable={result.get('auditable', False)}"
        )
    return {"id": study_id, **result}


def record_qualification_evidence(
    tenant_id: str,
    station_id: str | None,
    evidence_date: date,
    category: str,
    qualified: bool,
    evidence: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    evidence_hash = canonical_hash(evidence)
    evidence_id = f"qev-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        if station_id:
            _ensure_station(conn, tenant_id, station_id)
        row = conn.execute(
            """
            INSERT INTO chargeopt.twin_qualification_evidence (
                id,tenant_id,station_id,evidence_date,category,qualified,evidence,evidence_hash,recorded_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,station_id,evidence_date,category) DO NOTHING RETURNING id
            """,
            (
                evidence_id,
                tenant_id,
                station_id,
                evidence_date,
                category,
                qualified,
                Json(evidence),
                evidence_hash,
                actor,
            ),
        ).fetchone()
        duplicate = row is None
        if duplicate:
            evidence_id = conn.execute(
                """SELECT id FROM chargeopt.twin_qualification_evidence
                   WHERE tenant_id=%s AND station_id IS NOT DISTINCT FROM %s AND evidence_date=%s AND category=%s""",
                (tenant_id, station_id, evidence_date, category),
            ).fetchone()[0]
        else:
            _audit(
                conn,
                tenant_id,
                actor,
                "twin.qualification.recorded",
                evidence_id,
                f"category={category};qualified={qualified}",
            )
    return {"id": evidence_id, "evidence_hash": evidence_hash, "duplicate": duplicate}


def load_qualification_evidence(
    tenant_id: str,
    station_id: str | None,
    scope_tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn:
        _set_tenant_context(conn, tenant_id)
        rows = conn.execute(
            """SELECT id,evidence_date,category,qualified,evidence,evidence_hash,recorded_by,recorded_at
               FROM chargeopt.twin_qualification_evidence
               WHERE tenant_id=%s AND station_id IS NOT DISTINCT FROM %s ORDER BY evidence_date,category""",
            (tenant_id, station_id),
        ).fetchall()
    keys = ("id", "evidence_date", "category", "qualified", "evidence", "evidence_hash", "recorded_by", "recorded_at")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def _ensure_station(conn: Any, tenant_id: str, station_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM chargeopt.stations WHERE id=%s AND tenant_id=%s",
        (station_id, tenant_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown station_id: {station_id}")


def _audit(conn: Any, tenant_id: str, actor: str, action: str, target: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO chargeopt.audit_entries (id,tenant_id,timestamp,actor,action,target,detail)
           VALUES (%s,%s,now(),%s,%s,%s,%s)""",
        (f"au-{uuid4().hex}", tenant_id, actor, action, target, detail),
    )
