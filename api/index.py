from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from chargeopt.analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail, station_summary
from chargeopt.data import load_repository


REPO = load_repository()
ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_file(STATIC / "index.html")
            elif parsed.path.startswith("/static/"):
                self._send_file(STATIC / parsed.path.removeprefix("/static/"))
            elif parsed.path in {"/api", "/api/overview"}:
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
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path.startswith("/static/"):
            path = STATIC / "index.html" if parsed.path == "/" else STATIC / parsed.path.removeprefix("/static/")
            if path.exists() and path.is_file():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                return
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
