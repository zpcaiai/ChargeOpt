"""Forecast model metrics, registry, quality gates, and maker-checker promotion."""

from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from .db import get_connection
from .repository import _ensure_tenant_allowed, _set_tenant_context


def evaluate_quantile_forecast(
    actual: list[float], p10: list[float], p50: list[float], p90: list[float]
) -> dict[str, float]:
    if not actual or not (len(actual) == len(p10) == len(p50) == len(p90)):
        raise ValueError("actual, p10, p50, and p90 must be non-empty and have equal lengths")
    if any(low > median or median > high for low, median, high in zip(p10, p50, p90, strict=True)):
        raise ValueError("forecast quantiles must satisfy p10 <= p50 <= p90")
    errors = [forecast - observed for observed, forecast in zip(actual, p50, strict=True)]
    absolute = [abs(value) for value in errors]
    denominator = max(sum(abs(value) for value in actual), 1e-9)
    coverage = sum(low <= observed <= high for observed, low, high in zip(actual, p10, p90, strict=True)) / len(actual)
    return {
        "mae": round(sum(absolute) / len(actual), 6),
        "rmse": round(math.sqrt(sum(value * value for value in errors) / len(actual)), 6),
        "wape": round(sum(absolute) / denominator, 6),
        "bias": round(sum(errors) / len(actual), 6),
        "coverage_80": round(coverage, 6),
        "pinball_p10": round(_pinball(actual, p10, 0.1), 6),
        "pinball_p50": round(_pinball(actual, p50, 0.5), 6),
        "pinball_p90": round(_pinball(actual, p90, 0.9), 6),
    }


def _pinball(actual: list[float], forecast: list[float], quantile: float) -> float:
    losses = []
    for observed, predicted in zip(actual, forecast, strict=True):
        residual = observed - predicted
        losses.append(max(quantile * residual, (quantile - 1) * residual))
    return sum(losses) / len(losses)


def quality_gate(metrics: dict[str, float], reference_metrics: dict[str, float] | None = None) -> dict[str, Any]:
    checks = {
        "wape_lte_20pct": metrics["wape"] <= 0.20,
        "coverage_80_between_70_and_98pct": 0.70 <= metrics["coverage_80"] <= 0.98,
        "relative_bias_lte_10pct": abs(metrics["bias"]) <= max(metrics["mae"], 1.0),
    }
    drift_score = 0.0
    if reference_metrics:
        reference_wape = max(float(reference_metrics.get("wape", 0)), 1e-6)
        drift_score = max(0.0, (metrics["wape"] - reference_wape) / reference_wape)
        checks["wape_degradation_lte_25pct"] = drift_score <= 0.25
    return {"passed": all(checks.values()), "checks": checks, "drift_score": round(drift_score, 6)}


def register_model(
    tenant_id: str,
    payload: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    model_id = f"mdl-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        row = conn.execute(
            """
            INSERT INTO chargeopt.model_registry (
                id,tenant_id,scope,version,algorithm,artifact_uri,artifact_sha256,
                training_data_hash,training_window_start,training_window_end,metrics,created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id,tenant_id,scope,version,algorithm,artifact_uri,artifact_sha256,
                      training_data_hash,training_window_start,training_window_end,status,metrics,
                      created_by,approved_by,approved_at,created_at,updated_at
            """,
            (
                model_id,
                tenant_id,
                payload["scope"],
                payload["version"],
                payload["algorithm"],
                payload["artifact_uri"],
                payload["artifact_sha256"],
                payload["training_data_hash"],
                payload["training_window_start"],
                payload["training_window_end"],
                Json(payload.get("metrics", {})),
                actor,
            ),
        ).fetchone()
        conn.execute(
            """INSERT INTO chargeopt.audit_entries (id,tenant_id,timestamp,actor,action,target,detail)
               VALUES (%s,%s,now(),%s,'model.registered',%s,%s)""",
            (f"au-{uuid4().hex}", tenant_id, actor, model_id, f"{payload['scope']}:{payload['version']}"),
        )
    return _model_row(row)


def evaluate_model(
    model_id: str,
    tenant_id: str,
    payload: dict[str, Any],
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    metrics = evaluate_quantile_forecast(payload["actual"], payload["p10"], payload["p50"], payload["p90"])
    gate = quality_gate(metrics, payload.get("reference_metrics"))
    evaluation_id = f"eval-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        model = conn.execute(
            "SELECT id FROM chargeopt.model_registry WHERE id=%s AND tenant_id=%s FOR UPDATE", (model_id, tenant_id)
        ).fetchone()
        if model is None:
            raise KeyError(f"Unknown model_id: {model_id}")
        conn.execute(
            """
            INSERT INTO chargeopt.model_evaluations (
                id,tenant_id,model_id,dataset_hash,sample_count,metrics,quality_gate,drift_detected,evaluated_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                evaluation_id,
                tenant_id,
                model_id,
                payload["dataset_hash"],
                len(payload["actual"]),
                Json(metrics),
                Json(gate),
                gate["drift_score"] > 0.25,
                actor,
            ),
        )
        conn.execute(
            "UPDATE chargeopt.model_registry SET status=CASE WHEN %s THEN 'shadow' ELSE 'rejected' END, metrics=%s, updated_at=now() WHERE id=%s",
            (gate["passed"], Json(metrics), model_id),
        )
    return {"id": evaluation_id, "model_id": model_id, "metrics": metrics, "quality_gate": gate}


def promote_model(
    model_id: str,
    tenant_id: str,
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        model = conn.execute(
            """
            SELECT id,scope,status,created_by FROM chargeopt.model_registry
            WHERE id=%s AND tenant_id=%s FOR UPDATE
            """,
            (model_id, tenant_id),
        ).fetchone()
        if model is None:
            raise KeyError(f"Unknown model_id: {model_id}")
        if model[3] == actor:
            raise PermissionError("Model creator cannot approve their own model.")
        if model[2] != "shadow":
            raise ValueError("Only a quality-gated shadow model can be promoted.")
        evaluation = conn.execute(
            "SELECT quality_gate FROM chargeopt.model_evaluations WHERE model_id=%s ORDER BY evaluated_at DESC LIMIT 1",
            (model_id,),
        ).fetchone()
        if evaluation is None or not bool(evaluation[0].get("passed")):
            raise ValueError("The latest model evaluation did not pass the quality gate.")
        conn.execute(
            "UPDATE chargeopt.model_registry SET status='retired',updated_at=now() WHERE tenant_id=%s AND scope=%s AND status='active'",
            (tenant_id, model[1]),
        )
        row = conn.execute(
            """
            UPDATE chargeopt.model_registry
            SET status='active',approved_by=%s,approved_at=now(),updated_at=now()
            WHERE id=%s
            RETURNING id,tenant_id,scope,version,algorithm,artifact_uri,artifact_sha256,
                      training_data_hash,training_window_start,training_window_end,status,metrics,
                      created_by,approved_by,approved_at,created_at,updated_at
            """,
            (actor, model_id),
        ).fetchone()
        conn.execute(
            """INSERT INTO chargeopt.audit_entries (id,tenant_id,timestamp,actor,action,target,detail)
               VALUES (%s,%s,now(),%s,'model.promoted',%s,%s)""",
            (f"au-{uuid4().hex}", tenant_id, actor, model_id, str(model[1])),
        )
    return _model_row(row)


def list_models(tenant_id: str, scope_tenant_id: str | None = None) -> list[dict[str, Any]]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn:
        _set_tenant_context(conn, tenant_id)
        rows = conn.execute(
            """
            SELECT id,tenant_id,scope,version,algorithm,artifact_uri,artifact_sha256,
                   training_data_hash,training_window_start,training_window_end,status,metrics,
                   created_by,approved_by,approved_at,created_at,updated_at
            FROM chargeopt.model_registry WHERE tenant_id=%s ORDER BY scope,status,created_at DESC
            """,
            (tenant_id,),
        ).fetchall()
    return [_model_row(row) for row in rows]


def _model_row(row: Any) -> dict[str, Any]:
    keys = (
        "id",
        "tenant_id",
        "scope",
        "version",
        "algorithm",
        "artifact_uri",
        "artifact_sha256",
        "training_data_hash",
        "training_window_start",
        "training_window_end",
        "status",
        "metrics",
        "created_by",
        "approved_by",
        "approved_at",
        "created_at",
        "updated_at",
    )
    return dict(zip(keys, row, strict=True))
