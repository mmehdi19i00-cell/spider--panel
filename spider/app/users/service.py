"""User service — all user-related business logic.

Responsibilities:
  * create / edit / delete users
  * reset uuid, extend expiry, change traffic/ip limits
  * enable / disable
  * deactivate expired users (status transition)
  * IP-limit enforcement via the user_sessions table
  * traffic accounting + auto-disable on limit
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.users.models import User, UserSession


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def create_user(
    db: AsyncSession,
    *,
    username: str,
    expire_days: int | None = None,
    traffic_limit_gb: float = 0,
    ip_limit: int = 0,
    enabled_inbounds: str = "",
) -> User:
    # uniqueness
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        raise ValueError(f"username '{username}' already exists")

    expire_at = None
    if expire_days is not None:
        expire_at = datetime.now(timezone.utc) + timedelta(days=expire_days)

    user = User(
        username=username,
        uuid=security.generate_uuid(),
        status="active",
        enabled=True,
        expire_at=expire_at,
        traffic_limit_bytes=int(traffic_limit_gb * 1024**3),
        ip_limit=ip_limit,
        enabled_inbounds=enabled_inbounds,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def list_users(db: AsyncSession, search: str | None = None) -> list[User]:
    q = select(User)
    if search:
        q = q.where(User.username.ilike(f"%{search}%"))
    q = q.order_by(User.created_at.desc())
    res = await db.execute(q)
    return list(res.scalars().all())


async def update_user(db: AsyncSession, user: User, **fields: Any) -> User:
    allowed = {
        "username", "status", "enabled", "expire_at",
        "traffic_limit_bytes", "ip_limit", "enabled_inbounds",
    }
    for k, v in fields.items():
        if k in allowed and v is not None:
            setattr(user, k, v)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user: User) -> None:
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.delete(user)
    await db.commit()


async def reset_uuid(db: AsyncSession, user: User) -> User:
    user.uuid = security.generate_uuid()
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


async def extend_expiry(db: AsyncSession, user: User, days: int) -> User:
    base = user.expire_at
    # normalize naive (SQLite) to UTC-aware for comparison
    if base is not None and base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if base is None or base < now:
        base = now
    user.expire_at = base + timedelta(days=days)
    user.status = "active"
    user.updated_at = now
    await db.commit()
    await db.refresh(user)
    return user


async def set_enabled(db: AsyncSession, user: User, enabled: bool) -> User:
    user.enabled = enabled
    if not enabled:
        user.status = "disabled"
    elif user.is_expired:
        user.status = "expired"
    else:
        user.status = "active"
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Expiry / traffic maintenance
# ---------------------------------------------------------------------------
async def deactivate_expired(db: AsyncSession) -> int:
    """Flip expired-but-active users to status='expired'. Returns count."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        select(User).where(
            User.enabled.is_(True),
            User.status != "expired",
            User.expire_at.is_not(None),
            User.expire_at <= now,
        )
    )
    users = res.scalars().all()
    for u in users:
        u.status = "expired"
    if users:
        await db.commit()
    return len(users)


async def enforce_traffic_limit(db: AsyncSession) -> int:
    """Disable users who blew past their traffic cap (limit>0)."""
    res = await db.execute(
        select(User).where(
            User.traffic_limit_bytes > 0,
            User.used_traffic_bytes >= User.traffic_limit_bytes,
            User.status != "expired",
            User.enabled.is_(True),
        )
    )
    users = res.scalars().all()
    for u in users:
        u.enabled = False
        u.status = "disabled"
    if users:
        await db.commit()
    return len(users)


# ---------------------------------------------------------------------------
# IP limit
# ---------------------------------------------------------------------------
async def count_active_sessions(db: AsyncSession, user_id: int) -> int:
    res = await db.execute(
        select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)
    )
    return int(res.scalar() or 0)


async def register_session(
    db: AsyncSession, user: User, ip: str, idle_timeout_minutes: int = 60
) -> tuple[bool, str]:
    """Attempt to register a connection. Returns (allowed, reason).

    Enforces ip_limit: number of *distinct active sessions* may not exceed
    ip_limit (0 = unlimited). We reuse an existing session row for the same
    (user, ip) to avoid counting reconnects as new devices.
    """
    now = datetime.now(timezone.utc)
    # prune stale sessions
    await db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.last_seen < now - timedelta(minutes=idle_timeout_minutes),
        )
    )

    sess: UserSession | None = None
    existing = await db.execute(
        select(UserSession).where(
            UserSession.user_id == user.id, UserSession.ip == ip
        )
    )
    sess = existing.scalar_one_or_none()
    if user.ip_limit and user.ip_limit > 0:
        active = await count_active_sessions(db, user.id)
        if sess is None and active >= user.ip_limit:
            return False, "ip_limit_reached"

    if sess is None:
        sess = UserSession(user_id=user.id, uuid=user.uuid, ip=ip)
        db.add(sess)
    sess.last_seen = now
    sess.connected_at = sess.connected_at or now
    await db.commit()
    return True, "ok"


async def touch_session(db: AsyncSession, user: User, ip: str) -> None:
    res = await db.execute(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.ip == ip)
    )
    s = res.scalar_one_or_none()
    if s:
        s.last_seen = datetime.now(timezone.utc)
        await db.commit()


async def list_sessions(db: AsyncSession, user_id: int) -> list[UserSession]:
    res = await db.execute(
        select(UserSession).where(UserSession.user_id == user_id).order_by(
            UserSession.last_seen.desc()
        )
    )
    return list(res.scalars().all())


# ---------------------------------------------------------------------------
# Stats (dashboard)
# ---------------------------------------------------------------------------
async def user_stats(db: AsyncSession) -> dict[str, int]:
    total = await db.scalar(select(func.count()).select_from(User))
    active = await db.scalar(
        select(func.count()).select_from(User).where(User.status == "active", User.enabled.is_(True))
    )
    expired = await db.scalar(
        select(func.count()).select_from(User).where(User.status == "expired")
    )
    disabled = await db.scalar(
        select(func.count()).select_from(User).where(User.enabled.is_(False))
    )
    return {
        "total": int(total or 0),
        "active": int(active or 0),
        "expired": int(expired or 0),
        "disabled": int(disabled or 0),
    }


def human_bytes(n: int) -> str:
    val: float = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024:
            return f"{val:.2f} {unit}"
        val /= 1024.0
    return f"{val:.2f} PB"
