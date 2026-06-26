"""Vercel Python serverless entrypoint.

Vercel auto-detects this file as a Python function entrypoint.
The `app` variable is the FastAPI ASGI application.
Static files in /static are served by Vercel's CDN automatically.
"""
import os
import sys

# Ensure project root is on sys.path so `chargeopt` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chargeopt.app import create_app  # noqa: E402

app = create_app()
