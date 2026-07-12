"""FastAPI application entrypoint.

Lifespan: create tables, ensure admin + default inbound + domain, write the
initial Xray config, then start Xray. Serves Jinja2 templates and a JSON API.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.api import (
    auth,
    dashboard,
    domains,
    inbounds,
    news,
    qr,
    remote,
    settings as settings_router,
    subscription,
    system,
    users,
    xray_logs,
    remote,
)
from app.bootstrap import (
    ensure_admin,
    ensure_default_domain,
    ensure_default_inbound,
)
from app.core.auth_middleware import AuthMiddleware
from app.core.config import settings
from app.core.logging import log
from app.database import dispose_engine, get_db, get_sessionmaker, init_db
from app.domains import manager as domain_manager
from app.schemas import TokenRequest
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

# Session middleware for cookie-based auth
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY or "insecure-dev-secret-change-me",
    https_only=settings.is_railway,  # True in production (Railway), False for dev
    same_site="lax",
    max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
)

# Custom auth middleware for cookie + bearer token validation
app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# CSRF defense-in-depth: state-changing requests (POST/PUT/DELETE/PATCH) that
# carry an X-Requested-With: SpiderSPA header must also carry a valid-format
# X-CSRF-Token. Plain API/test clients that omit both headers are unaffected.
# This stops cross-site form/JS from issuing mutations without the SPA token.
class CSRFTokenMiddleware:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if request.headers.get("X-Requested-With") == "SpiderSPA":
                token = request.headers.get("X-CSRF-Token", "")
                if not (len(token) == 32 and all(c in "0123456789abcdef" for c in token)):
                    response = JSONResponse("CSRF token invalid", status_code=403)
                    await response(scope, receive, send)
                    return
        await app(scope, receive, send)


# Jinja2 templates
templates = Jinja2Templates(directory="app/templates")

# API routers
for r in (auth, dashboard, domains, inbounds, news, qr, remote, settings_router, subscription, system, users, xray_logs):
    app.include_router(r.router)


# Template routes - protected pages
@app.get("/", response_class=HTMLResponse)
async def root_redirect(request: Request):
    """Root path -> dashboard if authenticated, else login."""
    token = request.cookies.get("spider_token")
    if token:
        from app.core.security import decode_access_token

        payload = decode_access_token(token)
        if payload and "sub" in payload:
            return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page - only accessible when not authenticated."""
    # Check if already authenticated via session
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=302)
    # Also check cookie
    token = request.cookies.get("spider_token")
    if token:
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={})


# JSON login alias at /api/login (matches the frontend's expected contract).
# Delegates to the auth router's shared handler so behavior stays identical.
@app.post("/api/login")
async def api_login_alias(response: Response, payload: TokenRequest, db: AsyncSession = Depends(get_db)):
    from app.api.auth import _do_login

    return await _do_login(response, payload.username, payload.password, db)


@app.get("/logout")
async def logout_page(request: Request, response: Response):
    """Logout - clear session and redirect to login."""
    # Clear session
    request.session.clear()
    # Clear cookie
    from app.core.auth_middleware import clear_auth_cookie
    clear_auth_cookie(response)
    return RedirectResponse(url="/login", status_code=302)


async def require_auth(request: Request) -> str:
    """Require authentication, redirect to login if not authenticated."""
    # Check session first
    user = request.session.get("user")
    if user:
        return user

    # Check cookie
    token = request.cookies.get("spider_token")
    if token:
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            request.session["user"] = payload["sub"]
            return payload["sub"]

    # Not authenticated
    # For API requests, return 401
    if request.url.path.startswith("/api/"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    # For web requests, redirect to login
    raise HTTPException(status_code=302, detail="Redirect to login", headers={"Location": "/login"})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, user: str = Depends(require_auth)):
    """Dashboard page - requires authentication."""
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"user": user})


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user: str = Depends(require_auth)):
    """Users page - requires authentication."""
    return templates.TemplateResponse(request=request, name="users.html", context={"user": user})


@app.get("/inbounds", response_class=HTMLResponse)
async def inbounds_page(request: Request, user: str = Depends(require_auth)):
    """Inbounds page - requires authentication."""
    return templates.TemplateResponse(request=request, name="inbounds.html", context={"user": user})


@app.get("/domains", response_class=HTMLResponse)
async def domains_page(request: Request, user: str = Depends(require_auth)):
    """Domains page - requires authentication."""
    return templates.TemplateResponse(request=request, name="domains.html", context={"user": user})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: str = Depends(require_auth)):
    """Settings page - requires authentication."""
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user})


@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, user: str = Depends(require_auth)):
    """System page - requires authentication."""
    return templates.TemplateResponse(request=request, name="system.html", context={"user": user})


@app.get("/xray", response_class=HTMLResponse)
async def xray_logs_page(request: Request, user: str = Depends(require_auth)):
    """Xray logs page - requires authentication."""
    return templates.TemplateResponse(request=request, name="xray.html", context={"user": user})


@app.get("/sub", response_class=HTMLResponse)
async def sub_landing(request: Request):
    """Public subscription UI landing page (enter a UUID, or open /sub/<uuid>)."""
    # This is a public page, no auth required
    return templates.TemplateResponse(request=request, name="sub.html", context={})


@app.get("/app", response_class=HTMLResponse)
async def app_shell(request: Request, user: str = Depends(require_auth)):
    """Single-page console shell: fixed sidebar + sections, no reload.

    Reuses the existing JSON APIs and the shared api()/toast() globals from
    base.html. Auth is enforced by require_auth (redirects to /login).
    """
    return templates.TemplateResponse(
        request=request, name="app_shell.html", context={"user": user}
    )

# Health check
@app.get("/api/healthz")
async def healthz():
    return {"status": "ok", "service": "spider-panel"}


# Static files
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Mount static dir for assets (css/js/img)
if os.path.isdir(_STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="assets")

# Mount music dir (audio files played when the panel opens)
_MUSICS_DIR = os.path.join(_STATIC_DIR, "musics")
if os.path.isdir(_MUSICS_DIR):
    app.mount("/musics", StaticFiles(directory=_MUSICS_DIR), name="musics")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )