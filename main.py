"""Vercel entrypoint – auto-detected by Vercel's Python runtime.

FastAPI serves everything: /api/* routes + static files + root HTML.
"""
import os
import traceback

try:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from chargeopt.app import create_app

    app = create_app()

    # Serve static assets (styles.css, app.js) at /static/*
    _static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    # Serve index.html at root
    @app.get("/", include_in_schema=False)
    async def _root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

except Exception as _boot_err:
    _tb = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    app = FastAPI()

    @app.get("/{path:path}")
    async def _boot_error(path: str):
        return JSONResponse({"error": str(_boot_err), "traceback": _tb}, status_code=500)
