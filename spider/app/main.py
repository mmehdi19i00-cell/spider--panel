"""FastAPI application entrypoint.

Lifespan: create tables, ensure admin + default inbound + domain, write the
initial Xray config, then start Xray. Serves a static SPA (red neon spider
dashboard) and a JSON API.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, dashboard, domains, inbounds, subscription, system, users
from app.bootstrap import (
    ensure_admin,
    ensure_default_domain,
    ensure_default_inbound,
)
from app.core.config import settings
from app.core.logging import log
from app.database import dispose_engine, get_sessionmaker, init_db
from app.xray.builder import write_config
from app.xray.process import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Spider Panel starting", extra={"payload": {"port": settings.PORT, "db": settings.db_url.split("://")[0]}})
    # 1. schema
    await init_db()
    # 2. first-run data
    async with get_sessionmaker()() as db:
        await ensure_admin(db)
        await ensure_default_inbound(db)
        await ensure_default_domain(db)
        # 3. initial config
        await write_config(db)
        # 4. start xray (best effort; fails loud in logs if config invalid)
        started = await manager.start()
        log.info(f"xray auto-start: {'ok' if started else 'skipped/failed'}")
    yield
    # shutdown: stop xray, reap child, close pool
    await manager.stop()
    await dispose_engine()
    log.info("Spider Panel stopped")


app = FastAPI(
    title="Spider Panel",
    version="1.0.0",
    description="Red Neon Xray management panel",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
for r in (auth, users, dashboard, inbounds, domains, subscription, system):
    app.include_router(r.router)


# Static frontend
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok", "service": "spider-panel"}


@app.get("/")
async def index():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # Don't hijack /api or /sub
    if full_path.startswith("api/") or full_path.startswith("sub/"):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
    file_path = os.path.join(_STATIC_DIR, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


# Mount static dir for assets (css/js/img)
if os.path.isdir(_STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
