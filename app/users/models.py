"""SQLAlchemy models — the single source of truth.

Everything the Xray config builder and subscription builder read comes from
these tables. Nothing hardcodes pbk/sid/sni/transport/security/uuid/path.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Admin (panel login)
# ---------------------------------------------------------------------------
class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AdminUser {self.username}>"


# ---------------------------------------------------------------------------
# Inbounds — one row per enabled listening inbound (Reality / TLS / ...)
# ---------------------------------------------------------------------------
class Inbound(Base):
    __tablename__ = "inbounds"
    __table_args__ = (UniqueConstraint("tag", name="uq_inbound_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(String(64), unique=True)  # e.g. "vless-reality"
    name: Mapped[str] = mapped_column(String(128), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="vless")  # vless
    # Domain assigned to THIS inbound. Subscription URIs and the server SNI use
    # this domain (so each inbound can serve a different domain). Empty -> use
    # the active domain (global fallback).
    domain: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[int] = mapped_column(Integer)  # internal listen port (server binds this)
    # External/reverse-proxy port the *client* should connect to. When set, the
    # server keeps listening on `port` but subscription URIs use `external_port`.
    # This supports e.g. Reality behind a VPS TCP proxy / NAT where the public
    # port differs from the container's internal listening port.
    external_port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    security: Mapped[str] = mapped_column(String(32), default="reality")  # reality | tls | none
    network: Mapped[str] = mapped_column(String(32), default="xhttp")  # xhttp | ws | tcp

    # Reality fields
    uuid: Mapped[str] = mapped_column(String(64))  # master client uuid for the inbound
    private_key: Mapped[str] = mapped_column(String(64), default="")
    public_key: Mapped[str] = mapped_column(String(64), default="")
    short_id: Mapped[str] = mapped_column(String(32), default="")
    server_name: Mapped[str] = mapped_column(String(255), default="")  # reality serverName (target)
    spider_x: Mapped[str] = mapped_column(String(255), default="/")

    # TLS fields (when security=tls)
    cert_path: Mapped[str] = mapped_column(String(512), default="")
    key_path: Mapped[str] = mapped_column(String(512), default="")
    alpn: Mapped[str] = mapped_column(String(64), default="h2,http/1.1")

    # Transport (xhttp / ws) shared + specific
    transport_path: Mapped[str] = mapped_column(String(255), default="/")
    ws_host: Mapped[str] = mapped_column(String(255), default="")
    xhttp_mode: Mapped[str] = mapped_column(String(32), default="auto")  # auto|packet-up|packet-down|stream-up|stream-down
    xhttp_x_padding_bytes: Mapped[str] = mapped_column(String(32), default="100-1000")
    xhttp_sc_max_each_post_bytes: Mapped[str] = mapped_column(String(32), default="1000000-2000000")
    xhttp_sc_max_concurrent_posts: Mapped[int] = mapped_column(Integer, default=100)
    xhttp_extra: Mapped[str] = mapped_column(Text, default="")  # extra JSON for xhttp

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # relationships filled by builder from users table
    def __repr__(self) -> str:  # pragma: no cover
        return f"<Inbound {self.tag} {self.security}/{self.network}>"


# ---------------------------------------------------------------------------
# Proxy users (clients)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|disabled|expired
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # 0 = unlimited
    used_traffic_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    ip_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    # comma-separated list of inbound tags this user is allowed on
    enabled_inbounds: Mapped[str] = mapped_column(Text, default="")

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_expired(self) -> bool:
        if not self.expire_at:
            return False
        exp = self.expire_at
        # SQLite returns naive datetimes; normalize to UTC-aware for comparison.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc)

    @property
    def is_active(self) -> bool:
        return self.enabled and not self.is_expired and self.status != "expired"

    @property
    def inbound_tags(self) -> list[str]:
        return [t for t in (self.enabled_inbounds or "").split(",") if t]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} {self.status}>"


# ---------------------------------------------------------------------------
# Active connection sessions (for IP limit)
# ---------------------------------------------------------------------------
class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    uuid: Mapped[str] = mapped_column(String(64), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="sessions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Session {self.uuid}@{self.ip}>"


# ---------------------------------------------------------------------------
# Domains — multiple, one active. Active domain drives Reality SNI / TLS / links.
# ---------------------------------------------------------------------------
class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Domain {self.domain} active={self.is_active}>"


# ---------------------------------------------------------------------------
# Key/value settings (panel-level toggles)
# ---------------------------------------------------------------------------
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Setting {self.key}>"
