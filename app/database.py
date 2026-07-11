"""Async SQLAlchemy engine + session management.

Supports SQLite (dev) and PostgreSQL (prod) via the DATABASE_URL setting.
Connection pooling for Postgres, WAL/check_same_thread disabled for SQLite.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging import log


class Base(DeclarativeBase):
    pass


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine():
    url = settings.db_url
    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
    # PostgreSQL (production) — sane pool + timeouts
    return create_async_engine(
        url,
        echo=False,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def get_engine():
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_sessionmaker()() as session:
        yield session


async def init_db() -> None:
    """Create tables (idempotent). For prod, Alembic migrations are preferred
    but create_all keeps dev + first-boot simple.

    After creating tables we run a small set of ALTERs for columns added after
    the original schema (e.g. inbounds.domain). These are no-ops if the column
    already exists, so existing Railway/postgres databases are upgraded in place
    without data loss.
    """
    from app.users import models  # noqa: F401  (register all mappers)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # --- forward migrations (safe ALTERs) ---
    await _migrate_add_column(engine, "inbounds", "domain", "VARCHAR(255)", default="''")


async def _migrate_add_column(engine, table: str, column: str, coltype: str, default: str = "''") -> None:
    """Add a column if it does not exist (works on SQLite + Postgres)."""
    try:
        if engine.dialect.name == "sqlite":
            async with engine.begin() as conn:
                # sqlite: inspect columns
                from sqlalchemy import text

                res = await conn.execute(text(f"PRAGMA table_info({table})"))
                cols = {row[1] for row in res.fetchall()}
                if column not in cols:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype} DEFAULT {default}"))
        else:
            async with engine.begin() as conn:
                from sqlalchemy import text

                await conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {coltype} "
                        f"DEFAULT {default}"
                    )
                )
    except Exception as e:  # pragma: no cover - migration best-effort
        log.warning(f"migration skip {table}.{column}: {e}")


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
