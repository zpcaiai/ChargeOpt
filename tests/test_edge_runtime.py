import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from chargeopt.edge_runtime import (
    CommandRouter,
    ControlPlaneClient,
    EdgeConfigurationError,
    ModbusConnector,
    build_command_server,
    load_config,
)


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"status":"accepted"}'


def test_control_plane_client_forwards_signed_idempotent_message():
    client = ControlPlaneClient("https://control.example", "device-key")
    with patch("urllib.request.urlopen", return_value=_Response()) as send:
        result = client.forward("mqtt", "st-1", "dev-1", "topic/a", "telemetry", {"load_kw": 12})

    request = send.call_args.args[0]
    body = json.loads(request.data)
    assert result == {"status": "accepted"}
    assert request.full_url.endswith("/api/v1/protocols/mqtt/messages")
    assert request.headers["X-api-key"] == "device-key"
    assert len(body["idempotency_key"]) == 64


def test_control_plane_client_exposes_http_error_detail():
    error = urllib.error.HTTPError("https://control.example", 403, "forbidden", {}, None)
    error.read = MagicMock(return_value=b"tenant denied")
    with patch("urllib.request.urlopen", side_effect=error), pytest.raises(RuntimeError, match="tenant denied"):
        ControlPlaneClient("https://control.example", "bad").forward(
            "mqtt", "st-1", None, "topic/a", "telemetry", {"load_kw": 12}
        )


class _ModbusResponse:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self.error = error

    def isError(self):
        return self.error


class _ModbusClient:
    def __init__(self):
        self.closed = False
        self.write = None

    def connect(self):
        return True

    def read_holding_registers(self, address, *, count, device_id):
        assert count == 1
        return _ModbusResponse([100 + address + device_id])

    def write_register(self, address, value, *, device_id):
        self.write = (address, value, device_id)
        return _ModbusResponse()

    def close(self):
        self.closed = True


def test_modbus_connector_polls_and_forwards_scaled_registers():
    field_client = _ModbusClient()
    control_plane = MagicMock()
    control_plane.forward.return_value = {"status": "accepted"}
    connector = ModbusConnector(
        {
            "host": "10.0.0.2",
            "station_id": "st-1",
            "device_id": "dev-1",
            "registers": {
                "load_kw": {"address": 0, "device_id": 1, "scale": 0.1},
                "storage_soc": {"address": 2, "device_id": 1, "scale": 0.001},
            },
        },
        control_plane,
        client_factory=lambda *_args, **_kwargs: field_client,
    )

    assert connector.poll_once() == {"status": "accepted"}
    payload = control_plane.forward.call_args.args[5]
    assert payload["registers"]["load_kw"] == pytest.approx(10.1)
    assert payload["registers"]["storage_soc"] == pytest.approx(0.103)
    assert field_client.closed is True


def test_modbus_connector_writes_bounded_signed_command():
    field_client = _ModbusClient()
    connector = ModbusConnector(
        {
            "host": "10.0.0.2",
            "station_id": "st-1",
            "command_register": {"address": 100, "device_id": 2, "scale": 0.1, "min_raw": -500, "max_raw": 500},
        },
        MagicMock(),
        client_factory=lambda *_args, **_kwargs: field_client,
    )

    result = connector.execute({"id": "tsk-1", "payload": {"target_adjustment_kw": -20}})
    assert result["edge_status"] == "succeeded"
    assert field_client.write == (100, 65336, 2)

    with pytest.raises(EdgeConfigurationError, match="outside"):
        connector.execute({"id": "tsk-2", "payload": {"power_kw": 100}})


class _Adapter:
    def execute(self, task):
        return {"edge_status": "succeeded", "value": task["payload"]["power_kw"]}


def test_command_router_fails_closed_without_mapping():
    router = CommandRouter({"modbus": _Adapter()}, device_protocols={"dev-1": "modbus"})
    result = router.execute({"id": "tsk-1", "device_id": "dev-1", "payload": {"power_kw": 5}})
    assert result["value"] == 5
    assert result["task_id"] == "tsk-1"
    with pytest.raises(EdgeConfigurationError, match="No protocol mapping"):
        router.execute({"id": "tsk-2", "payload": {}})


@contextmanager
def _running_server(token="a" * 24):
    server = build_command_server("127.0.0.1", 0, token, CommandRouter({"modbus": _Adapter()}, {"dev-1": "modbus"}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/chargeopt/tasks/execute", token
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_command_server_requires_token_and_routes_task():
    with _running_server() as (url, token):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = json.dumps({"task": {"id": "tsk-1", "device_id": "dev-1", "payload": {"power_kw": 5}}}).encode()
        unauthorized = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as denied:
            opener.open(unauthorized)
        assert denied.value.code == 401

        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with opener.open(request) as response:
            result = json.load(response)
        assert result["edge_status"] == "succeeded"
        assert result["task_id"] == "tsk-1"


def test_load_config_rejects_missing_secrets(tmp_path):
    path = tmp_path / "edge.json"
    path.write_text('{"control_plane_url":"https://example.com"}')
    with pytest.raises(EdgeConfigurationError, match="control_plane_api_key"):
        load_config(path)


def test_gateway_token_minimum_length():
    with pytest.raises(EdgeConfigurationError, match="24"):
        build_command_server("127.0.0.1", 0, "short", CommandRouter({}))
