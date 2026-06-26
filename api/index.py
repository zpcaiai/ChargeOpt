"""Vercel Python serverless entrypoint (built via @vercel/python).

All requests route here (see vercel.json). FastAPI serves the API routes,
the static assets under /static, and the root index.html.

NOTE: `app` MUST be assigned at module top level (not inside try/except)
so Vercel's @vercel/python builder can statically detect the entrypoint.
"""
import os
import sys

# Ensure project root is on sys.path so `chargeopt` package is importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from chargeopt.app import create_app  # noqa: E402

app = create_app()

_static_dir = os.path.join(_ROOT, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def _root():
    return FileResponse(os.path.join(_static_dir, "index.html"))
