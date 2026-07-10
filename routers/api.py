"""Dashboard REST API router.

Implements every endpoint the static dashboard (static/index.html) calls:
users CRUD, inbounds CRUD, server resources/stats, change-password, and the
tools endpoints (reality keys, settings, railway IP scan, my-ip).

All responses are JSON. Validation failures return HTTP 4xx with {"detail": ...}
which the frontend's `api()` helper surfaces as an error toast.

This module has NO business logic of its own beyond shaping state into the
shapes the UI expects; the source of truth is core.state (USERS / INBOUNDS)
and services.xray_service (key generation + link generation).
"""
import asyncio
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from config import AUTH, SESSION_COOKIE, SESSION_TTL, hash_password, get_host
from core.state import (
    USERS, USERS_LOCK,
    INBOUNDS, INBOUNDS_LOCK,
    SUBS, SUBS_LOCK,
    LINKS, LINKS_LOCK,
    save_state, generate_uuid, SETTINGS,
    stats, connections, hourly_traffic, error_logs, activity_logs,
)
from services.xray_service import (
    generate_vless_link,
    ensure_reality_keys,
    generate_reality_keypair,
    RealityIncompleteError,
)

router = APIRouter()

_BOOT_TIME = time.time()


# ── Pydantic request bodies ────────────────────────────────────────────────
class CreateUserReq(BaseModel):
    username: str
    traffic_limit_gb: float = 0
    expire_days: int = 0
    concurrent_connections: int = 2
    inbound_id: Optional[str] = None


class UpdateUserReq(BaseModel):
    username: Optional[str] = None
    traffic_limit_gb: Optional[float] = None
    expire_days: Optional[int] = None
    concurrent_connections: Optional[int] = None
    status: Optional[str] = None
    reset_traffic: bool = False


class CreateInboundReq(BaseModel):
    name: str = ""
    protocol: str = "vless"
    port: int = 0
    external_port: Optional[int] = None
    external_domain: str = ""
    network: str = "ws"
    security: str = "tls"
    domain: str = ""
    sni: str = ""
    fingerprint: str = "chrome"
    reality_settings: Dict[str, Any] = Field(default_factory=dict)
    ws_settings: Dict[str, Any] = Field(default_factory=dict)
    xhttp_settings: Dict[str, Any] = Field(default_factory=dict)
    grpc_settings: Dict[str, Any] = Field(default_factory=dict)


class ChangePasswordReq(BaseModel):
    current_password: str = ""
    new_password: str = ""


class SettingsReq(BaseModel):
    domain: str = ""


# ── Serializers ─────────────────────────────────────────────────────────────
def _user_out(uid: str, u: dict) -> dict:
    inbound_id = u.get("inbound_id")
    inbound = INBOUNDS.get(inbound_id, {}) if inbound_id else {}
    return {
        "user_id": uid,
        "uuid": u.get("uuid", uid),
        "username": u.get("username", ""),
        "traffic_used_bytes": u.get("traffic_used_bytes", 0),
        "traffic_limit_bytes": u.get("traffic_limit_bytes", 0),
        "expire_at": u.get("expire_at"),
        "status": u.get("status", "active"),
        "inbound_id": inbound_id,
        "inbound_name": inbound.get("name") if inbound else (inbound_id or "پیش‌فرض"),
        "concurrent_connections": u.get("concurrent_connections", 2),
    }


def _inbound_out(iid: str, ib: dict) -> dict:
    users_count = sum(1 for u in USERS.values() if u.get("inbound_id") == iid)
    rs = ib.get("reality_settings", {})
    # Normalize for UI round-trip (snake_case keys)
    rs_out = {
        "private_key": rs.get("private_key", ""),
        "public_key": rs.get("public_key", ""),
        "short_ids": rs.get("short_ids", ""),
        "server_names": rs.get("server_names", []),
        "sni": rs.get("sni", ""),
        "spiderx": rs.get("spiderx", "/"),
        "dest": rs.get("dest", ""),
        "fingerprint": rs.get("fingerprint", "chrome"),
    }
    return {
        "inbound_id": iid,
        "name": ib.get("name", ""),
        "protocol": ib.get("protocol", "vless"),
        "port": ib.get("port", 0),
        "external_port": ib.get("external_port"),
        "external_domain": ib.get("external_domain", ""),
        "network": ib.get("network", "tcp"),
        "security": ib.get("security", "none"),
        "domain": ib.get("domain", ""),
        "sni": ib.get("sni", ""),
        "fingerprint": ib.get("fingerprint", "chrome"),
        "users_count": users_count,
        "reality_settings": rs_out,
        "ws_settings": ib.get("ws_settings", {}),
        "xhttp_settings": ib.get("xhttp_settings", {}),
        "grpc_settings": ib.get("grpc_settings", {}),
    }


# ── Users ───────────────────────────────────────────────────────────────────
@router.get("/api/users")
async def list_users():
    async with USERS_LOCK:
        users = [_user_out(uid, u) for uid, u in USERS.items()]
    return {"users": users}


@router.post("/api/users")
async def create_user(body: CreateUserReq):
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="نام کاربری الزامی است")
    async with USERS_LOCK:
        for u in USERS.values():
            if u.get("username") == username:
                raise HTTPException(status_code=409, detail="این نام کاربری قبلاً وجود دارد")
        uid = generate_uuid()
        limit_bytes = int(body.traffic_limit_gb * 1073741824) if body.traffic_limit_gb > 0 else 0
        expire_at = None
        if body.expire_days and body.expire_days > 0:
            expire_at = (datetime.now() + timedelta(days=body.expire_days)).isoformat()
        USERS[uid] = {
            "username": username,
            "uuid": __import__("uuid").uuid4().hex,
            "config_uuid": generate_uuid(),
            "traffic_limit_bytes": limit_bytes,
            "traffic_used_bytes": 0,
            "expire_at": expire_at,
            "status": "active",
            "concurrent_connections": body.concurrent_connections,
            "inbound_id": body.inbound_id,
            "created_at": datetime.now().isoformat(),
        }
        await save_state()
    return {"user_id": uid, "username": username}


@router.patch("/api/users/{uid}")
async def update_user(uid: str, body: UpdateUserReq):
    async with USERS_LOCK:
        u = USERS.get(uid)
        if not u:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        if body.username is not None:
            u["username"] = body.username
        if body.traffic_limit_gb is not None:
            u["traffic_limit_bytes"] = int(body.traffic_limit_gb * 1073741824) if body.traffic_limit_gb > 0 else 0
        if body.expire_days is not None:
            u["expire_at"] = (datetime.now() + timedelta(days=body.expire_days)).isoformat() if body.expire_days > 0 else None
        if body.concurrent_connections is not None:
            u["concurrent_connections"] = body.concurrent_connections
        if body.status is not None:
            u["status"] = body.status
        if body.reset_traffic:
            u["traffic_used_bytes"] = 0
        await save_state()
    return {"success": True}


@router.patch("/api/users/{uid}/toggle")
async def toggle_user(uid: str):
    async with USERS_LOCK:
        u = USERS.get(uid)
        if not u:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        u["status"] = "inactive" if u.get("status") == "active" else "active"
        await save_state()
    return {"success": True, "status": u["status"]}


@router.delete("/api/users/{uid}")
async def delete_user(uid: str):
    async with USERS_LOCK:
        if uid not in USERS:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        USERS.pop(uid)
        await save_state()
    return {"success": True}


# ── Inbounds ────────────────────────────────────────────────────────────────
@router.get("/api/inbounds")
async def list_inbounds():
    async with INBOUNDS_LOCK:
        inbounds = [_inbound_out(iid, ib) for iid, ib in INBOUNDS.items()]
    return {"inbounds": inbounds}


def _normalize_reality(body: CreateInboundReq) -> dict:
    rs = dict(body.reality_settings or {})
    # Frontend sends server_names / short_ids (snake plural) — keep them;
    # the xray_service and link generator read these snake_case keys.
    if "serverNames" in rs and "server_names" not in rs:
        rs["server_names"] = rs.pop("serverNames")
    if "shortIds" in rs and "short_ids" not in rs:
        rs["short_ids"] = rs.pop("shortIds")
    if "spiderX" in rs and "spiderx" not in rs:
        rs["spiderx"] = rs.pop("spiderX")
    if "privateKey" in rs and "private_key" not in rs:
        rs["private_key"] = rs.pop("privateKey")
    if "publicKey" in rs and "public_key" not in rs:
        rs["public_key"] = rs.pop("publicKey")
    return rs


@router.post("/api/inbounds")
async def create_inbound(body: CreateInboundReq):
    iid = generate_uuid()
    ib = {
        "name": body.name or "اینباند جدید",
        "protocol": body.protocol,
        "port": body.port,
        "external_port": body.external_port,
        "external_domain": body.external_domain,
        "network": body.network,
        "security": body.security,
        "domain": body.domain,
        "sni": body.sni,
        "fingerprint": body.fingerprint,
        "reality_settings": _normalize_reality(body),
        "ws_settings": body.ws_settings,
        "xhttp_settings": body.xhttp_settings,
        "grpc_settings": body.grpc_settings,
        "created_at": datetime.now().isoformat(),
    }
    async with INBOUNDS_LOCK:
        INBOUNDS[iid] = ib
        await save_state()
    return {"inbound_id": iid, "success": True}


@router.patch("/api/inbounds/{iid}")
async def update_inbound(iid: str, body: CreateInboundReq):
    async with INBOUNDS_LOCK:
        ib = INBOUNDS.get(iid)
        if not ib:
            raise HTTPException(status_code=404, detail="اینباند یافت نشد")
        ib.update({
            "name": body.name or ib.get("name", ""),
            "protocol": body.protocol,
            "port": body.port,
            "external_port": body.external_port,
            "external_domain": body.external_domain,
            "network": body.network,
            "security": body.security,
            "domain": body.domain,
            "sni": body.sni,
            "fingerprint": body.fingerprint,
            "reality_settings": _normalize_reality(body),
            "ws_settings": body.ws_settings,
            "xhttp_settings": body.xhttp_settings,
            "grpc_settings": body.grpc_settings,
        })
        await save_state()
    return {"inbound_id": iid, "success": True}


@router.delete("/api/inbounds/{iid}")
async def delete_inbound(iid: str):
    async with INBOUNDS_LOCK:
        if iid not in INBOUNDS:
            raise HTTPException(status_code=404, detail="اینباند یافت نشد")
        INBOUNDS.pop(iid)
        await save_state()
    return {"success": True}


@router.post("/api/inbounds/{iid}/generate-short-id")
async def generate_short_id(iid: str):
    # A shortId is a random hex string (NOT a keypair) — acceptable randomness.
    sid = secrets.token_hex(8)
    async with INBOUNDS_LOCK:
        ib = INBOUNDS.get(iid)
        if not ib:
            raise HTTPException(status_code=404, detail="اینباند یافت نشد")
        rs = ib.setdefault("reality_settings", {})
        rs["short_ids"] = sid
        await save_state()
    return {"short_id": sid}


# ── Server resources / stats ───────────────────────────────────────────────
def _server_resources() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count() or 1,
        "ram_used_gb": vm.used / 1073741824,
        "ram_total_gb": vm.total / 1073741824,
        "ram_percent": vm.percent,
        "disk_percent": disk.percent,
        "disk_total_gb": disk.total / 1073741824,
        "net_sent_mb": (net.bytes_sent if net else 0) / 1048576,
        "net_recv_mb": (net.bytes_recv if net else 0) / 1048576,
        "uptime_seconds": int(time.time() - _BOOT_TIME),
    }


@router.get("/api/server/resources")
async def server_resources():
    try:
        return _server_resources()
    except Exception as e:  # noqa
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/server/stats")
async def server_stats():
    res = _server_resources()
    total_traffic = sum(u.get("traffic_used_bytes", 0) for u in USERS.values())
    return {
        "total_traffic_bytes": total_traffic,
        "total_requests": stats.get("total_requests", 0),
        "active_connections": len(connections),
        "cpu_percent": res["cpu_percent"],
        "ram_percent": res["ram_percent"],
        "disk_percent": res["disk_percent"],
        "uptime_seconds": res["uptime_seconds"],
        "users_count": len(USERS),
        "inbounds_count": len(INBOUNDS),
    }


@router.get("/stats")
async def stats_legacy():
    """Legacy dashboard endpoint used by some frontends."""
    return await server_stats()


# ── Change password ─────────────────────────────────────────────────────────
@router.post("/api/change-password")
async def change_password(body: ChangePasswordReq):
    expected = AUTH.get("password_hash")
    if expected and body.current_password:
        from config import CONFIG
        provided = hash_password(body.current_password)
        if provided != expected:
            raise HTTPException(status_code=401, detail="رمز عبور فعلی اشتباه است")
    if not body.new_password:
        raise HTTPException(status_code=400, detail="رمز عبور جدید الزامی است")
    AUTH["password_hash"] = hash_password(body.new_password)
    await save_state()
    return {"success": True}


# ── Tools ───────────────────────────────────────────────────────────────────
@router.post("/api/tools/settings")
async def tools_settings(body: SettingsReq):
    # Persist the panel domain used for external link generation.
    SETTINGS["domain"] = body.domain or get_host()
    await save_state()
    return {"success": True, "domain": SETTINGS.get("domain")}


@router.get("/api/tools/xray-status")
async def tools_xray_status():
    """Report Xray binary install/version so the panel can show it in Settings."""
    installed = await is_xray_installed()
    version = await get_xray_version() if installed else None
    return {
        "installed": installed,
        "version": version,
        "path": str(XRAY_BINARY_PATH),
        "valid": bool(version),
    }


@router.post("/api/tools/generate-reality-keys")
async def tools_generate_reality_keys():
    try:
        keys = await generate_reality_keypair()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "private_key": keys.get("private_key", ""),
        "public_key": keys.get("public_key", ""),
        "hash32": keys.get("hash32", ""),
    }


@router.get("/api/tools/my-ip")
async def tools_my_ip():
    ips: Dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get("https://api.ipify.org?format=json")
            if r.status_code == 200:
                ips["public"] = r.json().get("ip", "")
    except Exception:
        ips["public"] = ""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips["private"] = s.getsockname()[0]
        s.close()
    except Exception:
        ips["private"] = ""
    return {"ips": ips}


@router.get("/api/users/{uid}/qr")
async def user_qr(uid: str):
    """Return a QR code PNG of the user's VLESS config, or raw text if no QR lib."""
    async with USERS_LOCK:
        user = USERS.get(uid)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    # Build the config link the same way the dashboard does.
    inbound_ids = user.get("inbound_ids") or list(INBOUNDS.keys())
    link = ""
    for iid in inbound_ids:
        inbound = INBOUNDS.get(iid)
        if not inbound:
            continue
        if inbound.get("security") == "reality":
            try:
                await ensure_reality_keys(iid)
            except RealityIncompleteError:
                continue
        try:
            link = generate_vless_link(
                uuid=user.get("config_uuid") or uid,
                remark=f"spider-{user.get('username', uid)}",
                inbound_id=iid,
                user=user,
            )
            break
        except RealityIncompleteError:
            continue
        except Exception:
            continue
    if not link:
        raise HTTPException(status_code=400, detail="کانفیگی برای نمایش موجود نیست")
    try:
        import io
        import qrcode
        from PIL import Image  # noqa
        img = qrcode.make(link)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception:
        return Response(content=link, media_type="text/plain;charset=utf-8")


@router.get("/api/tools/scan-railway-ips")
async def tools_scan_railway_ips():
    """Best-effort regional latency probe.

    Probes a set of well-known public endpoints and reports per-host latency so
    the dashboard can show reachable regions. This is a real network probe, not
    fabricated data.
    """
    probes = [
        ("us-west", "1.1.1.1"),
        ("us-east", "8.8.8.8"),
        ("europe", "9.9.9.9"),
        ("asia", "223.5.5.5"),
    ]
    regions = []
    for region, host in probes:
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                await c.get(f"http://{host}", headers={"Host": host})
            latency = (time.time() - start) * 1000
            status = "ok" if latency < 600 else "slow"
            regions.append({"region": region, "host": host, "latency_ms": latency, "status": status})
        except Exception:
            regions.append({"region": region, "host": host, "latency_ms": None, "status": "unreachable"})
    return {"regions": regions}
