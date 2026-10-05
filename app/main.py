"""FastAPI application: REST API under /api/v1, interactive docs at /docs, the app at /."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import v1_admin, v1_core, v1_engines
from .config import get_settings
from .db import SessionLocal, init_db
from .services.jobs import run_due
from .services.seed import seed_demo

log = logging.getLogger("profitpilot")

DESCRIPTION = """
**ProfitPilot**: a pricing co-pilot for the new-to-online Meesho seller (Meesho DICE Challenge S3, Business Track).

It computes a return-adjusted floor, proposes a dual price, re-decides the price at every lifecycle trigger, diagnoses
low orders before any discount, searches prices safely (one menu per day for every buyer), and explains every
suggestion with a Why: what, why, ₹ effect, confidence and undo.

All numbers are **illustrative** (simulations and planning defaults), not real Meesho data.
Start with `GET /api/v1/bootstrap`, `GET /api/v1/recommendations` and `GET /api/v1/products/kurti/lifecycle`.
"""


async def _jobs_loop(interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as db:
                run_due(db)
        except Exception:  # keep the loop alive; errors are logged
            log.exception("scheduled checks failed")


def create_app() -> FastAPI:
    s = get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        if s.seed_demo:
            with SessionLocal() as db:
                seed_demo(db)
        task = asyncio.create_task(_jobs_loop(s.jobs_interval_sec)) if s.jobs_interval_sec > 0 else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="ProfitPilot API", version=__version__, description=DESCRIPTION, lifespan=lifespan,
                  docs_url="/docs", redoc_url="/redoc", openapi_url="/api/v1/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"], allow_headers=["*"])
    for r in (v1_core.router, v1_engines.router, v1_admin.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/api", include_in_schema=False)
    def api_index():
        return JSONResponse({"service": "profitpilot", "version": __version__, "docs": "/docs", "health": "/api/v1/health"})

    fe = Path(s.frontend_dir)
    if s.serve_frontend and (fe / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(fe), html=True), name="frontend")
    return app


app = create_app()
