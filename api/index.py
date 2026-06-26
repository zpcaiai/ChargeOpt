"""Vercel Python serverless entrypoint (built via @vercel/python).

All requests route here (see vercel.json). FastAPI serves the API routes,
the static assets under /static, and the root index.html.
"""
import os
import sys
import traceback

# Ensure project root is on sys.path so `chargeopt` package is importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from chargeopt.app import create_app

    app = create_app()

    _static_dir = os.path.join(_ROOT, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def _root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

except Exception as _boot_err:  # surface boot errors instead of silent 500
    _tb = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.get("/{path:path}")
    async def _boot_error(path: str):
        return JSONResponse({"error": str(_boot_err), "traceback": _tb}, status_code=500)
