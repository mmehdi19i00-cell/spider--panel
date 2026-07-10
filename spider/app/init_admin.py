"""Standalone CLI: create/update the admin account and seed defaults.

Usage:
    python -m app.init_admin --username admin --password 'secret' --email a@b.c
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# ensure repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _run(username: str, password: str, email: str) -> None:
    from app.core.config import settings
    from app.core.security import hash_password
    from app.database import get_sessionmaker, init_db
    from app.users.models import AdminUser
    from sqlalchemy import select

    await init_db()
    async with get_sessionmaker()() as db:
        res = await db.execute(select(AdminUser).where(AdminUser.username == username))
        admin = res.scalar_one_or_none()
        if admin is None:
            admin = AdminUser(username=username, email=email, is_active=True)
            db.add(admin)
            print(f"Created admin '{username}'")
        else:
            print(f"Admin '{username}' exists — updating password/email")
        admin.password_hash = hash_password(password)
        if email:
            admin.email = email
        await db.commit()
    print("Done.")


def main() -> None:
    p = argparse.ArgumentParser(description="Spider Panel admin setup")
    p.add_argument("--username", default=os.getenv("ADMIN_USERNAME", "admin"))
    p.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", ""))
    p.add_argument("--email", default=os.getenv("ADMIN_EMAIL", ""))
    args = p.parse_args()
    if not args.password:
        p.error("password is required (--password or ADMIN_PASSWORD env)")
    asyncio.run(_run(args.username, args.password, args.email))


if __name__ == "__main__":
    main()
