"""Bootstrap helpers: first-run admin, default inbound, default domain."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_uuid, hash_password, verify_reality_keypair
from app.domains import manager as domain_manager
from app.inbounds import service as ib_service
from app.users.models import AdminUser, Domain, Inbound


async def ensure_admin(db: AsyncSession) -> None:
    res = await db.execute(select(AdminUser))
    if res.scalars().first() is not None:
        return
    admin = AdminUser(
        username=settings.ADMIN_USERNAME,
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        email=settings.ADMIN_EMAIL or "",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    # ADMIN_PASSWORD may have been auto-generated for dev; log it once.
    if not settings.ADMIN_PASSWORD or settings.ADMIN_PASSWORD == "insecure-dev-secret-change-me":
        pass  # real deployments set ADMIN_PASSWORD via env


async def ensure_default_inbound(db: AsyncSession) -> None:
    res = await db.execute(select(Inbound))
    if res.scalars().first() is not None:
        return
    # Create a default VLESS Reality + XHTTP inbound on the internal Xray port
    # (never 443 inside Railway). The client-facing port is the external/TCP
    # proxy port handled by the subscription builder.
    try:
        await ib_service.create_inbound(
            db,
            tag="vless-reality-xhttp",
            name="VLESS Reality (XHTTP)",
            port=settings.xray_inbound_port,
            sec="reality",
            network="xhttp",
            server_name=settings.public_host or "is1-ssl.mzstatic.com",
            spider_x="/",
            transport_path="/",
            xhttp_mode="auto",
        )
    except ValueError:
        # already exists / port conflict — ignore, operator can fix via UI
        pass


async def ensure_default_domain(db: AsyncSession) -> None:
    res = await db.execute(select(Domain))
    if res.scalars().first() is not None:
        return
    if settings.public_host and settings.public_host not in ("127.0.0.1", "localhost"):
        await domain_manager.add_domain(db, settings.public_host, note="auto (Railway proxy)")
        await domain_manager.set_active(db, settings.public_host)
