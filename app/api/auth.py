"""Auth router: cookie-session login, change credentials, logout, me.

Browser pages use an HttpOnly session cookie (set here on login, cleared on
logout). JSON APIs keep accepting the legacy ``Bearer`` JWT as a fallback so
existing API clients and the test-suite keep working.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
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
from app.core import session as session_mod
from app.database import get_db
from app.schemas import ChangeCredentials, TokenRequest, TokenResponse
from app.users.models import AdminUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _do_login(username: str, password: str, db: AsyncSession, request: Request) -> Response:
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

    sess = await session_mod.create_session(db, admin, request=request)
    token = create_access_token(admin.username, extra={"role": "admin"})

    from fastapi.responses import JSONResponse

    resp = JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "username": admin.username,
            "csrf_token": sess.csrf_token,
        }
    )
    # Set the secure, HttpOnly, SameSite=Lax session cookie.
    session_mod.set_session_cookie(resp, sess.id, request)
    return resp


@router.post("/token", response_model=TokenResponse)
async def login_form(
    form_data: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    return await _do_login(form_data.username, form_data.password, db, request)


@router.post("/login", response_model=TokenResponse)
async def login_json(
    payload: TokenRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    return await _do_login(payload.username, payload.password, db, request)


@router.post("/logout")
async def logout(
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    sid = request.cookies.get(session_mod.COOKIE_NAME) if request else None
    await session_mod.delete_session(db, sid)
    resp = JSONResponse({"ok": True})
    session_mod.clear_session_cookie(resp)
    return resp


@router.get("/me")
async def me(admin: AdminUser = Depends(get_current_admin)):
    return {"username": admin.username, "email": admin.email, "active": admin.is_active}


@router.post("/change-credentials")
async def change_credentials(
    payload: ChangeCredentials,
    request: Request = None,
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
    # Rotate session + CSRF so the old token can't be reused after a cred change.
    if request is not None:
        sid = request.cookies.get(session_mod.COOKIE_NAME)
        await session_mod.delete_session(db, sid)
        new_sess = await session_mod.create_session(db, admin, request=request)
        session_mod.set_session_cookie(Response(), new_sess.id, request)
    return {"ok": True, "changed": changed}
