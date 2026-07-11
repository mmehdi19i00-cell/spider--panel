"""Xray observability endpoints (logs, validate, download config).

- GET  /api/xray/logs            -> recent xray stdout/stderr (real capture)
- WS   /api/xray/logs/stream     -> live tail of xray output (auth required)
- POST /api/xray/validate        -> run `xray run -test` and return the result
- GET  /api/xray/config          -> download the generated config.json
- GET  /api/xray/last-result     -> last start/validate diagnostics
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import settings
from app.core.security import get_current_admin
from app.users.models import AdminUser
from app.xray.process import manager

router = APIRouter(prefix="/api/xray", tags=["xray"])


# ---------------------------------------------------------------------------
# Logs (HTTP)
# ---------------------------------------------------------------------------
@router.get("/logs")
async def xray_logs(_: AdminUser = Depends(get_current_admin), limit: int = 300):
    return {"lines": manager.get_logs(limit=limit)}


@router.get("/last-result")
async def xray_last(_: AdminUser = Depends(get_current_admin)):
    return manager.last_result()


@router.post("/validate")
async def xray_validate(_: AdminUser = Depends(get_current_admin)):
    """Validate the on-disk config with `xray run -test`. Returns full output."""
    result = manager.validate_config_file()
    return result


@router.get("/config")
async def xray_config(_: AdminUser = Depends(get_current_admin)):
    path = settings.xray_config_path
    if not os.path.exists(path):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="config.json not generated yet")
    return FileResponse(path, filename="config.json", media_type="application/json")


# ---------------------------------------------------------------------------
# Live log stream (WebSocket, auth via ?token=)
# ---------------------------------------------------------------------------
@router.websocket("/logs/stream")
async def xray_logs_stream(ws: WebSocket):
    # Auth: token query param (dashboard passes the same JWT)
    token = ws.query_params.get("token") or ""
    from app.core.security import decode_access_token

    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        await ws.close(code=4401, reason="unauthorized")
        return
    await ws.accept()
    # send the recent backlog first
    for line in manager.get_logs(limit=200):
        try:
            await ws.send_text(line)
        except Exception:
            return
    # then tail new lines as the manager appends
    last_seen = len(manager.get_logs(limit=100000))
    try:
        while True:
            logs = manager.get_logs(limit=100000)
            if len(logs) > last_seen:
                for line in logs[last_seen:]:
                    await ws.send_text(line)
                last_seen = len(logs)
            await ws.receive_text()  # heartbeat / allow client to close
    except WebSocketDisconnect:
        return
    except Exception:
        return
