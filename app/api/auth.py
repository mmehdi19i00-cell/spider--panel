"""Auth router: login (session), logout, me, change username/password."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
from app.core.auth_middleware import set_auth_cookie, clear_auth_cookie

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """OAuth2 compatible login, returns access token and sets session cookie."""
    return await _do_login(request, response, form_data.username, form_data.password, db)


@router.post("/login", response_model=TokenResponse)
async def login_json(
    request: Request,
    response: Response,
    payload: TokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """JSON login, returns access token and sets session cookie."""
    return await _do_login(request, response, payload.username, payload.password, db)


async def _do_login(request: Request, response: Response, username: str, password: str, db: AsyncSession) -> TokenResponse:
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

    # Only mark the cookie Secure when the connection is actually HTTPS.
    # A Secure cookie is dropped over plain HTTP, which would break the
    # session (and the WebSocket log stream) on HTTP deployments.
    secure = str(request.url.scheme).lower() == "https"

    # Set HttpOnly cookie for session
    set_auth_cookie(
        response,
        token,
        expires_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        secure=secure,
    )

    return TokenResponse(access_token=token, username=admin.username)


@router.post("/logout")
async def logout(response: Response):
    """Logout - clear session cookie."""
    clear_auth_cookie(response)
    return {"ok": True, "message": "Logged out"}


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