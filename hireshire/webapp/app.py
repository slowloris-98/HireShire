"""FastAPI application factory for the HireShire dashboard."""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from hireshire.webapp.deps import get_settings

load_dotenv()
from hireshire.webapp.routers import config_api, chat, data, runs

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="HireShire Dashboard", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(data.router)
    app.include_router(config_api.router)
    app.include_router(runs.router)
    app.include_router(chat.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    # In production the built SPA is served from the same origin.
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="spa")

    return app


app = create_app()
