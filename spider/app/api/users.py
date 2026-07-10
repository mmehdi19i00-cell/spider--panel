"""Users router: CRUD + reset uuid, extend, traffic/ip limit, enable/disable."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import get_current_admin
from app.schemas import UserCreate, UserOut, UserUpdate
from app.users import service
from app.users.models import AdminUser, User

router = APIRouter(prefix="/api/users", tags=["users"])
_protected = [Depends(get_current_admin)]


@router.get("", response_model=list[UserOut])
async def list_users(search: str | None = None, db: AsyncSession = Depends(get_db)):
    return await service.list_users(db, search=search)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        u = await service.create_user(
            db,
            username=payload.username,
            expire_days=payload.expire_days,
            traffic_limit_gb=payload.traffic_limit_gb,
            ip_limit=payload.ip_limit,
            enabled_inbounds=payload.enabled_inbounds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return u


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return u


@router.put("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, payload: UserUpdate, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    fields = payload.model_dump(exclude_unset=True)
    # expire_days -> extend expiry
    if "expire_days" in fields:
        days = fields.pop("expire_days")
        u = await service.extend_expiry(db, u, days)
    try:
        u = await service.update_user(db, u, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return u


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    await service.delete_user(db, u)


@router.post("/{user_id}/reset-uuid", response_model=UserOut)
async def reset_uuid(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return await service.reset_uuid(db, u)


@router.post("/{user_id}/extend", response_model=UserOut)
async def extend(user_id: int, days: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return await service.extend_expiry(db, u, days)


@router.post("/{user_id}/enable", response_model=UserOut)
async def enable_user(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return await service.set_enabled(db, u, True)


@router.post("/{user_id}/disable", response_model=UserOut)
async def disable_user(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return await service.set_enabled(db, u, False)


@router.get("/{user_id}/sessions")
async def user_sessions(user_id: int, db: AsyncSession = Depends(get_db)):
    u = await service.get_user(db, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    sessions = await service.list_sessions(db, user_id)
    return [
        {
            "id": s.id,
            "ip": s.ip,
            "connected_at": s.connected_at,
            "last_seen": s.last_seen,
        }
        for s in sessions
    ]
