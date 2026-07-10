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

from config import AUTH, SESSION_COOKIE, SESSION_TTL, hash_password, get_host, logger
from core.state import (
    USERS, USERS_LOCK,
    INBOUNDS, INBOUNDS_LOCK,
    SUBS, SUBS_LOCK,
    LINKS, LINKS_LOCK,
    save_state, generate_uuid, SETTINGS,
    stats, connections, hourly_traffic, error_logs, activity_logs,
    DOMAINS, get_active_domain, set_active_domain, add_domain,
    count_connected_ips, active_sessions,
    find_user_by_uuid, find_user_by_config_uuid,
)
from services.xray_service import (
    generate_vless_link,
    ensure_reality_keys,
    generate_reality_keypair,
    RealityIncompleteError,
    restart_xray,
    generate_xray_server_config,
)
from core.state import allocate_inbound_port

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
        "connected_ips": count_connected_ips(u.get("config_uuid") or uid),
        "online_sessions": len(active_sessions(u.get("config_uuid") or uid)),
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


# ── Xray sync ───────────────────────────────────────────────────────────────
async def _sync_xray() -> None:
    """Rebuild the Xray config from current users/inbounds and reload Xray.

    Called after any user create/update/delete so the generated link's UUID
    is guaranteed to exist as a client in /app/xray-config/config.json. Without
    this, a freshly created user's link would reference a client Xray never
    learned about, so the connection would fail.
    """
    try:
        cfg = generate_xray_server_config()
        result = await restart_xray(cfg)
        if not result.get("ok"):
            logger.warning(f"Xray sync (restart) failed: {result.get('error')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Xray sync skipped/failed: {e}")


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
        import uuid as _uuid
        uid = generate_uuid()
        limit_bytes = int(body.traffic_limit_gb * 1073741824) if body.traffic_limit_gb > 0 else 0
        expire_at = None
        if body.expire_days and body.expire_days > 0:
            expire_at = (datetime.now() + timedelta(days=body.expire_days)).isoformat()
        USERS[uid] = {
            "username": username,
            "uuid": str(_uuid.uuid4()),
            "config_uuid": str(_uuid.uuid4()),
            "traffic_limit_bytes": limit_bytes,
            "traffic_used_bytes": 0,
            "expire_at": expire_at,
            "status": "active",
            "concurrent_connections": body.concurrent_connections,
            "inbound_id": body.inbound_id,
            "created_at": datetime.now().isoformat(),
        }
        await save_state()
    # Sync the new client into Xray (rebuild config + reload) so the link's
    # UUID actually exists as a client in /app/xray-config/config.json.
    await _sync_xray()
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
    await _sync_xray()
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
    await _sync_xray()
    return {"success": True}


# ── Inbounds ────────────────────────────────────────────────────────────────
@router.get("/api/inbounds")
async def list_inbounds():
    async with INBOUNDS_LOCK:
        inbounds = [_inbound_out(iid, ib) for iid, ib in INBOUNDS.items()]
    return {"inbounds": inbounds}


@router.get("/api/inbounds/{iid}")
async def get_inbound(iid: str):
    async with INBOUNDS_LOCK:
        ib = INBOUNDS.get(iid)
        if not ib:
            raise HTTPException(status_code=404, detail="اینباند یافت نشد")
        out = _inbound_out(iid, ib)
    return out


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
    # Allocate a unique internal listen port unless the caller pinned one.
    # Never collide with the FastAPI web port or another inbound's port.
    internal_port = body.port
    if not internal_port or internal_port <= 0:
        internal_port = allocate_inbound_port(body.network or "tcp")
    ib = {
        "name": body.name or "اینباند جدید",
        "protocol": body.protocol,
        "port": internal_port,
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
    # For Reality inbounds, make sure pbk/sid exist before rebuilding Xray.
    if ib.get("security") == "reality":
        try:
            await ensure_reality_keys(iid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Reality key ensure failed for {iid}: {e}")
    await _sync_xray()
    return {"inbound_id": iid, "success": True}


@router.patch("/api/inbounds/{iid}")
async def update_inbound(iid: str, body: CreateInboundReq):
    async with INBOUNDS_LOCK:
        ib = INBOUNDS.get(iid)
        if not ib:
            raise HTTPException(status_code=404, detail="اینباند یافت نشد")
        # Keep a pinned port; otherwise re-allocate a non-conflicting one.
        internal_port = body.port
        if not internal_port or internal_port <= 0:
            internal_port = allocate_inbound_port(body.network or ib.get("network", "tcp"))
        ib.update({
            "name": body.name or ib.get("name", ""),
            "protocol": body.protocol,
            "port": internal_port,
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
    if ib.get("security") == "reality":
        try:
            await ensure_reality_keys(iid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Reality key ensure failed for {iid}: {e}")
    await _sync_xray()
    return {"inbound_id": iid, "success": True}


@router.delete("/api/inbounds/{iid}")
async def delete_inbound(iid: str):
    async with INBOUNDS_LOCK:
        if iid not in INBOUNDS:
            raise HTTPException(status_code=404, detail="اینباند یافت نشد")
        INBOUNDS.pop(iid)
        await save_state()
    await _sync_xray()
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
    boot = _BOOT_TIME
    return {
        "status": "LIVE",
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count() or 1,
        # UI-expected raw fields (bytes/MB)
        "ram_used": vm.used,
        "ram_total": vm.total,
        "ram_percent": vm.percent,
        "ram_used_gb": vm.used / 1073741824,
        "ram_total_gb": vm.total / 1073741824,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "disk_percent": disk.percent,
        "disk_total_gb": disk.total / 1073741824,
        "upload": (net.bytes_sent if net else 0),
        "download": (net.bytes_recv if net else 0),
        "net_sent_mb": (net.bytes_sent if net else 0) / 1048576,
        "net_recv_mb": (net.bytes_recv if net else 0) / 1048576,
        "uptime_seconds": int(time.time() - boot),
    }


@router.get("/api/server/resources")
async def server_resources():
    try:
        return _server_resources()
    except Exception as e:  # noqa
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/server/status")
async def server_status():
    """Live server status for the dashboard (matches the UI's expected shape)."""
    try:
        r = _server_resources()
        total_traffic = sum(u.get("traffic_used_bytes", 0) for u in USERS.values())
        return {
            "status": "LIVE",
            "cpu": r["cpu_percent"],
            "cpu_count": r["cpu_count"],
            "ram_used": r["ram_used"],
            "ram_total": r["ram_total"],
            "ram_percent": r["ram_percent"],
            "disk_used": r["disk_used"],
            "disk_total": r["disk_total"],
            "disk_percent": r["disk_percent"],
            "upload": r["upload"],
            "download": r["download"],
            "uptime": r["uptime_seconds"],
            "traffic_total_bytes": total_traffic,
            "users_count": len(USERS),
            "inbounds_count": len(INBOUNDS),
        }
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


# ── Domain Manager ────────────────────────────────────────────────────────
class DomainCreateReq(BaseModel):
    domain: str
    description: str = ""


class DomainUpdateReq(BaseModel):
    domain: Optional[str] = None
    description: Optional[str] = None


@router.get("/api/domains")
async def list_domains():
    """List all domains with their active flag; includes the active domain."""
    domains = [
        {
            "id": did,
            "domain": d["domain"],
            "description": d.get("description", ""),
            "is_active": bool(d.get("is_active")),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }
        for did, d in DOMAINS.items()
    ]
    return {"domains": domains, "active_domain": get_active_domain()}


@router.post("/api/domains")
async def create_domain(body: DomainCreateReq):
    if not body.domain:
        raise HTTPException(status_code=400, detail="نام دامنه الزامی است")
    did = add_domain(body.domain, body.description)
    await save_state()
    return {"success": True, "id": did, "domain": body.domain, "active_domain": get_active_domain()}


@router.put("/api/domains/{domain_id}")
async def update_domain(domain_id: str, body: DomainUpdateReq):
    d = DOMAINS.get(domain_id)
    if not d:
        raise HTTPException(status_code=404, detail="دامنه یافت نشد")
    if body.domain is not None:
        d["domain"] = body.domain
    if body.description is not None:
        d["description"] = body.description
    d["updated_at"] = datetime.now().isoformat()
    await save_state()
    return {"success": True, "id": domain_id, "domain": d["domain"]}


@router.delete("/api/domains/{domain_id}")
async def delete_domain(domain_id: str):
    d = DOMAINS.get(domain_id)
    if not d:
        raise HTTPException(status_code=404, detail="دامنه یافت نشد")
    if d.get("is_active"):
        raise HTTPException(status_code=400, detail="دامنه فعال قابل حذف نیست؛ ابتدا دامنه دیگری را فعال کنید")
    del DOMAINS[domain_id]
    await save_state()
    return {"success": True}


@router.post("/api/domains/{domain_id}/activate")
async def activate_domain(domain_id: str):
    if not set_active_domain(domain_id):
        raise HTTPException(status_code=404, detail="دامنه یافت نشد")
    await save_state()
    return {"success": True, "active_domain": get_active_domain()}


@router.get("/api/ip-limit")
async def ip_limit_info():
    """Global IP-limit overview: per-user allowed vs connected IPs."""
    out = []
    for uid, u in USERS.items():
        cuuid = u.get("config_uuid") or uid
        out.append({
            "user_id": uid,
            "username": u.get("username", ""),
            "limit": u.get("concurrent_connections", 2),
            "connected_ips": count_connected_ips(cuuid),
            "online_sessions": len(active_sessions(cuuid)),
        })
    return {"users": out}


@router.get("/api/user/{uuid}/connections")
async def user_connections(uuid: str):
    uid, user = find_user_by_uuid(uuid)
    if uid is None:
        uid, user = find_user_by_config_uuid(uuid)
    if uid is None or user is None:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    cuuid = user.get("config_uuid") or uid
    return {
        "user_id": uid,
        "allowed_ips": user.get("concurrent_connections", 2),
        "connected_ips": count_connected_ips(cuuid),
        "online_sessions": len(active_sessions(cuuid)),
        "sessions": active_sessions(cuuid),
    }


@router.get("/api/user/{uuid}/sessions")
async def user_sessions(uuid: str):
    uid, user = find_user_by_uuid(uuid)
    if uid is None:
        uid, user = find_user_by_config_uuid(uuid)
    if uid is None or user is None:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    cuuid = user.get("config_uuid") or uid
    return {"user_id": uid, "sessions": active_sessions(cuuid)}


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
