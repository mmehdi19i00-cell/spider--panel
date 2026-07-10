"""Subscription endpoints.

GET /sub/{uuid}            -> newline-separated vless URIs (base64 metadata link)
GET /sub/{uuid}?format=json -> JSON with uris + user info

Every URI is validated before being returned; an invalid URI is never served.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.subscriptions import builder as sub_builder
from app.subscriptions.validator import assert_valid_subscription
from app.users.models import User

router = APIRouter(tags=["subscription"])


def _to_base64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@router.get("/sub/{uuid}")
async def subscription(
    uuid: str,
    format: str = Query("text", description="text|json"),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(User).where(User.uuid == uuid))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Subscription not found")

    uris = await sub_builder.build_subscription(db, user)
    # NEVER return broken configs.
    try:
        assert_valid_subscription(uris)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=f"Subscription validation failed: {e}")

    if format == "json":
        return {
            "username": user.username,
            "uuid": user.uuid,
            "status": user.status,
            "expire_at": user.expire_at,
            "traffic_limit_bytes": user.traffic_limit_bytes,
            "used_traffic_bytes": user.used_traffic_bytes,
            "uris": uris,
        }

    # text format: subscription-info header compatible
    body = "\n".join(uris) + ("\n" if uris else "")
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Subscription-Userinfo": (
            f"upload=0;download={user.used_traffic_bytes};"
            f"total={user.traffic_limit_bytes};expire="
            f"{int(user.expire_at.timestamp()) if user.expire_at else 0}"
        ),
        "Profile-Title": quote(f"Spider Panel | {user.username}"),
    }
    return PlainTextResponse(content=body, headers=headers)
