"""Vercel Python serverless entrypoint.

Vercel auto-detects this file as a Python function entrypoint.
The `app` variable is the FastAPI ASGI application.
Static files in /static are served by Vercel's CDN automatically.
"""
from chargeopt.app import create_app

app = create_app()
