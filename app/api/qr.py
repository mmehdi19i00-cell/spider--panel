"""QR code endpoint for subscription / VLESS config URIs.

Generates a REAL QR code (via `qrcode`) encoding the actual VLESS URI for a
user's config. Returns SVG (scalable, no binary deps). The dashboard and the
public /sub page call these to show scannable codes.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.subscriptions import builder as sub_builder
from app.users.models import AdminUser, User

router = APIRouter(prefix="/api/qr", tags=["qr"])


async def _load_user(db: AsyncSession, uuid: str) -> User:
    res = await db.execute(select(User).where(User.uuid == uuid))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _qr_svg(data: str, box: int = 8, border: int = 2) -> str:
    import qrcode
    from qrcode.image.svg import SvgImage

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=SvgImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


@router.get("/{uuid}")
async def qr_user(
    uuid: str,
    index: int = Query(0, ge=0, description="which config (0=first)"),
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Real QR for a user's Nth VLESS config."""
    user = await _load_user(db, uuid)
    uris = await sub_builder.build_subscription(db, user)
    if not uris:
        raise HTTPException(status_code=404, detail="No configs for this user")
    idx = min(index, len(uris) - 1)
    svg = _qr_svg(uris[idx])
    return HTMLResponse(content=svg, media_type="image/svg+xml")


@router.get("/{uuid}/raw")
async def qr_raw(
    uuid: str,
    index: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Plain-SVG QR for the raw VLESS URI. PUBLIC (the uuid is the secret)."""
    user = await _load_user(db, uuid)
    uris = await sub_builder.build_subscription(db, user)
    if not uris:
        raise HTTPException(status_code=404, detail="No configs for this user")
    idx = min(index, len(uris) - 1)
    svg = _qr_svg(uris[idx])
    return HTMLResponse(content=svg, media_type="image/svg+xml")
