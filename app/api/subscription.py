"""Subscription endpoints.

GET /sub/{uuid}            -> newline-separated vless URIs (base64 metadata link)
GET /sub/{uuid}?format=json -> JSON with uris + user info
GET /sub/{uuid} (browser)  -> the public neon subscription UI (sub.html)
GET /sub/{uuid}/ping       -> real TCP reachability check of the config (public)

Every URI is validated before being returned; an invalid URI is never served.
The public UI (sub.html) is served to browsers (Accept: text/html) so a user can
open "domain/sub/<uuid>" directly and see usage, expiry, connect steps, and a
live ping — without touching the admin panel.
"""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.subscriptions import builder as sub_builder
from app.subscriptions.validator import assert_valid_subscription
from app.users.models import User

router = APIRouter(tags=["subscription"])

_SUB_HTML = Path(__file__).resolve().parent.parent / "static" / "sub.html"


def _to_base64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


async def _load_user(db: AsyncSession, uuid: str) -> User:
    res = await db.execute(select(User).where(User.uuid == uuid))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return user


@router.get("/sub/{uuid}")
async def subscription(
    uuid: str,
    request: Request,
    format: str = Query("text", description="text|json"),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, uuid)
    uris = await sub_builder.build_subscription(db, user)
    # NEVER return broken configs.
    try:
        assert_valid_subscription(uris)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=f"Subscription validation failed: {e}")

    # Browsers get the public UI (so "domain/sub/<uuid>" opens the dashboard).
    accept = request.headers.get("accept", "")
    wants_ui = ("text/html" in accept) and (format != "json")
    if wants_ui and _SUB_HTML.is_file():
        return FileResponse(str(_SUB_HTML))

    if format == "json":
        return {
            "username": user.username,
            "uuid": user.uuid,
            "status": user.status,
            "enabled": user.enabled,
            "expire_at": user.expire_at,
            "traffic_limit_bytes": user.traffic_limit_bytes,
            "used_traffic_bytes": user.used_traffic_bytes,
            "uris": uris,
        }

    # text format: subscription-info header compatible
    body = "\n".join(uris) + ("\n" if uris else "")
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Subscription-Userinfo": (
            f"upload=0;download={user.used_traffic_bytes};"
            f"total={user.traffic_limit_bytes};expire="
            f"{int(user.expire_at.timestamp()) if user.expire_at else 0}"
        ),
        "Profile-Title": quote(f"Spider Panel | {user.username}"),
    }
    return PlainTextResponse(content=body, headers=headers)


@router.get("/sub/{uuid}/ping")
async def ping(uuid: str, db: AsyncSession = Depends(get_db)):
    """Real reachability check: TCP-connect to the config host:port from the server.

    Returns latency in ms. This is a genuine socket probe of the public endpoint
    the client will use (domain:external_port), so it reflects whether the config
    actually answers — not a fake value.
    """
    user = await _load_user(db, uuid)
    uris = await sub_builder.build_subscription(db, user)
    if not uris:
        return {"ok": False, "error": "no enabled inbounds for this user"}
    first = uris[0]
    parsed = urlparse(first)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return {"ok": False, "error": "could not parse host/port from config"}
    try:
        t0 = time.monotonic()
        with socket.create_connection((host, port), timeout=3.0):
            ms = round((time.monotonic() - t0) * 1000, 1)
        return {"ok": True, "ms": ms, "host": host, "port": port}
    except OSError as e:
        return {"ok": False, "host": host, "port": port, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        return {"ok": False, "host": host, "port": port, "error": str(e)}
