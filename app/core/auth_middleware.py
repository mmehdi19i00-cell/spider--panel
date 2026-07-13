"""Auth middleware: session-based authentication with HttpOnly cookies."""
from __future__ import annotations

from typing import Optional
from datetime import datetime, timezone

from fastapi import Request, Response
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.security import decode_access_token, get_current_admin
from app.database import get_sessionmaker
from app.users.models import AdminUser
from sqlalchemy import select


# Routes that don't require authentication
PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/api/auth/token",
    "/api/auth/login",
    "/api/login",
    "/api/auth/logout",
    "/api/healthz",
    "/sub",
    "/static",
    "/assets",
    "/musics",
    "/favicon.ico",
}


def is_public_path(path: str) -> bool:
    """Check if a path should be accessible without authentication."""
    for public in PUBLIC_PATHS:
        if path.startswith(public):
            return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check authentication via HttpOnly cookie or Bearer token."""

    async def dispatch(self, request: Request, call_next):
        # Skip authentication for public paths
        if is_public_path(request.url.path):
            return await call_next(request)

        # Try to get token from HttpOnly cookie first
        token = request.cookies.get("spider_token")

        # Fallback to Authorization header for API clients
        if not token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:]

        def unauth_response():
            # The embedded "Chrome" tab loads this endpoint inside an <iframe>.
            # A raw JSON 401 there renders as unstyled text, so return a styled
            # HTML sign-in card instead.
            if request.url.path == "/api/browser/proxy":
                return HTMLResponse(
                    """<!doctype html><html data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{font-family:system-ui,Segoe UI,sans-serif;background:#0f0f12;color:#ffe9ee;
       display:grid;place-items:center;height:100vh;margin:0;padding:24px;text-align:center}
  .card{max-width:420px;padding:28px;border:1px solid rgba(255,26,60,.4);border-radius:16px;
        background:rgba(28,4,12,.55);box-shadow:0 0 30px rgba(255,26,60,.25)}
  h2{color:#ff1a3c;letter-spacing:2px;margin:0 0 10px}
  p{color:#c79aa6;line-height:1.6;margin:8px 0 18px}
  a{display:inline-block;padding:10px 18px;border-radius:10px;background:linear-gradient(135deg,#ff1a3c,#b3001b);
    color:#fff;text-decoration:none;font-weight:600;letter-spacing:1px}
</style></head><body><div class="card">
  <h2>SIGN-IN REQUIRED</h2>
  <p>Your session has expired or you are not signed in. The embedded browser needs a valid administrator session to load pages.</p>
  <a href="/login">Go to login</a>
</div></body></html>""",
                    status_code=401,
                )
            # Redirect to login for browser requests
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(url="/login", status_code=302)
            # Return 401 for API requests
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

        if not token:
            return unauth_response()

        # Validate token
        payload = decode_access_token(token)
        if not payload or "sub" not in payload:
            if request.url.path == "/api/browser/proxy":
                return unauth_response()
            # Clear invalid cookie
            response = RedirectResponse(url="/login", status_code=302) if "text/html" in request.headers.get("accept", "") else JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )
            response.delete_cookie(
                key="spider_token",
                httponly=True,
                secure=True,
                samesite="lax",
                path="/"
            )
            return response

        # Attach user info to request state
        request.state.user = payload["sub"]
        request.state.token = token

        return await call_next(request)


def set_auth_cookie(response: Response, token: str, expires_minutes: int = 1440) -> None:
    """Set HttpOnly secure cookie with the auth token."""
    response.set_cookie(
        key="spider_token",
        value=token,
        max_age=expires_minutes * 60,
        httponly=True,
        secure=True,  # Only over HTTPS (Railway provides HTTPS)
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear the auth cookie on logout."""
    response.delete_cookie(
        key="spider_token",
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )