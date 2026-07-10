"""Inbound service — create / edit / delete inbounds and regenerate config.

Inbounds are the canonical transport+security definitions. Everything the
Xray builder and subscription builder need lives on the Inbound row, so this
is the single place Reality/TLS/xhttp params are managed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.users.models import Inbound
from app.xray.reality import ensure_keypair


async def list_inbounds(db: AsyncSession) -> list[Inbound]:
    res = await db.execute(select(Inbound).order_by(Inbound.id))
    return list(res.scalars().all())


async def get_inbound(db: AsyncSession, inbound_id: int) -> Inbound | None:
    return await db.get(Inbound, inbound_id)


async def create_inbound(
    db: AsyncSession,
    *,
    tag: str,
    name: str,
    port: int,
    sec: str = "reality",
    network: str = "xhttp",
    server_name: str = "",
    spider_x: str = "/",
    transport_path: str = "/",
    ws_host: str = "",
    xhttp_mode: str = "auto",
    xhttp_x_padding_bytes: str = "100-1000",
    xhttp_sc_max_each_post_bytes: str = "1000000-2000000",
    xhttp_sc_max_concurrent_posts: int = 100,
    xhttp_extra: str = "",
    cert_path: str = "",
    key_path: str = "",
    alpn: str = "h2,http/1.1",
    uuid: str | None = None,
) -> Inbound:
    res = await db.execute(select(Inbound).where(Inbound.tag == tag))
    if res.scalar_one_or_none():
        raise ValueError(f"inbound tag '{tag}' already exists")

    priv, pub = "", ""
    short_id = ""
    if sec == "reality":
        priv, pub = ensure_keypair(None, None)
        short_id = security_generate_short_id()

    ib = Inbound(
        tag=tag,
        name=name,
        protocol="vless",
        port=port,
        security=sec,
        network=network,
        uuid=uuid or security.generate_uuid(),
        private_key=priv,
        public_key=pub,
        short_id=short_id,
        server_name=server_name,
        spider_x=spider_x,
        cert_path=cert_path,
        key_path=key_path,
        alpn=alpn,
        transport_path=transport_path,
        ws_host=ws_host,
        xhttp_mode=xhttp_mode,
        xhttp_x_padding_bytes=xhttp_x_padding_bytes,
        xhttp_sc_max_each_post_bytes=xhttp_sc_max_each_post_bytes,
        xhttp_sc_max_concurrent_posts=xhttp_sc_max_concurrent_posts,
        xhttp_extra=xhttp_extra,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(ib)
    await db.commit()
    await db.refresh(ib)
    return ib


def security_generate_short_id() -> str:
    return security.generate_short_id(8)


async def update_inbound(db: AsyncSession, ib: Inbound, **fields: Any) -> Inbound:
    # If security switched to reality and keys missing, generate.
    if fields.get("security") == "reality" and not ib.private_key:
        priv, pub = ensure_keypair(None, None)
        ib.private_key = priv
        ib.public_key = pub
        ib.short_id = security_generate_short_id()
    allowed = {
        "name", "port", "security", "network", "server_name", "spider_x",
        "cert_path", "key_path", "alpn", "transport_path", "ws_host",
        "xhttp_mode", "xhttp_x_padding_bytes", "xhttp_sc_max_each_post_bytes",
        "xhttp_sc_max_concurrent_posts", "xhttp_extra", "enabled", "uuid",
    }
    for k, v in fields.items():
        if k in allowed and v is not None:
            setattr(ib, k, v)
    await db.commit()
    await db.refresh(ib)
    return ib


async def regenerate_reality_keys(db: AsyncSession, ib: Inbound) -> Inbound:
    priv, pub = ensure_keypair(None, None)
    ib.private_key = priv
    ib.public_key = pub
    ib.short_id = security_generate_short_id()
    await db.commit()
    await db.refresh(ib)
    return ib


async def delete_inbound(db: AsyncSession, ib: Inbound) -> None:
    await db.delete(ib)
    await db.commit()
