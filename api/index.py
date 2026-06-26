"""Vercel Python serverless entrypoint (built via @vercel/python).

All requests route here (see vercel.json). FastAPI serves the API routes,
the static assets under /static, and the root index.html.

NOTE: a top-level `app` (FastAPI) is created FIRST so Vercel's
@vercel/python builder can statically detect the entrypoint. We then try
to replace it with the real application; any boot failure is surfaced as
a JSON 500 with the full traceback instead of an opaque crash.
"""
import os
import sys
import traceback

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

# Ensure project root is on sys.path so `chargeopt` package is importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Top-level fallback app — guarantees the builder finds `app`.
app = FastAPI()

try:
    from fastapi.staticfiles import StaticFiles

    from chargeopt.app import create_app

    app = create_app()

    _static_dir = os.path.join(_ROOT, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def _root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

except Exception as _boot_err:
    _tb = traceback.format_exc()

    @app.get("/{path:path}")
    async def _boot_error(path: str):
        return JSONResponse(
            {"error": str(_boot_err), "traceback": _tb},
            status_code=500,
        )
