"""Vercel diagnostic — bare minimum ASGI to confirm function runs."""
import sys


async def app(scope, receive, send):
    if scope["type"] == "http":
        body = f"OK sys.path={sys.path}".encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", b"text/plain"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": body})
