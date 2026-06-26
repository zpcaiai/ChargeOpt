"""Vercel Python serverless entrypoint (built via @vercel/python)."""
import os
import sys
import traceback

# Ensure project root is on sys.path so `chargeopt` package is importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_BOOT_ERROR: str | None = None

# Unconditional top-level assignment so @vercel/python builder detects `app`.
# Will be replaced by the real FastAPI app if boot succeeds.
async def app(scope, receive, send):  # type: ignore[misc]  # noqa: E302
    pass

try:
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    from chargeopt.app import create_app

    app = create_app()

    _static_dir = os.path.join(_ROOT, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def _root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

except Exception:
    _BOOT_ERROR = traceback.format_exc()

    # Minimal stdlib ASGI app — no fastapi dependency
    async def app(scope, receive, send):  # type: ignore[misc]
        if scope["type"] == "http":
            body = (_BOOT_ERROR or "unknown boot error").encode()
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"content-length", str(len(body)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})
