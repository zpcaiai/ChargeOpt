"""Vercel Python serverless entrypoint (built via @vercel/python).

All requests route here via vercel.json. FastAPI serves the API routes,
static assets under /static, and the root index.html.
"""
import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_boot_tb: str | None = None


async def app(scope, receive, send):  # replaced below on success; fallback if boot fails
    body = (_boot_tb or "boot not attempted").encode()
    await send({"type": "http.response.start", "status": 500,
                "headers": [[b"content-type", b"text/plain"],
                             [b"content-length", str(len(body)).encode()]]})
    await send({"type": "http.response.body", "body": body})


try:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from chargeopt.app import create_app

    app = create_app()  # type: ignore[misc]

    _static_dir = os.path.join(_ROOT, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def _root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

except Exception:
    _boot_tb = traceback.format_exc()
