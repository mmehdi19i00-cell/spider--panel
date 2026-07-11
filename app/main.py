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

from app.api import (
    auth,
    dashboard,
    domains,
    inbounds,
    news,
    qr,
    settings as settings_router,
    subscription,
    system,
    users,
    xray_logs,
)
from app import pages
from app.bootstrap import (
    ensure_admin,
    ensure_default_domain,
    ensure_default_inbound,
)
from app.core.config import settings
from app.core.logging import log
from app.database import dispose_engine, get_sessionmaker, init_db
from app.domains import manager as domain_manager
from app.xray.builder import write_config
from app.xray.process import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI ALWAYS binds the Railway-injected PORT. Never share it with Xray.
    log.info(f"Spider Panel started port: {settings.panel_port}")
    # 1. schema
    await init_db()
    # 2. first-run data
    async with get_sessionmaker()() as db:
        await ensure_admin(db)
        await ensure_default_inbound(db)
        await ensure_default_domain(db)

        # Resolve active domain + reality state for the startup banner.
        active_domain = None
        dom = await domain_manager.get_active(db)
        if dom:
            active_domain = dom.domain
        from app.xray.builder import build_config
        cfg = await build_config(db)
        reality_enabled = any(
            (ib.get("streamSettings", {}).get("security") == "reality")
            for ib in cfg.get("inbounds", [])
        )

        # 3. initial config
        await write_config(db)
        log.info("Config written:")
        for ib in cfg.get("inbounds", []):
            ss = ib.get("streamSettings", {})
            if ss.get("security") == "reality":
                log.info(f"Xray inbound: 0.0.0.0:{settings.xray_inbound_port}")

        # 4. start xray (best effort; fails loud in logs if config invalid)
        started = await manager.start()
        manager.print_startup_banner(
            active_domain=active_domain,
            reality_enabled=reality_enabled,
        )
        if started:
            log.info("Xray started successfully.")
        else:
            log.error("Xray did NOT start — see validation errors above.")
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

# CSRF defense-in-depth: for state-changing requests (POST/PUT/DELETE/PATCH)
# issued by a logged-in browser session (session cookie present) OR carrying
# the SPA marker header, a valid-format `X-CSRF-Token` is required. Plain API /
# test clients that omit both the session cookie and the SPA header are
# unaffected. This stops cross-site form/JS from issuing mutations.
from starlette.middleware.base import BaseHTTPMiddleware

class CSRFTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            path = request.url.path
            # Credential-exchange endpoints establish the session, so they are
            # CSRF-exempt (no session exists yet to bind a token to).
            if path.startswith("/api/auth/"):
                return await call_next(request)
            has_session = bool(request.cookies.get("spider_session"))
            is_spa = request.headers.get("X-Requested-With") == "SpiderSPA"
            if has_session or is_spa:
                token = request.headers.get("X-CSRF-Token", "")
                if not (len(token) == 48 and all(c in "0123456789abcdef" for c in token)):
                    from fastapi import Response

                    return Response("CSRF token invalid", status_code=403)
        return await call_next(request)
app.add_middleware(CSRFTokenMiddleware)

# API routers
for r in (auth, users, dashboard, inbounds, domains, qr, subscription, system, settings_router, news, xray_logs):
    app.include_router(r.router)

# Page (multi-page) router — separate templates, auth-guarded.
app.include_router(pages.router)


# Static frontend
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok", "service": "spider-panel"}


# Mount static dir for assets (css/js/img)
if os.path.isdir(_STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="assets")

# Mount music dir (audio files played when the panel opens)
_MUSICS_DIR = os.path.join(_STATIC_DIR, "musics")
if os.path.isdir(_MUSICS_DIR):
    app.mount("/musics", StaticFiles(directory=_MUSICS_DIR), name="musics")


@app.get("/{full_path:path}")
async def fallback_404(full_path: str):
    # Don't hijack /api or /assets or /musics (handled above).
    from fastapi import HTTPException

    if full_path.startswith(("api/", "assets/", "musics/", "sub/")):
        raise HTTPException(status_code=404, detail="Not found")
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>404</title></head><body style='background:#0a0a0c;color:#fff;"
        "font-family:system-ui;text-align:center;padding:40px'>"
        "<h1>404 — Not found</h1>"
        "<a href='/dashboard' style='color:#ff2d55'>Go to dashboard</a></body></html>",
        status_code=404,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
