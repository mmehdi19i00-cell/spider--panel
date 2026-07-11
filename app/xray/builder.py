"""Xray config.json builder — the ONLY place server config is generated.

All values are read from the database (Inbound rows + active users). The
subscription builder later reads the SAME Inbound fields, guaranteeing that
client URIs always match the running server config.

Pipeline:
    Database -> Xray Builder -> config.json -> (xray loads it)
                                       -> Subscription Builder reads Inbound rows too
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log, log_config_event
from app.users.models import Inbound, User, Domain
from app.xray.reality import ensure_keypair
from app.xray.transports import build_stream_settings


def _build_tls(inbound: Inbound, sni_domain: str) -> dict[str, Any]:
    if inbound.security == "tls":
        tls: dict[str, Any] = {
            "certificates": [
                {
                    "certificateFile": inbound.cert_path or "/etc/xray/cert.pem",
                    "keyFile": inbound.key_path or "/etc/xray/key.pem",
                }
            ]
        }
        if inbound.alpn:
            tls["alpn"] = [a.strip() for a in inbound.alpn.split(",") if a.strip()]
        return tls
    # reality
    pk, pubk = ensure_keypair(inbound.private_key, inbound.public_key)
    # SNI/ServerName shown to clients + the Reality "dest" (the fronting site).
    # Default target is Apple's mzstatic SNI unless an operator sets server_name.
    sni = inbound.server_name or "is1-ssl.mzstatic.com"
    return {
        "realitySettings": {
            "show": False,
            "dest": f"{sni_domain}:443",
            "xver": 0,
            "serverNames": [sni_domain],
            "privateKey": pk,
            "minClientVer": "",
            "maxClientVer": "",
            "maxTimeDiff": 0,
            "shortIds": [inbound.short_id or ""],
            "spiderX": inbound.spider_x or "/",
        }
    }


def _build_client(user: User) -> dict[str, Any]:
    return {
        "id": user.uuid,
        "level": 0,
        "email": f"{user.username}@{user.uuid[:8]}",
        "flow": "xtls-rprx-vision",
    }


async def build_config(db: AsyncSession) -> dict[str, Any]:
    """Assemble the full Xray config from DB state."""
    # Active domain drives SNI fallback for inbounds without their own domain.
    active_domain = await _active_domain(db)

    result = await db.execute(select(Inbound).where(Inbound.enabled.is_(True)))
    inbounds = result.scalars().all()

    # Collect all users who should be clients (active, not expired, enabled).
    uresult = await db.execute(select(User))
    users = uresult.scalars().all()
    active_users = [u for u in users if u.is_active]

    inbounds_cfg: list[dict[str, Any]] = []
    for ib in inbounds:
        # Per-inbound domain wins; else the global active domain; else localhost.
        # (This is the hostname clients connect to — handled by the subscription
        # builder for the client URI host.)
        host = ib.domain or active_domain or "localhost"
        # Reality SNI / fronting target: the inbound's server_name, else the
        # Apple mzstatic default (editable per inbound via `server_name`).
        sni = ib.server_name or "is1-ssl.mzstatic.com"
        stream = build_stream_settings(ib)
        # attach TLS/Reality to streamSettings
        if ib.security in ("tls", "reality"):
            stream["security"] = ib.security
            for k, v in _build_tls(ib, sni).items():
                stream[k] = v
        # Filter clients to those allowed on this inbound
        clients = []
        for u in active_users:
            tags = u.inbound_tags
            if (not tags) or (ib.tag in tags):
                clients.append(_build_client(u))

        inbounds_cfg.append(
            {
                # Bind an INTERNAL port (never 443 inside Railway). The client
                # connects via the external/TCP-proxy port (see subscription
                # builder), so the listen port here is purely server-side.
                "listen": "0.0.0.0",
                "port": ib.port or settings.xray_inbound_port,
                "protocol": ib.protocol,  # vless
                "settings": {
                    "clients": clients,
                    "decryption": "none",
                    "fallbacks": [],
                },
                "streamSettings": stream,
                "tag": ib.tag,
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": False,
                },
            }
        )

    config = {
        "log": {"loglevel": "warning", "access": "", "error": ""},
        "dns": {
            "servers": [
                "https+local://1.1.1.1/dns-query",
                "1.1.1.1",
                "8.8.8.8",
                "localhost",
            ]
        },
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "block"},
                {"type": "field", "domain": ["geosite:category-ads"], "outboundTag": "block"},
            ],
        },
        "inbounds": inbounds_cfg,
        "outbounds": [
            {"protocol": "freedom", "tag": "direct", "settings": {}},
            {
                "protocol": "blackhole",
                "tag": "block",
                "settings": {"response": {"type": "http"}},
            },
        ],
        "policy": {
            "levels": {"0": {"handshake": 4, "connIdle": 300}},
        },
    }
    return config


async def _active_domain(db: AsyncSession) -> str | None:
    res = await db.execute(select(Domain).where(Domain.is_active.is_(True)))
    dom = res.scalar_one_or_none()
    return dom.domain if dom else None


async def write_config(db: AsyncSession) -> str:
    """Build and persist config.json. Returns the path written."""
    config = await build_config(db)
    path = Path(settings.xray_config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    # Audit log — every value that matters for a working link.
    for ib in config["inbounds"]:
        ss = ib.get("streamSettings", {})
        sec = ss.get("security", "none")
        rec = {
            "inbound_tag": ib["tag"],
            "protocol": ib["protocol"],
            "transport": ss.get("network", "tcp"),
            "security": sec,
            "internal_port": ib["port"],
            "public_port": settings.public_port,
            "domain": settings.public_host,
        }
        if sec == "reality" and ss.get("realitySettings"):
            rs = ss["realitySettings"]
            rec["reality_status"] = "enabled"
            rec["public_key"] = rs.get("privateKey", "")[:0] + "...(private)"
        log_config_event("config.written", **rec)
    log.info(f"Config written to {path} with {len(config['inbounds'])} inbound(s)")
    return str(path)


def validate_config_on_disk(path: str | None = None) -> tuple[bool, str]:
    """Run `xray -test -config ...` to validate. Returns (ok, message)."""
    path = path or settings.xray_config_path
    binary = settings.XRAY_BINARY_PATH
    if not shutil.which(binary) and not Path(binary).exists():
        return False, f"xray binary not found at {binary}"
    import subprocess

    try:
        proc = subprocess.run(
            [binary, "-test", "-config", path],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0:
            return True, (proc.stdout or "ok").strip()
        return False, (proc.stderr or proc.stdout or "validation failed").strip()
    except subprocess.SubprocessError as e:
        return False, str(e)
