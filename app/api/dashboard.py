"""Dashboard router: aggregate stats for the main panel view."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.schemas import DashboardStats
from app.users import service
from app.users.models import AdminUser, User, UserSession
from app.xray.process import manager

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _sys_metrics() -> tuple[float | None, float | None]:
    try:
        import psutil

        return psutil.cpu_percent(interval=0.3), psutil.virtual_memory().percent
    except Exception:
        return None, None


def _storage_stats() -> dict:
    try:
        import shutil

        from app.core.config import settings

        total, used, free = shutil.disk_usage(settings.data_dir)
        return {"total_bytes": total, "used_bytes": used, "free_bytes": free}
    except Exception:
        return {"total_bytes": 0, "used_bytes": 0, "free_bytes": 0}


@router.get("/stats", response_model=DashboardStats)
async def stats(_: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    us = await service.user_stats(db)
    online = await db.scalar(select(func.count()).select_from(UserSession))
    total_traffic = await db.scalar(
        select(func.coalesce(func.sum(User.used_traffic_bytes), 0))
    )
    health = await manager.health_check()
    cpu, mem = _sys_metrics()
    # storage: data dir size
    storage = _storage_stats()
    last = manager.last_result()
    return DashboardStats(
        total_users=us["total"],
        active_users=us["active"],
        expired_users=us["expired"],
        disabled_users=us["disabled"],
        online_connections=int(online or 0),
        xray_running=health["running"],
        xray_pid=health.get("pid"),
        cpu_percent=cpu,
        memory_percent=mem,
        total_traffic_bytes=int(total_traffic or 0),
        server_time=datetime.now(timezone.utc),
        extra={
            "binary": health.get("binary"),
            "version": health.get("version"),
            "config": health.get("config"),
            "auto_restart": health.get("auto_restart"),
            "last_error": last.get("error"),
            "storage": storage,
        },
    )
