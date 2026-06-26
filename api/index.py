"""Vercel Python serverless entrypoint.

Vercel expects either a BaseHTTPRequestHandler subclass named ``handler``
or an ASGI/WSGI callable.  We expose the FastAPI ``app`` object directly;
Vercel's Python runtime detects ASGI apps automatically when the symbol is
named ``app``.
"""
from chargeopt.app import app  # noqa: F401  – re-exported for Vercel
