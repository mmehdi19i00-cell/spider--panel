"""
XHTTP Router - FastAPI routes for XHTTP transport.
Exposes router = APIRouter()
Never imports FastAPI app.
"""
from fastapi import APIRouter, Request, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from config import logger, IRAN_TZ, get_host
from state import (
    LINKS, LINKS_LOCK, stats, hourly_traffic, connections, error_logs,
    logger as state_logger, is_link_allowed, save_state,
    PATH_INDEX, PATH_INDEX_LOCK,
)
from services.xray_service import parse_vless_header, check_and_use  # Will need to move these

# ── Router ────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/xhttp", tags=["xhttp"])

# Import the main functions we need - these will be moved to proper services
# For now, keep the logic here but use state imports


# Helper functions needed
async def _get_link_by_path(path: str):
    """Get link UUID from path."""
    async with PATH_INDEX_LOCK:
        return PATH_INDEX.get(path)


@router.post("/{path}")
async def xhttp_post(path: str, request: Request):
    """Handle XHTTP POST requests."""
    # Strip leading slash
    clean_path = path.lstrip("/")
    link_uuid = await _get_link_by_path(clean_path)
    if not link_uuid:
        raise HTTPException(status_code=404, detail="Path not found")
    
    link = LINKS.get(link_uuid)
    if not link or not is_link_allowed(link):
        raise HTTPException(status_code=403, detail="Link not allowed")
    
    # Process the request through VLESS relay
    # This would connect to the actual VLESS relay handler
    # For now, return a placeholder
    return {"status": "ok", "uuid": link_uuid}


@router.get("/{path}")
async def xhttp_get(path: str, request: Request):
    """Handle XHTTP GET requests (stream-up)."""
    clean_path = path.lstrip("/")
    link_uuid = await _get_link_by_path(clean_path)
    if not link_uuid:
        raise HTTPException(status_code=404, detail="Path not found")
    
    link = LINKS.get(link_uuid)
    if not link or not is_link_allowed(link):
        raise HTTPException(status_code=403, detail="Link not allowed")
    
    # Handle streaming
    return StreamingResponse(
        _xhttp_stream(link_uuid, request),
        media_type="application/octet-stream",
    )


async def _xhttp_stream(link_uuid: str, request: Request):
    """Stream XHTTP data."""
    # Placeholder for actual streaming logic
    yield b""


# ── WebSocket handler for XHTTP ───────────────────────────────────────────
@router.websocket("/ws/{path}")
async def xhttp_websocket(websocket: WebSocket, path: str):
    """WebSocket endpoint for XHTTP."""
    await websocket.accept()
    clean_path = path.lstrip("/")
    link_uuid = await _get_link_by_path(clean_path)
    
    if not link_uuid:
        await websocket.close(code=4004, reason="Path not found")
        return
    
    link = LINKS.get(link_uuid)
    if not link or not is_link_allowed(link):
        await websocket.close(code=4003, reason="Link not allowed")
        return
    
    # Handle websocket connection
    try:
        while True:
            data = await websocket.receive_bytes()
            # Process data
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()