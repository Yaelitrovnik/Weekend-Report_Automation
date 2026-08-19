from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    routes_evidence,
    routes_health,
    routes_notes,
    routes_reports,
    routes_review,
    routes_runs,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Weekend Report Automation")
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(routes_health.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_review.router)
    app.include_router(routes_notes.router)
    app.include_router(routes_evidence.router)
    app.include_router(routes_reports.router)
    return app


app = create_app()
