"""Entry point: python -m chargeopt"""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "chargeopt.app:app",
        host=s.host,
        port=s.port,
        workers=s.workers,
        log_level=s.log_level,
        access_log=False,
        reload=s.debug,
    )


if __name__ == "__main__":
    main()
