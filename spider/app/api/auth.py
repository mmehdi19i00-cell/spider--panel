"""Auth router: login (token), change username/password, logout, me."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_current_admin,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.schemas import ChangeCredentials, TokenRequest, TokenResponse
from app.users.models import AdminUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    return await _do_login(form_data.username, form_data.password, db)


@router.post("/login", response_model=TokenResponse)
async def login_json(payload: TokenRequest, db: AsyncSession = Depends(get_db)):
    return await _do_login(payload.username, payload.password, db)


async def _do_login(username: str, password: str, db: AsyncSession) -> TokenResponse:
    res = await db.execute(select(AdminUser).where(AdminUser.username == username))
    admin = res.scalar_one_or_none()
    if not admin or not verify_password(password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    admin.last_login = datetime.now(timezone.utc)
    await db.commit()
    token = create_access_token(admin.username, extra={"role": "admin"})
    return TokenResponse(access_token=token, username=admin.username)


@router.get("/me")
async def me(admin: AdminUser = Depends(get_current_admin)):
    return {"username": admin.username, "email": admin.email, "active": admin.is_active}


@router.post("/change-credentials")
async def change_credentials(
    payload: ChangeCredentials,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    changed: list[str] = []
    if payload.new_username and payload.new_username != admin.username:
        res = await db.execute(
            select(AdminUser).where(AdminUser.username == payload.new_username)
        )
        if res.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Username already taken")
        admin.username = payload.new_username
        changed.append("username")
    if payload.new_password:
        if len(payload.new_password) < 6:
            raise HTTPException(status_code=400, detail="Password too short (min 6)")
        admin.password_hash = hash_password(payload.new_password)
        changed.append("password")
    await db.commit()
    return {"ok": True, "changed": changed}
