"""Vercel Python serverless entrypoint.

Vercel expects an ASGI callable named ``app``.  We create a fresh app
instance here; the lifespan (DB pool init/close) does not fire in
serverless — the app automatically falls back to in-memory fixtures
when DATABASE_URL is not set.
"""
from chargeopt.app import create_app

app = create_app()
