from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_twin_snapshot_and_topology_are_available_in_memory(client):
    snapshot = await client.get("/api/v1/digital-twin/stations/st-hq-hongqiao")
    topology = await client.get("/api/v1/digital-twin/stations/st-hq-hongqiao/topology")

    assert snapshot.status_code == 200
    assert snapshot.json()["state"]["contract"]["evidence_class"] == "synthetic"
    assert snapshot.json()["state"]["autonomy_gate"]["allowed"] is False
    assert topology.status_code == 200
    assert topology.json()["validation"]["valid"] is True


@pytest.mark.asyncio
async def test_twin_unknown_station_returns_404(client):
    response = await client.get("/api/v1/digital-twin/stations/not-a-station")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_twin_simulation_runs_without_database_as_synthetic(client):
    response = await client.post(
        "/api/v1/digital-twin/simulations",
        json={
            "station_id": "st-hq-hongqiao",
            "scenario_type": "what_if",
            "evidence_class": "synthetic",
            "idempotency_key": "api-sim-1",
            "initial_state": {"storage_soc": 0.6},
            "schedule": [
                {
                    "timestamp": "2026-07-18T04:00:00+00:00",
                    "load_kw": 1200,
                    "pv_kw": 100,
                    "storage_power_kw": -200,
                }
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["persisted"] is False
    assert body["contract"]["evidence_class"] == "synthetic"
    assert body["metrics"]["constraint_violation_count"] == 0


@pytest.mark.asyncio
async def test_twin_simulation_rejects_observed_claim_without_database(client):
    response = await client.post(
        "/api/v1/digital-twin/simulations",
        json={
            "station_id": "st-hq-hongqiao",
            "evidence_class": "observed",
            "idempotency_key": "api-sim-observed",
            "schedule": [{"load_kw": 100}],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_twin_causal_study_fails_closed_for_small_synthetic_sample(client):
    response = await client.post(
        "/api/v1/digital-twin/causal-studies",
        json={
            "evidence_class": "synthetic",
            "observations": [
                {
                    "treated": index % 2 == 0,
                    "outcome": index,
                    "covariates": {"load": index},
                }
                for index in range(12)
            ],
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["auditable"] is False


@pytest.mark.asyncio
async def test_twin_calibration_and_trajectory_comparison(client):
    predicted = [index * 10 for index in range(1, 31)]
    observed = [value * 1.05 + 3 for value in predicted]
    calibration = await client.post(
        "/api/v1/digital-twin/calibrations",
        json={
            "station_id": "st-hq-hongqiao",
            "evidence_class": "synthetic",
            "predicted": predicted,
            "observed": observed,
        },
    )
    comparison = await client.post(
        "/api/v1/digital-twin/trajectory-comparisons",
        json={
            "predicted": [{"grid_kw": 100}, {"grid_kw": 110}],
            "observed": [{"grid_kw": 101}, {"grid_kw": 112}],
            "fields": ["grid_kw"],
        },
    )

    assert calibration.status_code == 201
    assert calibration.json()["status"] == "passed"
    assert calibration.json()["persisted"] is False
    assert comparison.status_code == 200
    assert comparison.json()["metrics"]["grid_kw"]["mae"] == 1.5


@pytest.mark.asyncio
async def test_twin_fault_injection_suite_runs_as_replay(client):
    response = await client.post(
        "/api/v1/digital-twin/commissioning/fault-injection",
        json={"station_id": "st-hq-hongqiao"},
    )

    assert response.status_code == 200
    assert response.json()["qualified"] is True
    assert response.json()["persisted"] is False


@pytest.mark.asyncio
async def test_twin_recommendation_optimization_exposes_safety_gate(client):
    response = await client.post(
        "/api/v1/digital-twin/optimization",
        json={"station_id": "st-hq-hongqiao", "horizon_hours": 4, "mode": "recommend"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "recommend"
    assert body["safety_gate"]["allowed"] is True
    assert body["qualification"]["ready"] is False


@pytest.mark.asyncio
async def test_twin_auto_optimization_requires_field_qualification(client):
    response = await client.post(
        "/api/v1/digital-twin/optimization",
        json={"station_id": "st-hq-hongqiao", "horizon_hours": 4, "mode": "auto"},
    )

    assert response.status_code == 409
    assert "field qualification" in str(response.json()["detail"]).lower()


@pytest.mark.asyncio
async def test_twin_persistence_routes_require_database(client):
    topology = await client.post(
        "/api/v1/digital-twin/topologies",
        json={
            "station_id": "st-hq-hongqiao",
            "assets": [{"asset_key": "station", "asset_type": "station", "name": "Station"}],
            "relationships": [],
        },
    )
    qualification = await client.get("/api/v1/digital-twin/qualification")

    assert topology.status_code == 503
    assert qualification.status_code == 200
    assert qualification.json()["ready"] is False
