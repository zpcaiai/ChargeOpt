"""Deployable site-edge runtime for OCPP 1.6, Modbus TCP, and MQTT.

The edge process keeps field credentials on site. It forwards normalized,
idempotent device messages to the control plane and exposes an authenticated
command endpoint for the leased ChargeOpt worker.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger("chargeopt.edge")


class EdgeConfigurationError(ValueError):
    """Raised when a site configuration could permit unsafe operation."""


class CommandAdapter(Protocol):
    def execute(self, task: dict[str, Any]) -> dict[str, Any]: ...


def _stable_key(*parts: object) -> str:
    material = ":".join(str(part) for part in parts)
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class ControlPlaneClient:
    base_url: str
    api_key: str
    timeout_seconds: float = 15.0

    def forward(
        self,
        protocol: str,
        station_id: str,
        device_id: str | None,
        external_id: str,
        message_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "station_id": station_id,
            "device_id": device_id,
            "external_id": external_id,
            "message_type": message_type,
            "payload": payload,
            "idempotency_key": idempotency_key
            or _stable_key(
                protocol, external_id, message_type, payload.get("timestamp"), json.dumps(payload, sort_keys=True)
            ),
        }
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/v1/protocols/{protocol}/messages",
            data=json.dumps(body, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw) if raw else {"status": response.status}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:1000]
            raise RuntimeError(f"control plane returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"control plane unavailable: {exc.reason}") from exc


@dataclass
class MqttConnector:
    config: dict[str, Any]
    control_plane: ControlPlaneClient
    client: Any = field(init=False, default=None)

    def start(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("Install ChargeOpt with the 'edge' extra to use MQTT.") from exc

        if not self.config.get("ca_cert"):
            raise EdgeConfigurationError("MQTT ca_cert is required; plaintext brokers are refused.")
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.get("client_id", "chargeopt-edge"),
            clean_session=False,
            protocol=mqtt.MQTTv311,
            manual_ack=True,
        )
        if self.config.get("username"):
            self.client.username_pw_set(self.config["username"], self.config.get("password"))
        self.client.tls_set(
            ca_certs=self.config["ca_cert"],
            certfile=self.config.get("client_cert"),
            keyfile=self.config.get("client_key"),
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.tls_insecure_set(False)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.config["host"], int(self.config.get("port", 8883)), keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if int(reason_code) != 0:
            LOGGER.error("mqtt_connect_failed", extra={"reason_code": int(reason_code)})
            return
        for subscription in self.config.get("subscriptions", []):
            client.subscribe(subscription["topic"], qos=int(subscription.get("qos", 1)))

    def _subscription_for(self, topic: str) -> dict[str, Any]:
        for subscription in self.config.get("subscriptions", []):
            pattern = str(subscription["topic"])
            prefix = pattern.split("#", 1)[0].split("+", 1)[0]
            if topic.startswith(prefix):
                return subscription
        raise EdgeConfigurationError(f"No MQTT subscription mapping for topic {topic!r}.")

    def _on_message(self, client: Any, _userdata: Any, message: Any) -> None:
        try:
            mapping = self._subscription_for(message.topic)
            payload = json.loads(message.payload.decode())
            if not isinstance(payload, dict):
                raise ValueError("MQTT payload must be a JSON object.")
            payload.setdefault("timestamp", datetime.now(UTC).isoformat())
            self.control_plane.forward(
                "mqtt",
                mapping["station_id"],
                mapping.get("device_id"),
                message.topic,
                mapping.get("message_type", "telemetry"),
                payload,
                _stable_key("mqtt", message.topic, message.mid, payload.get("timestamp")),
            )
            client.ack(message.mid, message.qos)
        except Exception:
            # Deliberately leave QoS 1/2 messages unacknowledged for broker redelivery.
            LOGGER.exception("mqtt_message_rejected", extra={"topic": message.topic, "mid": message.mid})

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("MQTT connector is not connected.")
        payload = task.get("payload") or {}
        topic = payload.get("command_topic") or self.config.get("command_topic")
        if not topic:
            raise EdgeConfigurationError("MQTT command_topic is required.")
        command = {"task_id": task["id"], "command": payload, "issued_at": datetime.now(UTC).isoformat()}
        result = self.client.publish(topic, json.dumps(command, separators=(",", ":")), qos=1, retain=False)
        result.wait_for_publish(timeout=float(self.config.get("publish_timeout_seconds", 10)))
        if not result.is_published():
            raise RuntimeError("MQTT broker did not acknowledge the command publish.")
        return {"edge_status": "accepted", "protocol": "mqtt", "topic": topic, "message_id": result.mid}


@dataclass
class ModbusConnector:
    config: dict[str, Any]
    control_plane: ControlPlaneClient
    client_factory: Any | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def _client(self) -> Any:
        if self.client_factory is None:
            try:
                from pymodbus.client import ModbusTcpClient
            except ImportError as exc:
                raise RuntimeError("Install ChargeOpt with the 'edge' extra to use Modbus.") from exc
            self.client_factory = ModbusTcpClient
        return self.client_factory(
            self.config["host"],
            port=int(self.config.get("port", 502)),
            timeout=float(self.config.get("timeout_seconds", 3)),
        )

    def start(self) -> None:
        if not self.config.get("network_trust_boundary"):
            raise EdgeConfigurationError("Modbus network_trust_boundary must document the site VPN/VLAN boundary.")
        self._thread = threading.Thread(target=self._poll_loop, name="chargeopt-modbus", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _poll_loop(self) -> None:
        interval = float(self.config.get("poll_interval_seconds", 10))
        while not self._stop.wait(0 if self._stop.is_set() else interval):
            try:
                self.poll_once()
            except Exception:
                LOGGER.exception("modbus_poll_failed", extra={"host": self.config.get("host")})

    def poll_once(self) -> dict[str, Any]:
        registers: dict[str, float] = {}
        client = self._client()
        try:
            if not client.connect():
                raise RuntimeError("Modbus TCP connection failed.")
            for name, mapping in self.config.get("registers", {}).items():
                response = client.read_holding_registers(
                    int(mapping["address"]),
                    count=int(mapping.get("count", 1)),
                    device_id=int(mapping.get("device_id", 1)),
                )
                if response.isError():
                    raise RuntimeError(f"Modbus read failed for {name}: {response}")
                raw = int(response.registers[0])
                if mapping.get("signed") and raw >= 32768:
                    raw -= 65536
                registers[name] = raw * float(mapping.get("scale", 1))
        finally:
            client.close()
        payload = {"registers": registers, "scale": 1, "timestamp": datetime.now(UTC).isoformat()}
        return self.control_plane.forward(
            "modbus",
            self.config["station_id"],
            self.config.get("device_id"),
            self.config.get("external_id", f"{self.config['host']}:{self.config.get('port', 502)}"),
            "holding_registers",
            payload,
        )

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload") or {}
        register = self.config.get("command_register") or {}
        if "address" not in register:
            raise EdgeConfigurationError("Modbus command_register.address is required.")
        target = payload.get("target_grid_kw", payload.get("power_kw", payload.get("target_adjustment_kw")))
        if target is None:
            raise EdgeConfigurationError("Dispatch command has no supported power target.")
        scale = float(register.get("scale", 1))
        raw = int(round(float(target) / scale))
        minimum = int(register.get("min_raw", -32768))
        maximum = int(register.get("max_raw", 32767))
        if not minimum <= raw <= maximum:
            raise EdgeConfigurationError(f"Modbus command {raw} is outside [{minimum}, {maximum}].")
        encoded = raw & 0xFFFF
        client = self._client()
        try:
            if not client.connect():
                raise RuntimeError("Modbus TCP connection failed.")
            response = client.write_register(
                int(register["address"]), encoded, device_id=int(register.get("device_id", 1))
            )
            if response.isError():
                raise RuntimeError(f"Modbus write failed: {response}")
        finally:
            client.close()
        return {
            "edge_status": "succeeded",
            "protocol": "modbus",
            "address": int(register["address"]),
            "raw_value": raw,
            "target_kw": float(target),
        }


class OcppRegistry:
    def __init__(self) -> None:
        self._charge_points: dict[str, Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register(self, charge_point_id: str, charge_point: Any) -> None:
        with self._lock:
            self._charge_points[charge_point_id] = charge_point

    def unregister(self, charge_point_id: str) -> None:
        with self._lock:
            self._charge_points.pop(charge_point_id, None)

    def call(self, charge_point_id: str, request: Any, timeout: float) -> Any:
        with self._lock:
            charge_point = self._charge_points.get(charge_point_id)
        if charge_point is None or self._loop is None:
            raise RuntimeError(f"OCPP charge point {charge_point_id!r} is offline.")
        future = asyncio.run_coroutine_threadsafe(charge_point.call(request), self._loop)
        return future.result(timeout=timeout)


@dataclass
class OcppConnector:
    config: dict[str, Any]
    control_plane: ControlPlaneClient
    registry: OcppRegistry = field(default_factory=OcppRegistry)

    async def serve(self, stop: threading.Event | None = None) -> None:
        try:
            import websockets
            from ocpp.routing import on
            from ocpp.v16 import ChargePoint, call_result
            from ocpp.v16.enums import Action, RegistrationStatus
        except ImportError as exc:
            raise RuntimeError("Install ChargeOpt with the 'edge' extra to use OCPP.") from exc

        connector = self

        class SiteChargePoint(ChargePoint):
            @on(Action.boot_notification)
            async def on_boot_notification(self, **payload: Any) -> Any:
                connector._forward(self.id, "BootNotification", payload)
                return call_result.BootNotification(
                    current_time=datetime.now(UTC).isoformat(),
                    interval=int(connector.config.get("heartbeat_interval", 60)),
                    status=RegistrationStatus.accepted,
                )

            @on(Action.meter_values)
            async def on_meter_values(self, **payload: Any) -> Any:
                connector._forward(self.id, "MeterValues", payload)
                return call_result.MeterValues()

            @on(Action.status_notification)
            async def on_status_notification(self, **payload: Any) -> Any:
                connector._forward(self.id, "StatusNotification", payload)
                return call_result.StatusNotification()

        async def on_connect(connection: Any) -> None:
            requested = getattr(connection, "request", None)
            path = getattr(requested, "path", "")
            charge_point_id = path.strip("/")
            if not charge_point_id:
                await connection.close(code=1008, reason="charge point id required")
                return
            allowed = set(connector.config.get("allowed_charge_points", []))
            if allowed and charge_point_id not in allowed:
                await connection.close(code=1008, reason="charge point not allowed")
                return
            charge_point = SiteChargePoint(charge_point_id, connection)
            connector.registry.register(charge_point_id, charge_point)
            try:
                await charge_point.start()
            finally:
                connector.registry.unregister(charge_point_id)

        ssl_context = _server_ssl_context(self.config)
        self.registry.bind_loop(asyncio.get_running_loop())
        async with websockets.serve(
            on_connect,
            self.config.get("host", "0.0.0.0"),
            int(self.config.get("port", 9000)),
            subprotocols=["ocpp1.6"],
            ssl=ssl_context,
            ping_interval=30,
            ping_timeout=30,
        ):
            while stop is None or not stop.is_set():
                await asyncio.sleep(1)

    def _mapping(self, charge_point_id: str) -> dict[str, Any]:
        mapping = self.config.get("charge_points", {}).get(charge_point_id)
        if mapping is None:
            raise EdgeConfigurationError(f"No station mapping for OCPP charge point {charge_point_id!r}.")
        return mapping

    def _forward(self, charge_point_id: str, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        mapping = self._mapping(charge_point_id)
        return self.control_plane.forward(
            "ocpp", mapping["station_id"], mapping.get("device_id"), charge_point_id, message_type, payload
        )

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        try:
            from ocpp.v16 import call
        except ImportError as exc:
            raise RuntimeError("Install ChargeOpt with the 'edge' extra to use OCPP.") from exc
        payload = task.get("payload") or {}
        charge_point_id = payload.get("charge_point_id") or self.config.get("device_charge_points", {}).get(
            task.get("device_id")
        )
        if not charge_point_id:
            raise EdgeConfigurationError("OCPP charge_point_id mapping is required for commands.")
        limit_kw = payload.get("target_grid_kw", payload.get("power_kw", payload.get("target_adjustment_kw")))
        if limit_kw is None or float(limit_kw) < 0:
            raise EdgeConfigurationError("OCPP charging limit must be a non-negative kW value.")
        profile = {
            "chargingProfileId": int(payload.get("profile_id", 1)),
            "stackLevel": int(payload.get("stack_level", 0)),
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": float(limit_kw) * 1000}],
            },
        }
        request = call.SetChargingProfile(
            connector_id=int(payload.get("connector_id", 0)), cs_charging_profiles=profile
        )
        response = self.registry.call(charge_point_id, request, float(self.config.get("command_timeout_seconds", 15)))
        status = str(getattr(response, "status", "")).lower()
        if status != "accepted":
            raise RuntimeError(f"OCPP SetChargingProfile was rejected: {status or 'unknown'}")
        return {
            "edge_status": "succeeded",
            "protocol": "ocpp",
            "charge_point_id": charge_point_id,
            "limit_kw": float(limit_kw),
        }


def _server_ssl_context(config: dict[str, Any]) -> ssl.SSLContext:
    cert = config.get("server_cert")
    key = config.get("server_key")
    if not cert or not key:
        raise EdgeConfigurationError("OCPP server_cert and server_key are required.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    if not config.get("client_ca"):
        raise EdgeConfigurationError("OCPP client_ca is required for mutual TLS charger authentication.")
    context.load_verify_locations(config["client_ca"])
    context.verify_mode = ssl.CERT_REQUIRED
    return context


@dataclass
class CommandRouter:
    adapters: dict[str, CommandAdapter]
    device_protocols: dict[str, str] = field(default_factory=dict)
    station_protocols: dict[str, str] = field(default_factory=dict)

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        protocol = payload.get("protocol")
        if not protocol and task.get("device_id"):
            protocol = self.device_protocols.get(str(task["device_id"]))
        if not protocol and task.get("station_id"):
            protocol = self.station_protocols.get(str(task["station_id"]))
        if not protocol:
            raise EdgeConfigurationError("No protocol mapping exists for this task.")
        adapter = self.adapters.get(str(protocol))
        if adapter is None:
            raise EdgeConfigurationError(f"Protocol {protocol!r} is not enabled on this gateway.")
        result = adapter.execute(task)
        return {**result, "task_id": task.get("id"), "executed_at": datetime.now(UTC).isoformat()}


def build_command_server(host: str, port: int, token: str, router: CommandRouter) -> ThreadingHTTPServer:
    if len(token) < 24:
        raise EdgeConfigurationError("gateway token must contain at least 24 characters.")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/chargeopt/tasks/execute":
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if self.headers.get("Authorization") != f"Bearer {token}":
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    raise ValueError("invalid request size")
                body = json.loads(self.rfile.read(length))
                task = body.get("task") if isinstance(body, dict) else None
                if not isinstance(task, dict) or not task.get("id"):
                    raise ValueError("task object with id is required")
                result = router.execute(task)
                code = HTTPStatus.ACCEPTED if result.get("edge_status") == "accepted" else HTTPStatus.OK
                self._send(code, result)
            except (EdgeConfigurationError, ValueError) as exc:
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"edge_status": "failed", "error": str(exc)})
            except Exception as exc:
                LOGGER.exception("edge_command_failed")
                self._send(HTTPStatus.BAD_GATEWAY, {"edge_status": "failed", "error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            LOGGER.info("gateway_http", extra={"http_log": format % args})

        def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ThreadingHTTPServer((host, port), Handler)


def load_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text())
    required = {"control_plane_url", "control_plane_api_key", "gateway_token"}
    missing = sorted(key for key in required if not config.get(key))
    if missing:
        raise EdgeConfigurationError(f"Missing edge settings: {', '.join(missing)}")
    return config


def run(config: dict[str, Any]) -> None:
    control_plane = ControlPlaneClient(config["control_plane_url"], config["control_plane_api_key"])
    adapters: dict[str, CommandAdapter] = {}
    background: list[Any] = []
    ocpp: OcppConnector | None = None
    if config.get("mqtt"):
        mqtt = MqttConnector(config["mqtt"], control_plane)
        mqtt.start()
        adapters["mqtt"] = mqtt
        background.append(mqtt)
    if config.get("modbus"):
        modbus = ModbusConnector(config["modbus"], control_plane)
        modbus.start()
        adapters["modbus"] = modbus
        background.append(modbus)
    if config.get("ocpp"):
        ocpp = OcppConnector(config["ocpp"], control_plane)
        adapters["ocpp"] = ocpp

    router = CommandRouter(adapters, config.get("device_protocols", {}), config.get("station_protocols", {}))
    server = build_command_server(
        config.get("gateway_host", "127.0.0.1"), int(config.get("gateway_port", 9100)), config["gateway_token"], router
    )
    server_thread = threading.Thread(target=server.serve_forever, name="chargeopt-command-api", daemon=True)
    server_thread.start()

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    try:
        if ocpp:
            asyncio.run(ocpp.serve(stop))
        else:
            while not stop.wait(1):
                pass
    finally:
        server.shutdown()
        for connector in background:
            connector.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ChargeOpt field edge gateway.")
    parser.add_argument("--config", default=os.environ.get("CHARGEOPT_EDGE_CONFIG", "/etc/chargeopt/edge.json"))
    parser.add_argument("--validate", action="store_true", help="Validate configuration without connecting to devices.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = load_config(args.config)
    if args.validate:
        print(
            json.dumps(
                {"status": "valid", "protocols": [name for name in ("ocpp", "modbus", "mqtt") if config.get(name)]}
            )
        )
        return 0
    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
