"""Vercel entrypoint – auto-detected by Vercel's Python runtime.

Vercel looks for an `app` variable in main.py / app.py / index.py at root.
"""
from chargeopt.app import create_app

app = create_app()
