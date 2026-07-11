"""Server-rendered page routes (multi-page architecture, mobile-first).

Each protected page guards with :func:`require_page_auth` and redirects
unauthenticated browsers to ``/login``. Every page is its own template file
under ``app/templates/``.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import session as session_mod
from app.database import get_db

router = APIRouter(tags=["pages"])

_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")


def _tpl(name: str) -> str:
    return os.path.join(_TEMPLATES, name)


async def _protected(request: Request, db: AsyncSession) -> RedirectResponse | None:
    return await session_mod.require_page_auth(request, db)


async def _serve(name: str, request: Request, db: AsyncSession):
    redirect = await _protected(request, db)
    if redirect is not None:
        return redirect
    return FileResponse(_tpl(name))


@router.get("/")
async def root(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await session_mod.current_admin_from_session(request, db)
    if admin is None:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@router.get("/login")
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    # If already logged in, go straight to dashboard.
    admin = await session_mod.current_admin_from_session(request, db)
    if admin is not None:
        return RedirectResponse("/dashboard", status_code=302)
    return FileResponse(_tpl("login.html"))


@router.get("/dashboard")
async def dashboard_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("dashboard.html", request, db)


@router.get("/inbounds")
async def inbounds_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("inbounds.html", request, db)


@router.get("/users")
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("users.html", request, db)


@router.get("/domains")
async def domains_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("domains.html", request, db)


@router.get("/system")
async def system_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("system.html", request, db)


@router.get("/settings")
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("settings.html", request, db)


@router.get("/news")
async def news_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("news.html", request, db)


@router.get("/xray")
async def xray_page(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("xray.html", request, db)


@router.get("/sub")
async def sub_landing(request: Request, db: AsyncSession = Depends(get_db)):
    return await _serve("sub.html", request, db)


@router.get("/logout")
async def logout_page(request: Request, db: AsyncSession = Depends(get_db)):
    sid = request.cookies.get(session_mod.COOKIE_NAME)
    await session_mod.delete_session(db, sid)
    resp = RedirectResponse("/login", status_code=302)
    session_mod.clear_session_cookie(resp)
    return resp
