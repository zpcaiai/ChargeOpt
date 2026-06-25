from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail, station_summary
from .data import load_repository


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
REPO = load_repository()


class ChargeOptHandler(BaseHTTPRequestHandler):
    server_version = "ChargeOptHTTP/0.1"

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path.startswith("/static/"):
            path = STATIC / "index.html" if parsed.path == "/" else STATIC / parsed.path.removeprefix("/static/")
            if not path.exists() or not path.is_file():
                self.send_response(HTTPStatus.NOT_FOUND)
            else:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_file(STATIC / "index.html")
            elif parsed.path.startswith("/static/"):
                self._send_file(STATIC / parsed.path.removeprefix("/static/"))
            elif parsed.path == "/api/overview":
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
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
        except KeyError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:  # pragma: no cover - surfaced to browser during local development.
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), ChargeOptHandler)
    print(f"ChargeOpt OS running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
