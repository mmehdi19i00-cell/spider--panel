"""Domains router. Changing the active domain regenerates the Xray config
(Reality SNI / TLS / links all depend on it)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._actions import regenerate_and_reload
from app.core.security import get_current_admin
from app.database import get_db
from app.domains import manager as domain_manager
from app.schemas import DomainCreate, DomainOut
from app.users.models import AdminUser

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=list[DomainOut])
async def list_domains(db: AsyncSession = Depends(get_db)):
    return await domain_manager.list_domains(db)


@router.post("", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
async def add_domain(payload: DomainCreate, db: AsyncSession = Depends(get_db)):
    try:
        d = await domain_manager.add_domain(db, payload.domain, note=payload.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # First domain becomes active automatically.
    active = await domain_manager.get_active(db)
    if not active:
        await domain_manager.set_active(db, d.domain)
    return d


@router.delete("/{domain}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_domain(domain: str, db: AsyncSession = Depends(get_db)):
    ok = await domain_manager.remove_domain(db, domain)
    if not ok:
        raise HTTPException(404, "Domain not found")
    # If we removed the active domain, promote another.
    if not await domain_manager.get_active(db):
        domains = await domain_manager.list_domains(db)
        if domains:
            await domain_manager.set_active(db, domains[0].domain)


@router.post("/{domain}/activate")
async def activate(domain: str, db: AsyncSession = Depends(get_db)):
    try:
        d = await domain_manager.set_active(db, domain)
    except ValueError as e:
        raise HTTPException(404, detail=str(e))
    # SNI changed -> regenerate config + reload.
    await regenerate_and_reload(db)
    return {"ok": True, "active": d.domain}
