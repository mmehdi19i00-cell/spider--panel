"""Remote control + clipboard backend.

This module defines the *contract* a real remote-control agent must satisfy
(move/click/scroll/clipboard) and ships a LOOPBACK demo driver so the UI is
fully functional in the server-rendered FastAPI build with no Electron/remote
agent present.

Production wiring
-----------------
The real implementation lives outside this repo: an Electron `BrowserView`
(for the embedded browser) and a host-side input agent (pyautogui / robotjs /
osascript / xdotool) that receives these intents over a secure channel. To
enable it, set ``REMOTE_DRIVER=agent`` and implement ``RemoteDriver`` against
the remote host. The rest of the app talks only to ``get_driver()`` and the
JSON API below, so swapping drivers is a one-line config change.
"""
from __future__ import annotations

import os
import time
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.security import get_current_admin
from app.users.models import AdminUser


# ---------------------------------------------------------------------------
# Driver contract
# ---------------------------------------------------------------------------
class RemoteDriver(Protocol):
    """What any remote-control backend must implement."""

    mode: str  # "loopback" | "agent"

    def mouse_move(self, x: int, y: int, smooth: bool) -> None: ...
    def mouse_click(self, button: str, double: bool = False) -> None: ...
    def mouse_scroll(self, dx: int, dy: int) -> None: ...
    def drag(self, x: int, y: int, start: bool) -> None: ...
    def clipboard_get(self) -> str: ...
    def clipboard_set(self, text: str) -> None: ...


class LoopbackDriver:
    """Demo driver: records intents, does not touch a real cursor.

    In the embedded-browser (iframe) build this is the only honest option —
    the parent page cannot move the OS cursor. It is still useful: the UI
    proves out end-to-end, and an agent driver can be dropped in later.
    """

    mode = "loopback"

    def __init__(self) -> None:
        self.last: dict[str, Any] = {}
        self.clipboard = ""

    def mouse_move(self, x: int, y: int, smooth: bool) -> None:
        self.last = {"op": "move", "x": x, "y": y, "smooth": smooth, "t": time.time()}

    def mouse_click(self, button: str, double: bool = False) -> None:
        self.last = {"op": "click", "button": button, "double": double, "t": time.time()}

    def mouse_scroll(self, dx: int, dy: int) -> None:
        self.last = {"op": "scroll", "dx": dx, "dy": dy, "t": time.time()}

    def drag(self, x: int, y: int, start: bool) -> None:
        self.last = {"op": "drag", "start": start, "x": x, "y": y, "t": time.time()}

    def clipboard_get(self) -> str:
        return self.clipboard

    def clipboard_set(self, text: str) -> None:
        self.clipboard = text


_driver: RemoteDriver | None = None


def get_driver() -> RemoteDriver:
    """Return the active driver (singleton). Swap on REMOTE_DRIVER env."""
    global _driver
    if _driver is None:
        # Only "loopback" is supported in this repo. "agent" requires an
        # external host agent and is intentionally not importable here.
        _driver = LoopbackDriver()
    return _driver


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MoveIn(BaseModel):
    x: int
    y: int
    smooth: bool = True


class ClickIn(BaseModel):
    button: str = "left"  # left | right | middle
    double: bool = False


class ScrollIn(BaseModel):
    dx: int = 0
    dy: int = 0


class DragIn(BaseModel):
    x: int
    y: int
    start: bool = True


class ClipboardIn(BaseModel):
    text: str


class RemoteStatus(BaseModel):
    mode: str
    note: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/remote", tags=["remote"])

_STATUS_NOTE = (
    "Loopback demo driver active. Real cursor/OS control requires the Electron "
    "host agent (set REMOTE_DRIVER=agent and implement RemoteDriver)."
)


@router.get("/status", response_model=RemoteStatus)
async def status(_: AdminUser = Depends(get_current_admin)):
    return RemoteStatus(mode=get_driver().mode, note=_STATUS_NOTE)


@router.post("/mouse/move")
async def mouse_move(body: MoveIn, _: AdminUser = Depends(get_current_admin)):
    get_driver().mouse_move(body.x, body.y, body.smooth)
    return {"ok": True}


@router.post("/mouse/click")
async def mouse_click(body: ClickIn, _: AdminUser = Depends(get_current_admin)):
    get_driver().mouse_click(body.button, body.double)
    return {"ok": True}


@router.post("/mouse/scroll")
async def mouse_scroll(body: ScrollIn, _: AdminUser = Depends(get_current_admin)):
    get_driver().mouse_scroll(body.dx, body.dy)
    return {"ok": True}


@router.post("/mouse/drag")
async def mouse_drag(body: DragIn, _: AdminUser = Depends(get_current_admin)):
    get_driver().drag(body.x, body.y, body.start)
    return {"ok": True}


@router.get("/clipboard")
async def clipboard_get(_: AdminUser = Depends(get_current_admin)):
    # Server-side mirror (loopback). The browser UI prefers navigator.clipboard
    # for the *local* clipboard; this endpoint is the bridge for a remote agent.
    return {"text": get_driver().clipboard_get()}


@router.post("/clipboard")
async def clipboard_set(body: ClipboardIn, _: AdminUser = Depends(get_current_admin)):
    get_driver().clipboard_set(body.text)
    return {"ok": True}
