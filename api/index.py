"""Vercel diagnostic — import test."""
import sys
import traceback

_error = None
try:
    import fastapi  # noqa: F401
    from chargeopt.app import create_app
    _app = create_app()
    _msg = f"OK fastapi={fastapi.__version__} routes={[getattr(r,'path','?') for r in _app.routes[:5]]}"
except Exception:
    _error = traceback.format_exc()
    _msg = f"ERROR:\n{_error}"


async def app(scope, receive, send):
    if scope["type"] == "http":
        body = _msg.encode()
        await send({
            "type": "http.response.start",
            "status": 200 if _error is None else 500,
            "headers": [
                [b"content-type", b"text/plain"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": body})
