"""Inbounds router: create/edit/delete, regenerate Reality keys, toggle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.inbounds import service as ib_service
from app.schemas import InboundCreate, InboundOut, InboundUpdate
from app.users.models import AdminUser, Inbound

router = APIRouter(prefix="/api/inbounds", tags=["inbounds"])


@router.get("", response_model=list[InboundOut])
async def list_inbounds(db: AsyncSession = Depends(get_db)):
    return await ib_service.list_inbounds(db)


@router.post("", response_model=InboundOut, status_code=status.HTTP_201_CREATED)
async def create_inbound(payload: InboundCreate, db: AsyncSession = Depends(get_db)):
    try:
        ib = await ib_service.create_inbound(
            db,
            tag=payload.tag,
            name=payload.name,
            port=payload.port,
            sec=payload.security,
            network=payload.network,
            server_name=payload.server_name,
            spider_x=payload.spider_x,
            transport_path=payload.transport_path,
            ws_host=payload.ws_host,
            xhttp_mode=payload.xhttp_mode,
            xhttp_x_padding_bytes=payload.xhttp_x_padding_bytes,
            xhttp_sc_max_each_post_bytes=payload.xhttp_sc_max_each_post_bytes,
            xhttp_sc_max_concurrent_posts=payload.xhttp_sc_max_concurrent_posts,
            xhttp_extra=payload.xhttp_extra,
            cert_path=payload.cert_path,
            key_path=payload.key_path,
            alpn=payload.alpn,
            uuid=payload.uuid,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ib


@router.get("/{inbound_id}", response_model=InboundOut)
async def get_inbound(inbound_id: int, db: AsyncSession = Depends(get_db)):
    ib = await ib_service.get_inbound(db, inbound_id)
    if not ib:
        raise HTTPException(404, "Inbound not found")
    return ib


@router.put("/{inbound_id}", response_model=InboundOut)
async def update_inbound(
    inbound_id: int, payload: InboundUpdate, db: AsyncSession = Depends(get_db)
):
    ib = await ib_service.get_inbound(db, inbound_id)
    if not ib:
        raise HTTPException(404, "Inbound not found")
    fields = payload.model_dump(exclude_unset=True)
    return await ib_service.update_inbound(db, ib, **fields)


@router.post("/{inbound_id}/regen-keys", response_model=InboundOut)
async def regen_keys(inbound_id: int, db: AsyncSession = Depends(get_db)):
    ib = await ib_service.get_inbound(db, inbound_id)
    if not ib:
        raise HTTPException(404, "Inbound not found")
    return await ib_service.regenerate_reality_keys(db, ib)


@router.delete("/{inbound_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbound(inbound_id: int, db: AsyncSession = Depends(get_db)):
    ib = await ib_service.get_inbound(db, inbound_id)
    if not ib:
        raise HTTPException(404, "Inbound not found")
    await ib_service.delete_inbound(db, ib)
