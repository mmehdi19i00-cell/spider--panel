"""Server-side session management (cookie holds only the signed session id).

Design
------
* ``SessionMiddleware`` stores a signed ``session_id`` cookie (HttpOnly,
  SameSite=Lax, Secure on HTTPS). The cookie carries NO secrets.
* The actual session row lives in the DB (``Session`` table). It holds the
  ``admin_id`` and a rotating CSRF token.
* Page routes (``/dashboard``, ``/users``, ...) use :func:`require_page_auth`,
  which redirects unauthenticated browsers to ``/login``.
* JSON APIs keep accepting the legacy ``Bearer`` JWT as a fallback so existing
  API clients and the test-suite keep working.

Security properties
--------------------
* Private key / password / session internals are never exposed in responses.
* CSRF is enforced by the double-submit ``X-CSRF-Token`` header (see
  ``app/main.py::CSRFTokenMiddleware``).
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.users.models import AdminUser, Session

COOKIE_NAME = "spider_session"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(hours=12)
CSRF_BYTES = 24  # 48 hex chars


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    db: AsyncSession,
    admin: AdminUser,
    request: Optional[Request] = None,
    ttl: timedelta = SESSION_TTL,
) -> Session:
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_hex(CSRF_BYTES)
    now = _utcnow()
    sess = Session(
        id=sid,
        admin_id=admin.id,
        csrf_token=csrf,
        created_at=now,
        expires_at=now + ttl,
        ip=(request.client.host if request and request.client else "")[:64],
        user_agent=(request.headers.get("user-agent", "") if request else "")[:255],
    )
    db.add(sess)
    await db.commit()
    return sess


async def get_session(db: AsyncSession, session_id: Optional[str]) -> Optional[Session]:
    if not session_id:
        return None
    res = await db.execute(select(Session).where(Session.id == session_id))
    sess = res.scalar_one_or_none()
    if sess is None:
        return None
    if sess.expires_at.replace(tzinfo=timezone.utc) < _utcnow():
        await db.delete(sess)
        await db.commit()
        return None
    # periodic CSRF rotation is handled at login; keep the row.
    return sess


async def delete_session(db: AsyncSession, session_id: Optional[str]) -> None:
    if not session_id:
        return
    await db.execute(delete(Session).where(Session.id == session_id))
    await db.commit()


async def current_admin_from_session(
    request: Request,
    db: AsyncSession,
) -> Optional[AdminUser]:
    """Return the logged-in admin via the session cookie, or None."""
    sid = request.cookies.get(COOKIE_NAME)
    sess = await get_session(db, sid)
    if sess is None:
        return None
    res = await db.execute(select(AdminUser).where(AdminUser.id == sess.admin_id))
    admin = res.scalar_one_or_none()
    if admin is None or not admin.is_active:
        return None
    return admin


async def require_page_auth(request: Request, db: AsyncSession) -> Optional[RedirectResponse]:
    """For server-rendered pages: redirect to /login if not authenticated.

    Returns a ``RedirectResponse`` (to /login) when unauthenticated, else None.
    """
    admin = await current_admin_from_session(request, db)
    if admin is None:
        return RedirectResponse("/login", status_code=302)
    return None


def set_session_cookie(response, session_id: str, request: Request) -> None:
    secure = bool(request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
