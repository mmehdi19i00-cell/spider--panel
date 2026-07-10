"""Domain manager. Multiple domains, exactly one active. The active domain
is consumed by the Xray builder (Reality SNI / TLS) and the subscription
builder (link host).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Domain


async def list_domains(db: AsyncSession) -> list[Domain]:
    res = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
    return list(res.scalars().all())


async def add_domain(db: AsyncSession, domain: str, note: str = "") -> Domain:
    res = await db.execute(select(Domain).where(Domain.domain == domain))
    if res.scalar_one_or_none():
        raise ValueError(f"domain '{domain}' already exists")
    dom = Domain(domain=domain, note=note)
    db.add(dom)
    await db.commit()
    await db.refresh(dom)
    return dom


async def remove_domain(db: AsyncSession, domain: str) -> bool:
    res = await db.execute(select(Domain).where(Domain.domain == domain))
    dom = res.scalar_one_or_none()
    if not dom:
        return False
    # Prevent removing the last active domain silently — allow but warn.
    await db.delete(dom)
    await db.commit()
    return True


async def set_active(db: AsyncSession, domain: str) -> Domain:
    res = await db.execute(select(Domain).where(Domain.domain == domain))
    dom = res.scalar_one_or_none()
    if not dom:
        raise ValueError(f"domain '{domain}' not found")
    # Clear all active flags, then set this one.
    all_res = await db.execute(select(Domain))
    for d in all_res.scalars().all():
        d.is_active = False
    dom.is_active = True
    await db.commit()
    await db.refresh(dom)
    return dom


async def get_active(db: AsyncSession) -> Domain | None:
    res = await db.execute(select(Domain).where(Domain.is_active.is_(True)))
    return res.scalar_one_or_none()
