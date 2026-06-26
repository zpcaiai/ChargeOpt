"""Vercel Python serverless entrypoint.

All requests are routed here via vercel.json catch-all.
Static files are served by FastAPI's StaticFiles mount.
"""
import os

from fastapi.staticfiles import StaticFiles

from chargeopt.app import create_app

app = create_app()

_static = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_static):
    app.mount("/static", StaticFiles(directory=_static), name="static")
