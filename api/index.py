from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from chargeopt.analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail, station_summary
from chargeopt.data import load_repository


REPO = load_repository()


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/api", "/api/overview"}:
                self._send_json(build_overview(REPO))
            elif parsed.path == "/api/stations":
                self._send_json({"stations": [station_summary(REPO, station) for station in REPO.stations]})
            elif parsed.path.startswith("/api/stations/"):
                station_id = parsed.path.rsplit("/", 1)[-1]
                self._send_json(station_detail(REPO, station_id))
            elif parsed.path == "/api/dispatch":
                self._send_json(build_dispatch(REPO))
            elif parsed.path == "/api/vpp":
                self._send_json(build_vpp(REPO))
            elif parsed.path == "/api/roi":
                params = parse_qs(parsed.query)
                capacity = float(params.get("capacity_kwh", ["1200"])[0])
                power = float(params.get("power_kw", ["600"])[0])
                capex = float(params.get("capex_per_kwh", ["1150"])[0])
                include_vpp = params.get("vpp", ["true"])[0].lower() in {"1", "true", "yes"}
                self._send_json(simulate_roi(REPO, capacity, power, capex, include_vpp))
            elif parsed.path == "/api/audit":
                self._send_json(
                    {
                        "audit": [
                            entry.__dict__ | {"timestamp": entry.timestamp.isoformat(timespec="seconds")}
                            for entry in REPO.audit
                        ]
                    }
                )
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
