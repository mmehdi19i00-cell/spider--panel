"""System router: Xray process control (start/stop/restart/reload/health)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._actions import regenerate_and_reload
from app.core.security import get_current_admin
from app.database import get_db
from app.users.models import AdminUser
from app.xray.process import manager

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/xray/health")
async def health():
    return await manager.health_check()


@router.post("/xray/start")
async def start_xray(_: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    # ensure config exists/valid first
    await regenerate_and_reload(db, reload=False)
    ok = await manager.start()
    return {"ok": ok, "health": await manager.health_check()}


@router.post("/xray/stop")
async def stop_xray(_: AdminUser = Depends(get_current_admin)):
    await manager.stop()
    return {"ok": True}


@router.post("/xray/restart")
async def restart_xray(_: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await regenerate_and_reload(db, reload=False)
    ok = await manager.restart()
    return {"ok": ok, "health": await manager.health_check()}


@router.post("/xray/reload")
async def reload_xray(_: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await regenerate_and_reload(db, reload=False)
    ok = await manager.reload()
    return {"ok": ok, "health": await manager.health_check()}


@router.post("/config/regenerate")
async def regen(_: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    return await regenerate_and_reload(db, reload=True)
