"""
Web API Router - authentication, subscription, and session endpoints.

This module performs NO server-side HTML rendering.
The UI is a static single-page app served from main.py (static/index.html).
Only the API endpoints that the static frontend depends on live here.
"""
import asyncio
import hashlib
import secrets

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel

from config import logger, SESSION_COOKIE, SESSION_TTL, AUTH
from core.state import (
    LINKS, LINKS_LOCK, SUBS, SUBS_LOCK,
    USERS, USERS_LOCK,
    INBOUNDS, INBOUNDS_LOCK,
    SESSIONS, SESSIONS_LOCK,
    is_link_allowed,
)
from services.xray_service import (
    generate_vless_link as svc_generate_vless_link,
    ensure_reality_keys,
    RealityIncompleteError,
)

router = APIRouter()

# ── Public Subscription API ──────────────────────────────────────────────────
@router.get("/api/public/sub/{uuid_key}")
async def public_subscription(uuid_key: str, request: Request, pw: str = None):
    async with SUBS_LOCK:
        sub = SUBS.get(uuid_key)
    if not sub:
        raise HTTPException(status_code=404, detail="اشتراک یافت نشد")
    if sub.get("password") and pw != sub.get("password"):
        return HTMLResponse(
            "<html><body><h1>رمز عبور مورد نیاز</h1>"
            "<form method='get'><input name='pw' placeholder='رمز عبور'>"
            "<button type='submit'>ورود</button></form></body></html>"
        )
    return HTMLResponse(
        f"<html><body><h1>اشتراک {uuid_key}</h1>"
        "<p>Welcome to Spider Panel</p></body></html>"
    )


@router.get("/sub-group/{uuid_key}")
async def subscription_group(uuid_key: str, pw: str = None):
    async with SUBS_LOCK:
        sub = SUBS.get(uuid_key)
    if not sub:
        raise HTTPException(status_code=404, detail="اشتراک یافت نشد")
    if sub.get("password") and pw != sub.get("password"):
        raise HTTPException(status_code=401, detail="رمز عبور اشتباه است")
    links = []
    for link_id in sub.get("links", []):
        async with LINKS_LOCK:
            link = LINKS.get(link_id)
        if link and is_link_allowed(link):
            try:
                config = svc_generate_vless_link(
                    uuid=link_id,
                    remark=link.get("label", "Spider"),
                    inbound_id=link.get("inbound_id"),
                    user=link,
                )
                links.append(config)
            except Exception:
                pass
    return Response(content="\n".join(links), media_type="text/plain")


# ── Per-user config / subscription (JSON-safe) ─────────────────────────────
async def _user_config_payload(user_id: str) -> dict:
    """Build a JSON payload with the real VLESS config(s) for a user.

    Never raises uncaught exceptions that become HTTP 500 for validation
    problems — RealityIncompleteError is turned into a clean JSON error.
    """
    async with USERS_LOCK:
        user = USERS.get(user_id)
    if not user:
        return {"success": False, "error": "user not found", "status_code": 404}

    # Collect inbounds this user is allowed to use.
    inbound_ids = user.get("inbound_ids") or list(INBOUNDS.keys())
    configs = []
    for iid in inbound_ids:
        inbound = INBOUNDS.get(iid)
        if not inbound:
            continue
        # Reality inbounds must have real, persisted keys.
        if inbound.get("security") == "reality":
            try:
                await ensure_reality_keys(iid)
            except RealityIncompleteError as e:
                return {
                    "success": False,
                    "error": "Reality inbound is incomplete",
                    "missing": e.missing,
                    "inbound_id": iid,
                }
        try:
            link = svc_generate_vless_link(
                uuid=user.get("config_uuid") or user_id,
                remark=f"spider-{user.get('username', user_id)}",
                inbound_id=iid,
                user=user,
            )
            configs.append({"inbound_id": iid, "link": link})
        except RealityIncompleteError as e:
            return {
                "success": False,
                "error": "Reality inbound is incomplete",
                "missing": e.missing,
                "inbound_id": iid,
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e), "inbound_id": iid}

    return {"success": True, "user": user.get("username", user_id), "configs": configs,
            "config": configs[0]["link"] if configs else ""}


@router.get("/api/users/{user_id}/config")
async def user_config(user_id: str, request: Request):
    payload = await _user_config_payload(user_id)
    status = payload.pop("status_code", 200) if "status_code" in payload else (
        404 if not payload.get("success") and payload.get("error") == "user not found" else 200
    )
    if not payload.get("success"):
        status = payload.get("status_code", 400)
    return JSONResponse(payload, status_code=status)


@router.get("/api/sub/{username}")
async def user_subscription(username: str, request: Request):
    # Resolve username -> user_id
    async with USERS_LOCK:
        user_id = None
        for uid, u in USERS.items():
            if u.get("username") == username:
                user_id = uid
                break
    if not user_id:
        return JSONResponse({"success": False, "error": "user not found"}, status_code=404)
    payload = await _user_config_payload(user_id)
    status = 404 if (not payload.get("success") and payload.get("error") == "user not found") else 200
    if not payload.get("success"):
        status = payload.get("status_code", 400)
    return JSONResponse(payload, status_code=status)


# ── Login / Logout / Session API ─────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str = ""


@router.post("/api/login")
async def api_login(request: Request, body: LoginRequest):
    expected_hash = AUTH.get("password_hash")
    if expected_hash:
        provided_hash = hashlib.sha256(
            f"{body.password}{AUTH.get('secret', 'spider-panel-secret-key-v2')}".encode()
        ).hexdigest()
        if provided_hash != expected_hash:
            return JSONResponse({"detail": "رمز عبور اشتباه است"}, status_code=401)
    token = secrets.token_urlsafe(32)
    async with SESSIONS_LOCK:
        SESSIONS[token] = asyncio.get_event_loop().time() + SESSION_TTL
    response = JSONResponse({"success": True})
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        max_age=SESSION_TTL,
        samesite="lax",
    )
    return response


@router.post("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with SESSIONS_LOCK:
            SESSIONS.pop(token, None)
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/api/me")
async def api_me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with SESSIONS_LOCK:
            exp = SESSIONS.get(token)
            if exp and exp > asyncio.get_event_loop().time():
                return {"authenticated": True}
    return {"authenticated": False}
