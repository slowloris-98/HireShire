"""Launch the HireShire dashboard (Phase 5 frontend).

    python run_web.py            # serve API + built SPA on the configured host/port
    python run_web.py --reload   # dev: auto-reload the backend

The React dev server (frontend/, `npm run dev`) proxies /api here during
development; in production `npm run build` emits frontend/dist which this app
serves as static files.
"""
from __future__ import annotations

import argparse

import uvicorn

from hireshire.webapp.config import load_frontend_config


def main() -> None:
    cfg = load_frontend_config()
    parser = argparse.ArgumentParser(description="HireShire dashboard server")
    parser.add_argument("--host", default=cfg.server.host)
    parser.add_argument("--port", type=int, default=cfg.server.port)
    parser.add_argument("--reload", action="store_true", help="auto-reload (dev)")
    args = parser.parse_args()

    uvicorn.run(
        "hireshire.webapp.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
