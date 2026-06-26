"""Vercel Python serverless entrypoint (built via @vercel/python).

All requests route here via vercel.json. FastAPI serves the API routes,
static assets under /static, and the root index.html.
"""
import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_boot_tb: str | None = None


async def app(scope, receive, send):  # replaced below on success; fallback if boot fails
    body = (_boot_tb or "boot not attempted").encode()
    await send({"type": "http.response.start", "status": 500,
                "headers": [[b"content-type", b"text/plain"],
                             [b"content-length", str(len(body)).encode()]]})
    await send({"type": "http.response.body", "body": body})


try:
    from chargeopt.app import create_app

    app = create_app(use_lifespan=False)  # type: ignore[misc]

except Exception:
    _boot_tb = traceback.format_exc()
