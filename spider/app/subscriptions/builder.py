"""Subscription builder.

Reads the SAME Inbound rows the Xray builder reads, so every generated
VLESS URI matches the running server config exactly. This is the
single-source-of-truth guarantee: nothing is hand-built from scattered vars.

Subscription endpoints:  /sub/{uuid}  and  /sub/{uuid}?format=clash (raw list)
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.users.models import Domain, Inbound, User


async def _active_domain(db: AsyncSession) -> str | None:
    res = await db.execute(select(Domain).where(Domain.is_active.is_(True)))
    dom = res.scalar_one_or_none()
    if dom:
        return dom.domain
    return settings.public_host or None


def _build_vless(user: User, inbound: Inbound, sni: str, remark: str) -> str:
    host = sni
    port = settings.public_port

    params: dict[str, str] = {
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
        params["pbk"] = inbound.public_key or ""
        params["sid"] = inbound.short_id or ""
        params["sni"] = sni
        params["fp"] = "chrome"
        # spiderX
        if inbound.spider_x:
            params["spx"] = inbound.spider_x
    elif inbound.security == "tls":
        params["sni"] = sni

    query = urlencode(params, safe="")
    return (
        f"vless://{user.uuid}@{host}:{port}"
        f"?{query}#" + quote(remark, safe="")
    )


async def build_subscription(db: AsyncSession, user: User) -> list[str]:
    """Return list of vless URIs for the user's enabled inbounds."""
    sni = await _active_domain(db)
    if not sni:
        sni = "localhost"

    res = await db.execute(select(Inbound).where(Inbound.enabled.is_(True)))
    inbounds = res.scalars().all()

    tags = set(user.inbound_tags)
    uris: list[str] = []
    for ib in inbounds:
        if tags and ib.tag not in tags:
            continue
        remark = f"{user.username} | {ib.tag} | {ib.network}/{ib.security}"
        uris.append(_build_vless(user, ib, sni, remark))
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
