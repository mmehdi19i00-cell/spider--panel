"""
Spider Gateway - FastAPI Entry Point
Main application setup, routers, startup/shutdown.
ALL business logic moved to config/, state.py, services/, routers/
"""
import asyncio
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import httpx

# ── Project layout (never hardcode paths) ───────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# ── Import new modules ─────────────────────────────────────────────────────
from config import (
    CONFIG, SETTINGS, IRAN_TZ, get_host, logger,
    SESSION_COOKIE, SESSION_TTL, hash_password, AUTH,
    DATA_DIR, DATA_FILE,
    XRAY_BINARY_PATH,
)
from core.state import (
    # State
    LINKS, LINKS_LOCK, PATH_INDEX, PATH_INDEX_LOCK, SUBS, SUBS_LOCK,
    USERS, USERS_LOCK, INBOUNDS, INBOUNDS_LOCK, GROUPS, GROUPS_LOCK,
    IP_POOL, IP_POOL_LOCK, IP_BLACKLIST, IP_BLACKLIST_LOCK,
    USER_IP_MAP, USER_IP_MAP_LOCK,
    SESSIONS, SESSIONS_LOCK,
    stats, error_logs, activity_logs, hourly_traffic,
    connections,
    # Functions
    load_state, save_state, log_activity,
    _rebuild_path_index, _migrate_user_links,
    generate_uuid, generate_short_id, generate_random_path,
    now_ir, uptime, parse_size_to_bytes,
)
from services.xray_service import (
    generate_vless_link,  # Will need to move this or create a link service
    start_xray, stop_xray, get_xray_status, install_xray_core, is_xray_installed, get_xray_version,
)

# ── Import routers ─────────────────────────────────────────────────────────
# routers/xhttp.py was removed in the flat rewrite; the XHTTP router now lives
# at the package root (xhttp_siz10). It imports from core.state/config, NOT main,
# so there is no circular import.
from xhttp_siz10 import router as xhttp_router
from routers.web import router as web_router
from routers.api import router as api_router

# ── Telegram First-Run Paths ───────────────────────────────────────────────
TELEGRAM_FLAG_FILE = DATA_DIR / "telegram_seen.flag"
TELEGRAM_LINK_FILE = BASE_DIR / "data" / "link.txt"

# ── FastAPI App ────────────────────────────────────────────────────────────
app = FastAPI(title="Spider Gateway", docs_url=None, redoc_url=None)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include routers
app.include_router(xhttp_router)
app.include_router(web_router)
app.include_router(api_router)

# ── HTTP Client ────────────────────────────────────────────────────────────
http_client: httpx.AsyncClient | None = None

# ── Startup / Shutdown ─────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global http_client, stats
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
    timeout = httpx.Timeout(30.0, connect=10.0)
    http_client = httpx.AsyncClient(limits=limits, timeout=timeout, follow_redirects=True)

    stats["start_time"] = asyncio.get_event_loop().time()

    await load_state()

    # CRITICAL: Validate Xray binary exists and works before starting service
    if not await is_xray_installed():
        error_msg = f"Xray Core binary not found at {XRAY_BINARY_PATH}. Build failed: Xray installation missing."
        logger.critical(error_msg)
        raise RuntimeError(error_msg)

    version = await get_xray_version()
    if not version:
        error_msg = f"Xray binary at {XRAY_BINARY_PATH} is not executable or corrupted."
        logger.critical(error_msg)
        raise RuntimeError(error_msg)

    logger.info(f"Xray Core validated: version {version} at {XRAY_BINARY_PATH}")

    # Auto-create default inbound if none exist
    async with INBOUNDS_LOCK:
        if not INBOUNDS:
            INBOUNDS["default"] = {
                "name": "VLESS+WS پیش‌فرض",
                "protocol": "vless",
                "port": 443,
                "network": "ws",
                "security": "tls",
                "domain": SETTINGS.get("domain", get_host()),
                "sni": "",
                "external_port": 443,
                "fingerprint": "chrome",
                "reality_settings": {},
                "xhttp_settings": {},
                "created_at": datetime.now().isoformat(),
            }
            await save_state()
            log_activity("inbound", "اینباند پیش‌فرض VLESS+WS ساخته شد", "ok")

    # Ensure every Reality inbound has REAL, persisted keys (idempotent).
    # This generates keypairs via `xray x25519` on first run and reuses the
    # persisted ones on restart — never regenerates. Raises a clear error if
    # an inbound is missing its sni (operator must configure the TLS target).
    from services.xray_service import ensure_reality_keys, RealityIncompleteError
    async with INBOUNDS_LOCK:
        reality_inbounds = [iid for iid, ib in INBOUNDS.items()
                            if ib.get("security") == "reality"]
    for iid in reality_inbounds:
        try:
            await ensure_reality_keys(iid)
        except RealityIncompleteError as e:
            error_msg = (
                f"Reality inbound '{iid}' is incomplete (missing {', '.join(e.missing)}). "
                f"Configure sni/serverNames before starting."
            )
            logger.critical(error_msg)
            raise RuntimeError(error_msg)
        except Exception as e:
            logger.critical(f"Failed to prepare Reality inbound '{iid}': {e}")
            raise

    # Start Xray with the generated config (validates + writes + launches).
    # Without this the inbound is never actually listening, so every link
    # fails to connect even when the link itself is correct.
    xray_result = await start_xray()
    if not xray_result.get("ok"):
        error_msg = f"Xray failed to start: {xray_result.get('error')}"
        logger.critical(error_msg)
        raise RuntimeError(error_msg)
    logger.info(f"Xray started (pid {xray_result.get('pid')})")

    log_activity("system", "سرور راه‌اندازی شد", "ok")
    logger.info(f"Spider Gateway v9.2 started on port {CONFIG['port']}")


@app.on_event("shutdown")
async def shutdown():
    await save_state()
    if http_client:
        await http_client.aclose()

# ── Static UI routes ───────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/sub")
async def sub():
    return FileResponse(str(STATIC_DIR / "sub.html"))

# ── Helpers ────────────────────────────────────────────────────────────────
def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "نامشخص"

# ── Auth ───────────────────────────────────────────────────────────────────
async def create_session() -> str:
    token = secrets.token_urlsafe(32)
    async with SESSIONS_LOCK:
        SESSIONS[token] = asyncio.get_event_loop().time() + SESSION_TTL
    return token

async def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    async with SESSIONS_LOCK:
        exp = SESSIONS.get(token)
        if exp is None:
            return False
        if exp < asyncio.get_event_loop().time():
            SESSIONS.pop(token, None)
            return False
        return True

async def destroy_session(token: str | None):
    if not token:
        return
    async with SESSIONS_LOCK:
        SESSIONS.pop(token, None)

async def require_auth(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not await is_valid_session(token):
        raise HTTPException(status_code=401, detail="unauthorized")
    return token

# ── Basic endpoints ────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "connections": len(connections), "uptime": uptime()}

# ── Telegram First-Run API ────────────────────────────────────────────────
@app.get("/api/telegram/status")
async def telegram_status():
    """Check if user has seen the Telegram popup."""
    seen = TELEGRAM_FLAG_FILE.exists()
    if seen:
        return {"seen": True}
    # Read URL from data/link.txt
    url = "https://t.me/SpiderPanel"
    if TELEGRAM_LINK_FILE.exists():
        try:
            url = TELEGRAM_LINK_FILE.read_text().strip()
        except Exception:
            pass
    return {"seen": False, "url": url}


@app.post("/api/telegram/seen")
async def telegram_seen():
    """Mark Telegram popup as seen."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TELEGRAM_FLAG_FILE.touch()
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to create telegram_seen.flag: {e}")
        raise HTTPException(status_code=500, detail="Failed to save flag")

# ── Include more routers as we create them ─────────────────────────────────
# TODO: Add routers for users, inbounds, links, subs, etc.

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=CONFIG["port"], reload=False)
