"""Subscription builder.

Reads the SAME Inbound rows the Xray builder reads, so every generated
VLESS URI matches the running server config exactly. This is the
single-source-of-truth guarantee: nothing is hand-built from scattered vars.

Default Reality+XHTTP format produced for every enabled inbound:
  vless://{uuid}@{domain}:{external_port}
    ?encryption=none&security=reality&sni={sni}&fp=chrome
    &pbk={PUBLIC_KEY}&sid={SHORT_ID}&spx=%2F
    &type=xhttp&path=%2F&mode=auto
    &extra={urlencoded json}
    #{username}

The domain is the inbound's own assigned domain (or the active domain), the
port is the externally-reachable port (Railway TCP proxy / external_port),
and pbk/sid come straight from the Reality keys stored on the Inbound.
"""
from __future__ import annotations

import json
from urllib.parse import quote, urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domains import manager as domain_manager
from app.users.models import Domain, Inbound, User


async def _resolve_domain(db: AsyncSession, inbound: Inbound) -> str:
    """Domain for an inbound: its own assigned domain, else active, else default.

    The active domain comes from Domain Management (the operator-set active
    domain). This is the host clients connect to — it is NEVER the internal
    Xray port or the FastAPI web port.
    """
    if inbound.domain:
        return inbound.domain
    dom = await domain_manager.get_active(db)
    if dom:
        return dom.domain
    return settings.public_host or "localhost"


def _default_extra() -> str:
    """URL-safe JSON for the xhttp `extra` param (matches the spec link).

    Decoded shape: {"xPaddingBytes":"100-1000","mode":"auto","scMaxEachPostBytes":"1000000"}
    """
    payload = {
        "xPaddingBytes": "100-1000",
        "mode": "auto",
        "scMaxEachPostBytes": "1000000",
    }
    return json.dumps(payload, separators=(",", ":"))


def _build_vless(user: User, inbound: Inbound, sni: str, remark: str) -> str:
    host = sni
    # Client connects to the EXTERNALLY reachable port — the Railway TCP proxy
    # port (RAILWAY_TCP_PROXY_PORT). This is NEVER the internal Xray listen
    # port (24567) and NEVER the FastAPI web PORT. `external_port` is an
    # optional per-inbound override; otherwise we use the public (TCP proxy) port.
    port = inbound.external_port or settings.public_port

    params: dict[str, str] = {
        "encryption": "none",
        "type": inbound.network,  # xhttp | ws | tcp
        "security": inbound.security,  # reality | tls | none
    }

    if inbound.network == "xhttp":
        params["path"] = inbound.transport_path or "/"
        params["mode"] = inbound.xhttp_mode or "auto"
    elif inbound.network == "ws":
        params["path"] = inbound.transport_path or "/"
        if inbound.ws_host:
            params["host"] = inbound.ws_host

    if inbound.security == "reality":
        # sni = the fronting target shown to clients (default Apple mzstatic)
        sni_for_client = inbound.server_name or "is1-ssl.mzstatic.com"
        params["sni"] = sni_for_client
        params["fp"] = "chrome"
        params["pbk"] = inbound.public_key or ""
        params["sid"] = inbound.short_id or ""
        params["spx"] = inbound.spider_x or "/"
        # xhttp reality: force the canonical path/mode + the xhttp extra block.
        # The extra carries xPaddingBytes / mode / scMaxEachPostBytes exactly
        # as the client needs them.
        if inbound.network == "xhttp":
            params["path"] = "/"
            params["mode"] = "auto"
            params["extra"] = _default_extra()
    elif inbound.security == "tls":
        params["sni"] = sni

    query = urlencode(params, safe="")
    return (
        f"vless://{user.uuid}@{host}:{port}"
        f"?{query}#" + quote(remark, safe="")
    )


async def build_subscription(db: AsyncSession, user: User) -> list[str]:
    """Return list of vless URIs for the user's enabled inbounds."""
    res = await db.execute(select(Inbound).where(Inbound.enabled.is_(True)))
    inbounds = res.scalars().all()

    tags = set(user.inbound_tags)
    uris: list[str] = []
    for ib in inbounds:
        if tags and ib.tag not in tags:
            continue
        domain = await _resolve_domain(db, ib)
        remark = f"{user.username} | {ib.tag} | {ib.network}/{ib.security}"
        uris.append(_build_vless(user, ib, domain, remark))
    return uris


async def build_subscription_text(db: AsyncSession, user: User) -> str:
    uris = await build_subscription(db, user)
    return "\n".join(uris) + ("\n" if uris else "")


def validate_uris(uris: list[str], validator) -> list[str]:
    """Return only valid URIs using app.xray.validator.validate_vless_uri."""
    out = []
    for u in uris:
        ok, _errs = validator(u)
        if ok:
            out.append(u)
    return out
