"""Vercel entrypoint – auto-detected by Vercel's Python runtime.

Vercel looks for an `app` variable in main.py / app.py / index.py at root.
"""
import traceback

try:
    from chargeopt.app import create_app
    app = create_app()
except Exception as _boot_err:
    _tb = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    app = FastAPI()

    @app.get("/{path:path}")
    async def _boot_error(path: str):
        return JSONResponse({"error": str(_boot_err), "traceback": _tb}, status_code=500)
